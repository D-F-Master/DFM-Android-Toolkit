# -*- coding: utf-8 -*-
"""把两个 jar 的差异导出为 markdown（按方法分组，旧/新完整方法体对照）。

由 jar_diff_app.py 调用：path = md_export.export_markdown(result, jar_a, jar_b)
"""
import datetime
import os
import re

from utils import jar_diff_core as core

UNKNOWN_METHOD = '其他差异（未识别到方法）'
CONTEXT_LINES = 3
MAX_METHOD_LINES = 80       # 方法体超过此行数时只展示差异附近内容
METHOD_CONTEXT = 12         # 大方法每个差异行前后保留的行数

_BAD_PREFIX = {
    'if', 'for', 'while', 'switch', 'catch', 'new', 'return', 'assert',
    'else', 'do', 'case', 'throw', 'super', 'this', 'instanceof', 'break',
    'continue', 'package', 'import', 'class', 'interface', 'enum', 'record', 'try',
}
_BAD_NAME = _BAD_PREFIX | {'synchronized'}
_DECL_RE = re.compile(r'^\s*(.*?)([\w$]+)\s*\(')
_TICKS_RE = re.compile(r'`+')
_JAVAP_CODE_RE = re.compile(r'^\d+:')


def _method_signature(line):
    """该行像 Java 方法声明（含 javap 方法头）时返回签名文本，否则 None。"""
    s = line.strip()
    if not s or s[0] in '@/*' or s.startswith('//'):
        return None
    if _JAVAP_CODE_RE.match(s):        # javap 字节码行
        return None
    m = _DECL_RE.match(s)
    if not m:
        return None
    prefix, name = m.group(1), m.group(2)
    if name in _BAD_NAME or '=' in prefix or '//' in prefix:
        return None
    if re.search(r'\b(%s)\b' % '|'.join(sorted(_BAD_PREFIX)), prefix):
        return None
    # obj.method(...) 之类的调用：名字前紧挨着 '.'，声明则必须有空格或位于行首；
    # javap 的构造方法头（public com.demo.Hello();）例外，它以修饰符开头。
    modifier = bool(re.match(
        r'^(public|private|protected|static|final|abstract|native|strictfp|default)\b', s))
    if prefix and not prefix[-1].isspace() and not (s.endswith(');') and modifier):
        return None
    if s.endswith(';'):
        # 语句调用 foo(x); 没有返回类型/修饰符，不认作方法声明
        if not prefix.strip():
            return None
        return s
    if s.endswith('{'):
        return s[:-1].rstrip()
    return None


def _match_brace(lines, start):
    """从 start 行起做花括号匹配（忽略字符串/字符/注释），返回闭合行号。"""
    depth = 0
    in_block = False
    for i in range(start, len(lines)):
        line = lines[i]
        j = 0
        quote = None
        while j < len(line):
            c = line[j]
            nxt = line[j + 1] if j + 1 < len(line) else ''
            if in_block:
                if c == '*' and nxt == '/':
                    in_block = False
                    j += 2
                else:
                    j += 1
                continue
            if quote:
                if c == '\\':
                    j += 2
                else:
                    if c == quote:
                        quote = None
                    j += 1
                continue
            if c == '/' and nxt == '/':
                break
            if c == '/' and nxt == '*':
                in_block = True
                j += 2
                continue
            if c in ('"', "'"):
                quote = c
                j += 1
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return i
            j += 1
    return None


def _is_javap(lines):
    return any(_JAVAP_CODE_RE.match(l.strip()) for l in lines)


def _method_blocks(lines):
    """切出方法块：[(start, end, signature), ...]，0 基闭区间。"""
    decls = []
    for i, line in enumerate(lines):
        sig = _method_signature(line)
        if sig is not None:
            decls.append((i, sig, line.strip()))
    if not decls:
        return []
    javap = _is_javap(lines)
    out = []
    for k, (start, sig, text) in enumerate(decls):
        nxt = decls[k + 1][0] if k + 1 < len(decls) else len(lines)
        if javap:
            end = nxt - 1
        elif text.endswith('{'):
            end = _match_brace(lines, start)
            if end is None or end >= nxt:
                end = nxt - 1
        else:
            end = start          # 无方法体的声明（抽象/接口方法）
        while end > start:
            t = lines[end].strip()
            if not t or (javap and t == '}'):
                end -= 1
            else:
                break
        out.append((start, end, sig))
    return out


