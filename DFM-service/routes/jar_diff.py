# -*- coding: utf-8 -*-
"""JAR 包更新对比接口（核心逻辑来自 utils/jar_diff_core.py）。

- POST /api/jardiff/compare      上传旧/新两个 jar，后台线程开始对比（同一时刻仅允许一个任务）
- GET  /api/jardiff/status/{id}  轮询进度；完成后返回 summary + 条目列表
- GET  /api/jardiff/entry/{id}   单个条目的行级 diff（?path=）
- GET  /api/jardiff/export/{id}  生成 markdown 报告，返回 {filename, content}
- GET  /api/jardiff/backend      CFR/javap 可用性说明
"""
import atexit
import os
import re
import shutil
import tempfile
import threading
import uuid

from fastapi import APIRouter, File, UploadFile

from utils import jar_diff_core as core
from utils import md_export
from utils.tool_manager import get_lib_path

router = APIRouter(
    prefix="/api/jardiff",
    tags=["jar包对比"],
)


class _Job(object):
    """一次对比任务的状态。同一时刻只保留最近一次任务。"""

    def __init__(self, jar_a, jar_b, upload_dir):
        self.id = uuid.uuid4().hex
        self.percent = 0.0
        self.message = "准备中"
        self.done = False
        self.error = None
        self.result = None            # core.CompareResult
        self.jar_a = jar_a            # 上传文件的原始文件名（报告展示用）
        self.jar_b = jar_b
        self.upload_dir = upload_dir  # 上传的 jar 暂存目录


_lock = threading.Lock()
_job = None


def _cleanup(job):
    """释放任务占用的临时目录（对比结果 + 上传文件）。"""
    if job.result is not None:
        job.result.cleanup()
        job.result = None
    if job.upload_dir:
        shutil.rmtree(job.upload_dir, ignore_errors=True)
        job.upload_dir = None


atexit.register(lambda: _job is not None and _cleanup(_job))


def _get_job(job_id):
    job = _job
    if job is None or job.id != job_id:
        return None
    return job


def _safe_name(filename, fallback):
    """取上传文件名的 basename 并去掉非法字符，避免路径穿越。"""
    name = os.path.basename(filename or "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or fallback


def _find_cfr():
    """返回 (cfr_jar路径, java路径)；不可用返回 None。"""
    java_path = shutil.which("java")
    if not java_path:
        return None
    cfr_jar = get_lib_path() / "cfr.jar"
    if not cfr_jar.is_file():
        return None
    return (str(cfr_jar), java_path)


def _run(job, path_a, path_b):
    def on_progress(percent, message):
        job.percent = percent
        job.message = message

    try:
        result = core.compare_jars(path_a, path_b, cfr=_find_cfr(), progress=on_progress)
        job.result = result
    except BaseException as exc:
        job.error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        job.done = True


@router.post("/compare", summary="上传新旧 jar 开始对比")
async def compare(old: UploadFile = File(..., description="旧版本 jar"),
                  new: UploadFile = File(..., description="新版本 jar")):
    global _job
    with _lock:
        if _job is not None and not _job.done:
            return {"code": 400, "message": "已有对比任务进行中，请等待完成", "data": None}

        upload_dir = tempfile.mkdtemp(prefix="jardiff_upload_")
        try:
            dir_a = os.path.join(upload_dir, "a")
            dir_b = os.path.join(upload_dir, "b")
            os.makedirs(dir_a)
            os.makedirs(dir_b)
            path_a = os.path.join(dir_a, _safe_name(old.filename, "old.jar"))
            path_b = os.path.join(dir_b, _safe_name(new.filename, "new.jar"))
            with open(path_a, "wb") as f:
                f.write(await old.read())
            with open(path_b, "wb") as f:
                f.write(await new.read())
        except BaseException:
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise

        job = _Job(os.path.basename(path_a), os.path.basename(path_b), upload_dir)
        if _job is not None:
            _cleanup(_job)
        _job = job

    threading.Thread(target=_run, args=(job, path_a, path_b), daemon=True).start()
    return {"code": 200, "message": "success", "data": {"id": job.id}}


@router.get("/status/{job_id}", summary="轮询对比进度与结果")
def status(job_id: str):
    job = _get_job(job_id)
    if job is None:
        return {"code": 404, "message": "对比任务不存在或已被新任务替换", "data": None}

    data = {
        "id": job.id,
        "percent": round(job.percent, 1),
        "message": job.message,
        "done": job.done,
        "error": job.error,
    }
    if job.done and job.result is not None:
        data["summary"] = job.result.summary
        data["entries"] = [{
            "path": e["path"],
            "status": e["status"],
            "kind": e["kind"],
            "left_size": core.format_size(e["left_size"]),
            "right_size": core.format_size(e["right_size"]),
            "note": e["note"],
        } for e in job.result.entries]
    return {"code": 200, "message": "success", "data": data}


@router.get("/entry/{job_id}", summary="单个条目的行级 diff")
def entry(job_id: str, path: str):
    job = _get_job(job_id)
    if job is None:
        return {"code": 404, "message": "对比任务不存在或已被新任务替换", "data": None}
    if job.result is None:
        return {"code": 400, "message": "任务未完成或已失败", "data": None}

    for e in job.result.entries:
        if e["path"] == path:
            rows = []
            if e["left_text"] is not None or e["right_text"] is not None:
                rows = core.align_diff(
                    e["left_text"].splitlines() if e["left_text"] is not None else None,
                    e["right_text"].splitlines() if e["right_text"] is not None else None)
            return {"code": 200, "message": "success", "data": {
                "path": e["path"],
                "status": e["status"],
                "kind": e["kind"],
                "note": e["note"],
                "rows": [[lno, ltxt, rno, rtxt, kind] for (lno, ltxt, rno, rtxt, kind) in rows],
            }}
    return {"code": 404, "message": "条目不存在: %s" % path, "data": None}


@router.get("/export/{job_id}", summary="导出 markdown 对比报告")
def export(job_id: str):
    job = _get_job(job_id)
    if job is None:
        return {"code": 404, "message": "对比任务不存在或已被新任务替换", "data": None}
    if job.result is None:
        return {"code": 400, "message": "任务未完成或已失败", "data": None}

    content = md_export.build_markdown(job.result, job.jar_a, job.jar_b)
    # output_path 依赖 jar_b 的完整路径，这里只需要文件名部分
    filename = os.path.basename(md_export.output_path(job.jar_b))
    return {"code": 200, "message": "success", "data": {"filename": filename, "content": content}}


@router.get("/backend", summary="反编译环境可用性")
def backend():
    if _find_cfr() is not None:
        text = "CFR 反编译可用（class 显示为 Java 源码）"
    elif core.find_javap() is not None:
        text = "未找到 CFR，javap 反汇编可用（class 显示为字节码）"
    else:
        text = "未找到 CFR/javap，class 文件按二进制处理"
    return {"code": 200, "message": "success", "data": {"text": text}}
