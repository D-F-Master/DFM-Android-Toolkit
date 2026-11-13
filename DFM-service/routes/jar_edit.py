# -*- coding: utf-8 -*-
"""jar 代码修改工具：导入 jar → 浏览/反编译 class → 编辑源码 → javac 编译 → 替换回 jar 导出。

复刻的手工流程：
1. 上传 jar（支持 Spring Boot fat jar：类在 BOOT-INF/classes，依赖在 BOOT-INF/lib）
2. 选 class 用 CFR 反编译出 java 源码（含匿名内部类）
3. 修改源码后 javac 编译（-cp 类目录与依赖 jar），生成 class 及其匿名类 $N.class
4. 把编译产物替换进 jar 的对应条目，导出为「原名_修改.jar」下载
"""
import atexit
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import zipfile

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from utils.tool_manager import get_lib_path

router = APIRouter(
    prefix="/api/jaredit",
    tags=["jar代码修改"],
)

FAT_CLASSES = "BOOT-INF/classes/"   # fat jar 里类文件的统一前缀
FAT_LIB = "BOOT-INF/lib/"           # fat jar 里依赖 jar 的目录

JAVAC_TIMEOUT = 120  # 编译超时（秒）
CFR_TIMEOUT = 60     # 单类反编译超时（秒）

# 同时最多保留的会话数（每个会话占一个临时目录：原 jar + 解包 + 编译产物）
MAX_SESSIONS = 3

_lock = threading.Lock()
_sessions = {}      # id -> _Session
_sessions_order = []  # 会话 id 的创建顺序（淘汰最旧用）


class _Session(object):
    def __init__(self, sid, name, work_dir):
        self.id = sid
        self.name = name                # 原始文件名（导出命名用）
        self.work_dir = work_dir        # 会话临时目录
        self.jar_path = os.path.join(work_dir, name)
        self.extract_dir = os.path.join(work_dir, "extracted")
        self.classes_dir = None         # 类根目录（fat jar 为 extracted/BOOT-INF/classes）
        self.lib_dir = None             # 依赖目录（fat jar 为 extracted/BOOT-INF/lib；普通 jar 为 None）
        self.fat = False
        self.entries = []               # 相对 classes_dir 的 .class 路径列表（/ 分隔）
        self.out_jar = None             # 最近一次编译替换生成的 jar 路径


def _cleanup(session):
    shutil.rmtree(session.work_dir, ignore_errors=True)


def _cleanup_all():
    for session in list(_sessions.values()):
        _cleanup(session)
    _sessions.clear()
    _sessions_order.clear()


atexit.register(_cleanup_all)


def _get_session(sid):
    with _lock:
        return _sessions.get(sid)