def _line_method(blocks, lineno):
    """1 基行号 → 所属方法块，或 None。"""
    if not lineno:
        return None
    i = lineno - 1
    for b in blocks:
        if b[0] <= i <= b[1]:
            return b
    return None


def _class_name(path):
    name = path[:-6] if path.endswith('.class') else path
    for marker in ('BOOT-INF/classes/', 'WEB-INF/classes/'):
        if marker in name:
            name = name.split(marker, 1)[1]
            break
    return name.replace('/', '.')


def _code_span(text):
    s = str(text).replace('\r', ' ').replace('\n', ' ')
    longest = max([len(r) for r in _TICKS_RE.findall(s)] + [0])
    ticks = '`' * (longest + 1)
    pad = ' ' if s.startswith('`') or s.endswith('`') else ''
    return ticks + pad + s + pad + ticks


def _fence(lines):
    longest = 0
    for line in lines:
        for run in _TICKS_RE.findall(line):
            longest = max(longest, len(run))
    return '`' * max(3, longest + 1)


def _code_block(out, lines, start, end, diff_lines, lang):
    """带行号的方法体；变化行前标 '>'；过大方法只展示差异附近内容。"""
    width = max(1, len(str(end + 1)))
    total = end - start + 1
    if total <= MAX_METHOD_LINES:
        selected = list(range(start, end + 1))
    else:
        # 大方法：保留签名/结尾行与每个差异行前后的窗口，避免报告随文件膨胀
        keep = {start, end}
        for d in diff_lines:
            lo = max(start, d - 1 - METHOD_CONTEXT)
            hi = min(end, d - 1 + METHOD_CONTEXT)
            keep.update(range(lo, hi + 1))
        selected = sorted(keep)
    body = []
    prev = None
    for i in selected:
        if prev is not None and i > prev + 1:
            body.append('%s %*s | ... (省略 %d 行) ...' % (
                ' ', width, '...', i - prev - 1))
        mark = '>' if (i + 1) in diff_lines else ' '
        body.append('%s %*d | %s' % (mark, width, i + 1, lines[i]))
        prev = i
    fence = _fence(body)
    out.append(fence + lang)
    out.extend(body)
    out.append(fence)


def _method_map(blocks):
    out = {}
    for b in blocks:
        out.setdefault(b[2], b)
    return out


