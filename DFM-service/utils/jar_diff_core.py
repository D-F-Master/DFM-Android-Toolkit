# -*- coding: utf-8 -*-
"""JAR 包解压对比核心模块。

职责（只做对比，不含界面）：
- 安全解压两个 jar/war/zip 到临时目录（防 zip-slip、处理 GBK 中文文件名乱码）
- 按条目对比：新增 / 删除 / 修改 / 相同（CRC 相同视为内容一致）
- .class 文件优先用 CFR 反编译为 Java 源码（不可用时回退 javap 反汇编）；文本文件按行对比；其余按二进制标记
"""

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

# 视为文本的扩展名（小写，含点）
TEXT_EXTS = {
    '.java', '.properties', '.xml', '.txt', '.mf', '.yml', '.yaml', '.json',
    '.sql', '.html', '.htm', '.js', '.css', '.xsd', '.tld', '.md', '.cfg',
    '.conf', '.ini', '.csv', '.sh', '.bat', '.cmd', '.policy', '.sf',
    '.factories', '.imports', '.handlers', '.gradle', '.jsp', '.tag',
    '.vm', '.ftl', '.tpl', '.bpmn', '.wsdl',
}

STATUS_SAME = 'same'
STATUS_CHANGED = 'changed'
STATUS_ADDED = 'added'      # 只存在于新版本（右侧）
STATUS_REMOVED = 'removed'  # 只存在于旧版本（左侧）

KIND_CLASS = 'class'
KIND_TEXT = 'text'
KIND_BINARY = 'binary'

STATUS_LABELS = {
    STATUS_SAME: '相同',
    STATUS_CHANGED: '修改',
    STATUS_ADDED: '新增',
    STATUS_REMOVED: '删除',
}

JAVAP_TIMEOUT = 30      # 单个 class 反编译超时（秒）
CFR_TIMEOUT = 30        # 单个 class CFR 反编译超时（秒）
CFR_BATCH_SIZE = 50     # 每批反编译的 class 数，避免命令行过长
CFR_BATCH_TIMEOUT = 120 # 批量 CFR 反编译超时（秒）

# Windows 下 GUI 程序启动控制台子进程会弹出黑窗口，隐藏之（非 Windows 平台取 0）
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def find_javap():
    """在 PATH 中查找 javap；找不到返回 None。"""
    return shutil.which('javap')


def find_cfr():
    """定位随程序分发的 cfr.jar 与 java；任一不可用返回 None。

    返回 (cfr_jar 路径, java 路径)。打包后从 sys._MEIPASS 取 cfr.jar，
    源码运行时从本文件所在目录取。
    """
    java_path = shutil.which('java')
    if not java_path:
        return None
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    cfr_jar = os.path.join(base, 'cfr.jar')
    if not os.path.isfile(cfr_jar):
        return None
    return (cfr_jar, java_path)


def _has_cjk(s):
    """判断字符串是否含 CJK 统一表意文字（用于判断 cp437 乱码修复结果是否更好）。"""
    return any('\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' for ch in s)


def _fix_zip_name(info):
    """修正 zip 条目名编码：Windows 下用 GBK 打包的中文文件名在 zipfile 中按 cp437 解码会乱码。

    尝试 cp437→gbk 修复；当修复后含 CJK 而原名不含时采用（覆盖有/无 UTF-8 标志两种情况，
    合法 UTF-8 中文名因 cp437 编码会抛 UnicodeEncodeError 自动跳过）。
    """
    name = info.filename
    try:
        fixed = name.encode('cp437').decode('gbk')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
    if fixed != name and _has_cjk(fixed) and not _has_cjk(name):
        return fixed
    return name


def _safe_relpath(name):
    """把 zip 条目名转成安全的相对路径；非法（绝对路径/盘符/..）返回 None，防 zip-slip。"""
    name = name.replace('\\', '/')
    parts = [p for p in name.split('/') if p not in ('', '.')]
    if not parts or name.startswith('/') or any(p == '..' for p in parts):
        return None
    if ':' in parts[0]:  # 盘符，如 C:
        return None
    return '/'.join(parts)