def _safe_name(filename, fallback):
    name = os.path.basename(filename or "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or fallback


def _find_tool(tool):
    """返回 java/javac 的可执行路径；找不到返回 None。"""
    return shutil.which(tool)


def _list_entries(classes_dir):
    """列出类根目录下所有 .class 的相对路径（/ 分隔、排序）。"""
    entries = []
    for dirpath, _, files in os.walk(classes_dir):
        for fn in files:
            if fn.endswith(".class"):
                rel = os.path.relpath(os.path.join(dirpath, fn), classes_dir)
                entries.append(rel.replace(os.sep, "/"))
    entries.sort()
    return entries


@router.post("/upload", summary="上传 jar 并解包，返回 class 列表")
async def upload(file: UploadFile = File(..., description="待修改的 jar/war")):
    name = _safe_name(file.filename, "upload.jar")
    work_dir = tempfile.mkdtemp(prefix="jaredit_")
    try:
        session = _Session(uuid.uuid4().hex, name, work_dir)
        with open(session.jar_path, "wb") as f:
            f.write(await file.read())

        with zipfile.ZipFile(session.jar_path) as zf:
            names = zf.namelist()
            session.fat = any(n.startswith(FAT_CLASSES) for n in names)
            # zipfile.extractall 自带路径净化（剥掉绝对路径与 ..），无 zip-slip 风险
            zf.extractall(session.extract_dir)

        session.classes_dir = os.path.join(session.extract_dir, "BOOT-INF", "classes") if session.fat else session.extract_dir
        session.lib_dir = os.path.join(session.extract_dir, "BOOT-INF", "lib") if session.fat else None
        session.entries = _list_entries(session.classes_dir)

        with _lock:
            while len(_sessions_order) >= MAX_SESSIONS:
                oldest = _sessions_order.pop(0)
                old = _sessions.pop(oldest, None)
                if old is not None:
                    _cleanup(old)
            _sessions[session.id] = session
            _sessions_order.append(session.id)

        return {"code": 200, "message": "success", "data": {
            "id": session.id,
            "name": name,
            "fat": session.fat,
            "entries": session.entries,
        }}
    except zipfile.BadZipFile:
        shutil.rmtree(work_dir, ignore_errors=True)
        return {"code": 400, "message": "文件不是有效的 jar/zip 包", "data": None}
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


@router.get("/source/{sid}", summary="反编译指定 class，返回 java 源码")
def source(sid: str, path: str):
    session = _get_session(sid)
    if session is None:
        return {"code": 404, "message": "会话不存在或已被新上传替换，请重新上传 jar", "data": None}
    if path not in session.entries:
        return {"code": 404, "message": "class 不存在: %s" % path, "data": None}

    cfr_jar = get_lib_path() / "cfr.jar"
    java_path = _find_tool("java")
    if java_path is None:
        return {"code": 400, "message": "未找到 java，无法反编译", "data": None}
    if not cfr_jar.is_file():
        return {"code": 400, "message": "未找到 cfr.jar，无法反编译", "data": None}

    class_file = os.path.join(session.classes_dir, *path.split("/"))
    out_dir = tempfile.mkdtemp(prefix="jaredit_cfr_", dir=session.work_dir)
    cmd = [java_path, "-jar", str(cfr_jar), class_file, "--outputdir", out_dir]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CFR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"code": 400, "message": "反编译超时", "data": None}
    if proc.returncode != 0:
        return {"code": 400, "message": "反编译失败: %s" % (proc.stderr or proc.stdout or "").strip()[:2000], "data": None}

    # CFR 输出 <out_dir>/<包路径>/<类名>.java；匿名类合并进顶层类的同名文件
    expect = os.path.join(out_dir, *path[:-len(".class")].split("/")) + ".java"
    if os.path.isfile(expect):
        src_file = expect
    else:
        found = []
        for dirpath, _, files in os.walk(out_dir):
            for fn in files:
                if fn.endswith(".java"):
                    found.append(os.path.join(dirpath, fn))
        if not found:
            return {"code": 400, "message": "反编译未产出源码", "data": None}
        src_file = found[0]
    with open(src_file, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return {"code": 200, "message": "success", "data": {"path": path, "source": text}}


@router.post("/save", summary="编译修改后的源码并替换进 jar，生成新 jar")
async def save(sid: str = Form(...), path: str = Form(...), source: str = Form(...)):
    session = _get_session(sid)
    if session is None:
        return {"code": 404, "message": "会话不存在或已被新上传替换，请重新上传 jar", "data": None}
    if path not in session.entries:
        return {"code": 404, "message": "class 不存在: %s" % path, "data": None}

    javac_path = _find_tool("javac")
    if javac_path is None:
        return {"code": 400, "message": "未找到 javac（需要 JDK 而非 JRE），无法编译", "data": None}

    rel_class = path[:-len(".class")]           # com/demo/Calc
    java_file = os.path.join(session.work_dir, "src", *rel_class.split("/")) + ".java"
    os.makedirs(os.path.dirname(java_file), exist_ok=True)
    with open(java_file, "w", encoding="utf-8") as f:
        f.write(source)

    out_dir = os.path.join(session.work_dir, "out")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)

    # 与手工流程一致：类目录 + 依赖 jar 一起进 classpath（lib/* 通配由 javac 自行展开）
    cp_parts = [session.classes_dir]
    if session.fat and os.path.isdir(session.lib_dir):
        cp_parts.append(os.path.join(session.lib_dir, "*"))
    cp = os.pathsep.join(cp_parts)

    cmd = [javac_path, "-encoding", "UTF-8", "-cp", cp, "-d", out_dir, java_file]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=JAVAC_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"code": 400, "message": "编译超时", "data": None}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return {"code": 400, "message": "编译失败：\n%s" % detail[:4000], "data": None}

    # 编译产物：顶层类 + 匿名/内部类（Calc.class、Calc$1.class…）
    generated = _list_entries(out_dir)
    if not generated:
        return {"code": 400, "message": "编译未产出 class 文件", "data": None}

    prefix = FAT_CLASSES if session.fat else ""
    # 旧 jar 里要移除的条目：顶层类本体 + 它的 $N 匿名类（内部类可能增删，避免残留旧的 $N）
    remove_exact = prefix + rel_class
    remove_prefix = prefix + rel_class + "$"

    out_jar = os.path.join(session.work_dir, re.sub(r"\.jar$", "_修改.jar", session.name, flags=re.IGNORECASE))
    replaced = set()
    with zipfile.ZipFile(session.jar_path) as zin, \
            zipfile.ZipFile(out_jar, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename == remove_exact or info.filename.startswith(remove_prefix):
                replaced.add(info.filename)
                continue
            # 原样拷贝其余条目（保留原 ZipInfo 的压缩方式与属性）
            zout.writestr(info, zin.read(info.filename))
        for rel in generated:
            with open(os.path.join(out_dir, *rel.split("/")), "rb") as f:
                zout.writestr(prefix + rel, f.read())
            replaced.add(prefix + rel)

    session.out_jar = out_jar
    return {"code": 200, "message": "success", "data": {
        "file": os.path.basename(out_jar),
        "replaced": sorted(replaced),
    }}


@router.get("/download/{sid}", summary="下载编译替换后的 jar")
def download(sid: str):
    session = _get_session(sid)
    if session is None:
        return {"code": 404, "message": "会话不存在或已被新上传替换", "data": None}
    if session.out_jar is None or not os.path.isfile(session.out_jar):
        return {"code": 400, "message": "还没有编译产出的新 jar，请先编译", "data": None}
    return FileResponse(session.out_jar, media_type="application/java-archive", filename=os.path.basename(session.out_jar))