def _append_methods(out, llines, rlines, rows, jar_a, jar_b):
    """输出变化方法的旧/新完整方法体对照；返回方法外的变化行索引与是否有方法结构。"""
    left_blocks = _method_blocks(llines)
    right_blocks = _method_blocks(rlines)
    left_map = _method_map(left_blocks)
    right_map = _method_map(right_blocks)
    lang = 'text' if (_is_javap(llines) or _is_javap(rlines)) else 'java'
    order = []
    groups = {}
    other = []
    for idx, (lno, ltxt, rno, rtxt, kind) in enumerate(rows):
        if kind == 'same':
            continue
        lb = _line_method(left_blocks, lno)
        rb = _line_method(right_blocks, rno)
        if lb is None and rb is None:
            other.append(idx)
            continue
        lsig = lb[2] if lb else None
        rsig = rb[2] if rb else None
        # 用签名做 key：同一方法的多行差异（含只有单侧命中的插入行）归入同一节
        key = rsig or lsig
        if key not in groups:
            groups[key] = {'lb': lb, 'rb': rb, 'lsig': lsig, 'rsig': rsig,
                           'l': set(), 'r': set()}
            order.append(key)
        g = groups[key]
        if g['lb'] is None:
            g['lb'] = lb or left_map.get(key)
        if g['rb'] is None:
            g['rb'] = rb or right_map.get(key)
        if lsig and rsig and lsig != rsig:
            g['lsig'], g['rsig'] = lsig, rsig
        if lno:
            g['l'].add(lno)
        if rno:
            g['r'].add(rno)

    for key in order:
        g = groups[key]
        lb, rb = g['lb'], g['rb']
        if g['lsig'] and g['rsig'] and g['lsig'] != g['rsig']:
            title = '方法 %s → %s' % (_code_span(g['lsig']), _code_span(g['rsig']))
        else:
            title = '方法 %s' % _code_span(key)
        out.append('#### %s' % title)
        out.append('')
        out.append('- 位置：旧版本 %s / 新版本 %s' % (
            ('第 %d–%d 行' % (lb[0] + 1, lb[1] + 1)) if lb else '无',
            ('第 %d–%d 行' % (rb[0] + 1, rb[1] + 1)) if rb else '无'))
        out.append('- 变化行：旧 %s / 新 %s' % (
            ','.join(str(n) for n in sorted(g['l'])) or '无',
            ','.join(str(n) for n in sorted(g['r'])) or '无'))
        out.append('')
        out.append('**旧版本（%s）**' % _code_span(os.path.basename(jar_a) or '(未记录)'))
        out.append('')
        if lb:
            _code_block(out, llines, lb[0], lb[1], g['l'], lang)
        else:
            out.append('（该版本无此方法）')
        out.append('')
        out.append('**新版本（%s）**' % _code_span(os.path.basename(jar_b) or '(未记录)'))
        out.append('')
        if rb:
            _code_block(out, rlines, rb[0], rb[1], g['r'], lang)
        else:
            out.append('（该版本无此方法）')
        out.append('')
    return other, bool(left_blocks or right_blocks), left_blocks, right_blocks


def _row_in_method(rows, i, left_blocks, right_blocks):
    lno, ltxt, rno, rtxt, kind = rows[i]
    return (_line_method(left_blocks, lno) is not None
            or _line_method(right_blocks, rno) is not None)


def _append_context_diff(out, rows, hot, left_blocks, right_blocks):
    """方法外差异：带行号、@@ 位置头和前后上下文的 -旧/+新 对照。"""
    changed = [i for i in hot if rows[i][4] != 'same']
    if not changed:
        return

    def expand(center, step):
        """向 step 方向取上下文，遇到方法块内的行即停，避免重复展开方法体。"""
        boundary = center
        count = 0
        i = center + step
        while 0 <= i < len(rows) and count < CONTEXT_LINES:
            if _row_in_method(rows, i, left_blocks, right_blocks):
                break
            boundary = i
            count += 1
            i += step
        return boundary

    segments = []
    s, e = expand(changed[0], -1), expand(changed[0], 1) + 1
    for c in changed[1:]:
        cs, ce = expand(c, -1), expand(c, 1) + 1
        if cs <= e:
            e = max(e, ce)
        else:
            segments.append((s, e))
            s, e = cs, ce
    segments.append((s, e))

    max_no = 0
    for row in rows:
        for n in (row[0], row[2]):
            if n:
                max_no = max(max_no, n)
    width = max(1, len(str(max_no)))

    def fmt(n):
        return '%*d' % (width, n) if n else ' ' * width

    lines = []
    for s, e in segments:
        lnums = [rows[i][0] for i in range(s, e) if rows[i][0]]
        rnums = [rows[i][2] for i in range(s, e) if rows[i][2]]
        lines.append('@@ -%d,%d +%d,%d @@' % (
            min(lnums) if lnums else 0, len(lnums),
            min(rnums) if rnums else 0, len(rnums)))
        for i in range(s, e):
            lno, ltxt, rno, rtxt, kind = rows[i]
            if kind == 'same':
                lines.append('  %s | %s' % (
                    fmt(lno or rno), ltxt if ltxt is not None else rtxt))
            else:
                if ltxt is not None:
                    lines.append('- %s | %s' % (fmt(lno), ltxt))
                if rtxt is not None:
                    lines.append('+ %s | %s' % (fmt(rno), rtxt))
    fence = _fence(lines)
    out.append(fence + 'diff')
    out.extend(lines)
    out.append(fence)
    out.append('')