def extract_jar(jar_path, dest_dir, on_item=None):
    """解压 jar 到 dest_dir，返回 {相对路径: {'size': int, 'crc': int}}。

    on_item(done, total, rel) 可选，每处理完一个条目回调一次，用于进度上报。
    """
    entries = {}
    with zipfile.ZipFile(jar_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        total = len(infos)
        for idx, info in enumerate(infos, 1):
            rel = _safe_relpath(_fix_zip_name(info))
            if rel is None:
                if on_item:
                    on_item(idx, total, '')
                continue
            target = os.path.join(dest_dir, *rel.split('/'))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out)
            entries[rel] = {'size': info.file_size, 'crc': info.CRC}
            if on_item:
                on_item(idx, total, rel)
    return entries


def classify(rel_name, file_path):
    """判定条目类型：class / text / binary。未知扩展名按文件头是否含 NUL 嗅探。"""
    ext = os.path.splitext(rel_name)[1].lower()
    if ext == '.class':
        return KIND_CLASS
    if ext in TEXT_EXTS:
        return KIND_TEXT
    try:
        with open(file_path, 'rb') as f:
            head = f.read(8192)
        return KIND_BINARY if b'\x00' in head else KIND_TEXT
    except OSError:
        return KIND_BINARY


def decode_text(data):
    """按 utf-8 → gbk → latin-1 顺序解码文本。"""
    for enc in ('utf-8', 'gbk'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', errors='replace')


def decompile_class(extract_dir, rel_name, javap_path):
    """用 javap 把 class 反汇编为文本。成功返回 (文本, None)；失败返回 (None, 原因)。"""
    binary_name = rel_name[:-len('.class')].replace('/', '.')
    cmd = [javap_path, '-J-Dfile.encoding=UTF-8', '-p', '-c', '-constants',
           '-classpath', extract_dir, binary_name]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=JAVAP_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, 'javap 调用失败: %s' % exc
    if proc.returncode != 0:
        err_lines = decode_text(proc.stderr).strip().splitlines()
        reason = err_lines[0] if err_lines else 'returncode=%d' % proc.returncode
        return None, 'javap 反编译失败: %s' % reason
    return decode_text(proc.stdout), None


def decompile_class_cfr(class_path, cfr_jar, java_path):
    """用 CFR 把 class 反编译为 Java 源码。成功返回 (文本, None)；失败返回 (None, 原因)。

    CFR 对损坏 class 仍返回 0 且 stdout 为空，因此以 stdout 是否非空判定成功。
    """
    cmd = [java_path, '-jar', cfr_jar, class_path, '--silent', 'true']
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=CFR_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, 'CFR 调用失败: %s' % exc
    text = decode_text(proc.stdout)
    if proc.returncode != 0 or not text.strip():
        err_lines = decode_text(proc.stderr).strip().splitlines()
        reason = err_lines[0] if err_lines else 'returncode=%d' % proc.returncode
        return None, 'CFR 反编译失败: %s' % reason
    return text, None


def _cfr_output_key(class_path, base_dir):
    """推导 CFR --outputdir 写出的相对路径（按 class 内部包名，不含 classes 前缀）。"""
    rel = os.path.relpath(class_path, base_dir).replace('\\', '/')
    for prefix in ('BOOT-INF/classes/', 'WEB-INF/classes/'):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    return rel[:-len('.class')] + '.java'


def decompile_classes_cfr(class_paths, base_dir, cfr_jar, java_path, out_dir):
    """一次 JVM 批量反编译一批 class，返回 {class_path: 源码文本}。

    CFR 的 stdout 拼接输出顺序与输入顺序不一致，故改用 --outputdir 按文件名回读。
    只有输出文件存在且只被一个输入 class 认领时才采用；内部类、损坏 class 等
    缺失情况由调用方回退到逐个反编译。
    """
    if not class_paths:
        return {}
    os.makedirs(out_dir, exist_ok=True)
    claims = {}
    for path in class_paths:
        claims.setdefault(_cfr_output_key(path, base_dir), []).append(path)
    cmd = [java_path, '-jar', cfr_jar] + list(class_paths) + [
        '--silent', 'true', '--outputdir', out_dir]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=CFR_BATCH_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return {}  # 整批失败，交由调用方逐个回退
    if proc.returncode != 0:
        return {}
    result = {}
    for key, paths in claims.items():
        if len(paths) != 1:
            continue  # 多个 class 写同一文件，无法区分，交给逐个回退
        target = os.path.join(out_dir, *key.split('/'))
        if not os.path.isfile(target):
            continue
        try:
            with open(target, 'rb') as f:
                text = decode_text(f.read())
        except OSError:
            continue
        if text.strip():
            result[paths[0]] = text
    return result


def align_diff(left_lines, right_lines):
    """对齐两侧行，返回 [(左行号, 左文本, 右行号, 右文本, 类型)]。

    类型 ∈ {'same', 'removed', 'added', 'changed'}；缺失一侧的行号/文本为 None。
    参数可为 None（表示该侧无文本），按空列表处理。
    """
    left_lines = left_lines or []
    right_lines = right_lines or []
    matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                rows.append((i1 + k + 1, left_lines[i1 + k], j1 + k + 1, right_lines[j1 + k], 'same'))
        elif tag == 'replace':
            for k in range(max(i2 - i1, j2 - j1)):
                lno = i1 + k + 1 if k < i2 - i1 else None
                ltxt = left_lines[i1 + k] if k < i2 - i1 else None
                rno = j1 + k + 1 if k < j2 - j1 else None
                rtxt = right_lines[j1 + k] if k < j2 - j1 else None
                rows.append((lno, ltxt, rno, rtxt, 'changed'))
        elif tag == 'delete':
            for k in range(i1, i2):
                rows.append((k + 1, left_lines[k], None, None, 'removed'))
        else:  # insert
            for k in range(j1, j2):
                rows.append((None, None, k + 1, right_lines[k], 'added'))
    return rows


class CompareResult(object):
    """一次对比的结果。temp_dir 为解压临时目录，用完调用 cleanup() 释放。"""

    def __init__(self, entries, summary, temp_dir):
        self.entries = entries
        self.summary = summary
        self.temp_dir = temp_dir

    def cleanup(self):
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None


def _entry(path, status, kind, left_size, right_size, left_text, right_text, note):
    return {
        'path': path, 'status': status, 'kind': kind,
        'left_size': left_size, 'right_size': right_size,
        'left_text': left_text, 'right_text': right_text, 'note': note,
    }


def _read_side(root_dir, file_path, rel_name, kind, javap_path, cfr, cfr_text=None):
    """读取单侧内容为展示文本。返回 (text, note)；成功时 note 为 None。

    cfr_text 为批量反编译预取到的源码；为 None 时按原逻辑逐个反编译。
    """
    if kind == KIND_CLASS:
        if cfr_text is not None:
            return cfr_text, None
        err = None
        if cfr:
            text, err = decompile_class_cfr(file_path, cfr[0], cfr[1])
            if text is not None:
                return text, None
        if javap_path:
            text, err2 = decompile_class(root_dir, rel_name, javap_path)
            if text is not None:
                return text, None
            err = err2 or err
        return None, err or '未找到 CFR / javap，无法反编译 class，按二进制处理'
    if kind == KIND_TEXT:
        with open(file_path, 'rb') as f:
            return decode_text(f.read()), None
    return None, None  # binary 不出文本


def compare_jars(jar_a, jar_b, work_dir=None, javap_path='auto', cfr='auto',
                 progress=None):
    """对比两个 jar。jar_a=旧版本（左），jar_b=新版本（右）。

    返回 CompareResult：
      entries: [{'path','status','kind','left_size','right_size','left_text','right_text','note'}, ...]
      summary: {'total','same','changed','added','removed'}
    cfr 传 None 可强制走 javap 回退路径。
    progress(percent, message) 可选，用于上报 0–100 的阶段进度。
    """
    if javap_path == 'auto':
        javap_path = find_javap()
    if cfr == 'auto':
        cfr = find_cfr()
    own_temp = work_dir is None
    temp_dir = work_dir or tempfile.mkdtemp(prefix='jardiff_')
    dir_a = os.path.join(temp_dir, 'a')
    dir_b = os.path.join(temp_dir, 'b')
    os.makedirs(dir_a, exist_ok=True)
    os.makedirs(dir_b, exist_ok=True)

    def report(pct, msg):
        if progress:
            progress(pct, msg)

    try:
        def on_a(done, total, rel):
            report(8.0 * done / max(1, total), '解压旧版本 ' + rel)

        def on_b(done, total, rel):
            report(8.0 + 8.0 * done / max(1, total), '解压新版本 ' + rel)

        entries_a = extract_jar(jar_a, dir_a, on_a)
        entries_b = extract_jar(jar_b, dir_b, on_b)

        all_paths = sorted(set(entries_a) | set(entries_b))
        # 预先批量反编译需要文本对比的 class；缺失项在主循环里逐个回退。
        cfr_map = {}
        if cfr:
            class_a = []
            class_b = []
            for rel in all_paths:
                if not rel.endswith('.class'):
                    continue
                ea = entries_a.get(rel)
                eb = entries_b.get(rel)
                if ea is not None and eb is not None and ea['crc'] == eb['crc']:
                    continue  # CRC 相同不读文本
                pa = os.path.join(dir_a, *rel.split('/'))
                pb = os.path.join(dir_b, *rel.split('/'))
                if ea is not None and os.path.isfile(pa):
                    class_a.append(pa)
                if eb is not None and os.path.isfile(pb):
                    class_b.append(pb)
            total_class = len(class_a) + len(class_b)
            done_class = 0
            for side, paths, base in (('a', class_a, dir_a), ('b', class_b, dir_b)):
                for start in range(0, len(paths), CFR_BATCH_SIZE):
                    batch = paths[start:start + CFR_BATCH_SIZE]
                    cfr_map.update(decompile_classes_cfr(
                        batch, base, cfr[0], cfr[1],
                        os.path.join(temp_dir, 'cfr', side, 'b%d' % start)))
                    done_class += len(batch)
                    report(16.0 + 64.0 * done_class / max(1, total_class),
                           '反编译 %s（%d/%d）' % (
                               os.path.basename(batch[0]), done_class, total_class))
        report(80.0, '正在整理差异')

        out = []
        counts = {'total': len(all_paths), 'same': 0, 'changed': 0, 'added': 0, 'removed': 0}
        for idx, rel in enumerate(all_paths, 1):
            report(80.0 + 20.0 * idx / max(1, len(all_paths)), '整理 ' + rel)
            ea = entries_a.get(rel)
            eb = entries_b.get(rel)
            path_a = os.path.join(dir_a, *rel.split('/'))
            path_b = os.path.join(dir_b, *rel.split('/'))

            if ea is not None and eb is not None:
                if ea['crc'] == eb['crc']:
                    kind = classify(rel, path_a)
                    out.append(_entry(rel, STATUS_SAME, kind, ea['size'], eb['size'], None, None, None))
                    counts['same'] += 1
                    continue
                status = STATUS_CHANGED
                left_size, right_size = ea['size'], eb['size']
            elif eb is not None:
                status = STATUS_ADDED
                left_size, right_size = 0, eb['size']
            else:
                status = STATUS_REMOVED
                left_size, right_size = ea['size'], 0

            left_text = right_text = None
            note = None
            if status != STATUS_ADDED and os.path.isfile(path_a):
                left_text, err = _read_side(dir_a, path_a, rel, classify(rel, path_a),
                                            javap_path, cfr, cfr_map.get(path_a))
                note = err
            if status != STATUS_REMOVED and os.path.isfile(path_b):
                right_text, err = _read_side(dir_b, path_b, rel, classify(rel, path_b),
                                             javap_path, cfr, cfr_map.get(path_b))
                note = note or err
            if status == STATUS_CHANGED and left_text is None and right_text is None:
                note = note or '二进制文件，无法按文本对比'
            if (status == STATUS_CHANGED and left_text is not None
                    and right_text is not None and left_text == right_text):
                # 字节不同但反编译/解码文本逐行相同，按「相同」展示
                status = STATUS_SAME
            out.append(_entry(rel, status, classify(rel, path_a if os.path.isfile(path_a) else path_b),
                              left_size, right_size, left_text, right_text, note))
            counts[status] += 1
        report(100.0, '对比完成')
        return CompareResult(out, counts, temp_dir)
    except BaseException:
        if own_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def format_size(n):
    if n is None:
        return '-'
    if n < 1024:
        return '%d B' % n
    if n < 1024 * 1024:
        return '%.1f KB' % (n / 1024.0)
    return '%.2f MB' % (n / 1024.0 / 1024.0)