def _append_changed(out, e, jar_a, jar_b):
    out.append('### %s' % _code_span(e['path']))
    out.append('')
    if e['kind'] == core.KIND_CLASS:
        out.append('- 类名：%s' % _code_span(_class_name(e['path'])))
        out.append('')
    left = e['left_text']
    right = e['right_text']
    if left is None and right is None:
        out.append('- 无文本可对比：%s' % (e['note'] or '二进制文件或反编译失败'))
        out.append('')
        return
    llines = left.splitlines() if left else []
    rlines = right.splitlines() if right else []
    rows = core.align_diff(llines or None, rlines or None)
    if not any(r[4] != 'same' for r in rows):
        out.append('（无行级差异）')
        out.append('')
        return
    hot, has_methods, left_blocks, right_blocks = _append_methods(
        out, llines, rlines, rows, jar_a, jar_b)
    if hot:
        title = UNKNOWN_METHOD if has_methods else '差异明细（无方法结构）'
        out.append('#### %s' % title)
        out.append('')
        _append_context_diff(out, rows, hot, left_blocks, right_blocks)


def build_markdown(result, jar_a, jar_b, now=None):
    """生成 md 文本（纯函数，便于测试）。"""
    now = now or datetime.datetime.now()
    s = result.summary
    out = ['# JAR 差异报告', '']
    out.append('- 旧版本：%s' % _code_span(jar_a or '(未记录)'))
    out.append('- 新版本：%s' % _code_span(jar_b or '(未记录)'))
    out.append('- 生成时间：%s' % now.strftime('%Y-%m-%d %H:%M:%S'))
    out.append('- 汇总：共 %d 个条目｜修改 %d｜新增 %d｜删除 %d｜相同 %d' % (
        s['total'], s['changed'], s['added'], s['removed'], s['same']))
    out.append('- 说明：仅展开发生变化的方法（旧/新完整方法体对照）；未变化的方法、相同文件不展开。')
    out.append('- 图例：`-` 旧版本行，`+` 新版本行，`>` 方法体内标记的变化行，行首数字为行号。')
    out.append('')

    changed = [e for e in result.entries if e['status'] == core.STATUS_CHANGED]
    out.append('## 修改（%d）' % len(changed))
    out.append('')
    if not changed:
        out.append('（无）')
        out.append('')
    for e in changed:
        _append_changed(out, e, jar_a, jar_b)

    for title, status in (('新增', core.STATUS_ADDED), ('删除', core.STATUS_REMOVED)):
        items = [e for e in result.entries if e['status'] == status]
        out.append('## %s（%d）' % (title, len(items)))
        out.append('')
        if not items:
            out.append('（无）')
        for e in items:
            size = e['right_size'] if status == core.STATUS_ADDED else e['left_size']
            line = '- %s（%s' % (_code_span(e['path']), core.format_size(size))
            if e['note']:
                line += '，%s' % e['note']
            out.append(line + '）')
        out.append('')
    return '\n'.join(out) + '\n'


def output_path(jar_b, now=None):
    """新 jar 同目录下的报告路径：<版本号>jar包新旧版本代码对比<日期>.md，重名时追加序号。"""
    now = now or datetime.datetime.now()
    folder = os.path.dirname(os.path.abspath(jar_b))
    stem = os.path.splitext(os.path.basename(jar_b))[0]
    date_str = now.strftime('%Y%m%d')
    path = os.path.join(folder, '%sjar包新旧版本代码对比%s.md' % (stem, date_str))
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, '%sjar包新旧版本代码对比%s_%d.md' % (stem, date_str, n))
        n += 1
    return path


def export_markdown(result, jar_a, jar_b, now=None):
    """写到新 jar 同目录，返回绝对路径。"""
    if not jar_b:
        raise ValueError('未记录新版本 jar 路径，无法确定导出目录')
    path = output_path(jar_b, now)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(build_markdown(result, jar_a, jar_b, now))
    return path
