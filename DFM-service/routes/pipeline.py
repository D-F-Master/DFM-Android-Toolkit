"""
生产流水线 - 步骤编排 CRUD 与多设备执行

流水线数据结构（保存为 data/pipelines/{name}.json）：
{
  "name": "流水线名",
  "steps": [
    { "type": "installApk", "apkName": "demo.apk" },
    { "type": "simulate",   "scriptName": "脚本名", "repeat": 1 },
    { "type": "screenshot", "saveDir": "" },          # 空 = D:\\log\\{设备SN}\\
    { "type": "deleteFolder", "path": "/sdcard/xxx" }
  ]
}

执行（/device/pipeline/run）：
  - mode=parallel：按步骤推进，每步所有在线设备并发执行，全部完成进入下一步；
  - mode=serial：每台设备依次跑完整条流水线，再轮到下一台。
  - 某设备某步失败后该设备退出本次执行，不影响其他设备。
  - 全程产生带设备号/步骤/级别的日志，内存缓存，前端按游标增量拉取。
"""
import json
import re
import time
import uuid
import threading
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from config import logger
from utils.tool_manager import get_data_path
from routes.adb_utils import execute_adb_command
from routes.script import _run_script_on_device, SCRIPTS_DIR

router = APIRouter(
    prefix="/api",
    tags=["生产流水线"]
)

PIPELINES_DIR = get_data_path() / "pipelines"
APKS_DIR = PIPELINES_DIR / "apks"

# 步骤类型与中文标签（日志与前端展示用）
STEP_LABELS = {
    "installApk": "安装APK",
    "simulate": "模拟操作",
    "screenshot": "获取画面",
    "deleteFolder": "删除文件夹",
}

VALID_STEP_TYPES = tuple(STEP_LABELS.keys())


def _ensure_dirs():
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    APKS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    """文件名安全过滤：只保留中英文数字下划线连字符，防止路径穿越"""
    name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', str(name)).strip('_')
    return name or "unnamed"


def _safe_device_tag(device_id: str) -> str:
    """设备号转可用作文件名的串（192.168.0.110:5555 → 192_168_0_110_5555）"""
    return re.sub(r'[^\w-]', '_', device_id)


def _validate_steps(steps) -> str:
    """校验步骤列表，返回空串表示通过，否则返回错误信息"""
    if not isinstance(steps, list) or not steps:
        return "流水线 steps 必须是非空数组"

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"第 {i + 1} 步不是对象"
        step_type = step.get("type")
        if step_type not in VALID_STEP_TYPES:
            return f"第 {i + 1} 步的 type 无效: {step_type}（应为 {'/'.join(VALID_STEP_TYPES)}）"

        if step_type == "installApk" and not str(step.get("apkName", "")).strip():
            return f"第 {i + 1} 步未选择 APK 文件"
        if step_type == "simulate" and not str(step.get("scriptName", "")).strip():
            return f"第 {i + 1} 步未选择模拟脚本"
        if step_type == "deleteFolder":
            path = str(step.get("path", "")).strip()
            if not path:
                return f"第 {i + 1} 步未填写要删除的文件夹路径"
            # rm -rf 的目标必须限定在用户区，防止误删系统目录
            if not (path.startswith("/sdcard/") or path.startswith("/storage/")):
                return f"第 {i + 1} 步的删除路径仅允许 /sdcard/ 或 /storage/ 开头"

    return ""


# ========== 流水线 CRUD ==========

class PipelineSaveRequest(BaseModel):
    name: str
    steps: List[dict]


class PipelineNameRequest(BaseModel):
    name: str


class PipelineImportRequest(BaseModel):
    content: str
    name: Optional[str] = None


@router.post("/device/pipeline/save")
def save_pipeline(request: PipelineSaveRequest):
    """保存流水线到 data/pipelines/{name}.json"""
    try:
        _ensure_dirs()
        error = _validate_steps(request.steps)
        if error:
            return {"code": 400, "message": error, "data": None}
        name = _safe_name(request.name)
        data = {"name": name, "steps": request.steps}
        path = PIPELINES_DIR / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Pipeline saved: {path}")
        return {"code": 200, "message": "success", "data": {"name": name, "path": str(path)}}
    except Exception as e:
        return {"code": 500, "message": f"保存流水线失败: {str(e)}", "data": None}


@router.post("/device/pipeline/list")
def list_pipelines():
    """列出所有流水线（名称 + 步骤数）"""
    try:
        _ensure_dirs()
        items = []
        for f in sorted(PIPELINES_DIR.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                items.append({
                    "name": data.get("name", f.stem),
                    "stepCount": len(data.get("steps", [])),
                })
            except Exception:
                continue
        return {"code": 200, "message": "success", "data": items}
    except Exception as e:
        return {"code": 500, "message": f"获取流水线列表失败: {str(e)}", "data": None}


@router.post("/device/pipeline/load")
def load_pipeline(request: PipelineNameRequest):
    """读取单条流水线完整内容"""
    try:
        _ensure_dirs()
        name = _safe_name(request.name)
        path = PIPELINES_DIR / f"{name}.json"
        if not path.exists():
            return {"code": 404, "message": f"流水线 {name} 不存在", "data": None}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:
        return {"code": 500, "message": f"读取流水线失败: {str(e)}", "data": None}


@router.post("/device/pipeline/delete")
def delete_pipeline(request: PipelineNameRequest):
    """删除流水线文件"""
    try:
        _ensure_dirs()
        name = _safe_name(request.name)
        path = PIPELINES_DIR / f"{name}.json"
        if path.exists():
            path.unlink()
            logger.info(f"Pipeline deleted: {path}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"删除流水线失败: {str(e)}", "data": None}


@router.post("/device/pipeline/import")
def import_pipeline(request: PipelineImportRequest):
    """导入流水线：解析 JSON 原文，校验后落盘（与模拟脚本导入同套路）"""
    try:
        content = (request.content or "").strip()
        if not content:
            return {"code": 400, "message": "流水线内容为空", "data": None}
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return {"code": 400, "message": f"JSON 解析失败: {str(e)}", "data": None}
        if not isinstance(data, dict):
            return {"code": 400, "message": "流水线根节点必须是对象", "data": None}

        error = _validate_steps(data.get("steps"))
        if error:
            return {"code": 400, "message": f"流水线校验失败: {error}", "data": None}

        raw_name = request.name or data.get("name") or f"imported_{int(time.time() * 1000)}"
        name = _safe_name(str(raw_name))

        _ensure_dirs()
        target = PIPELINES_DIR / f"{name}.json"
        existed = target.exists()
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"name": name, "steps": data["steps"]}, f, ensure_ascii=False, indent=2)

        logger.info(f"Pipeline imported: {target}")
        return {
            "code": 200,
            "message": "success",
            "data": {"name": name, "path": str(target), "stepCount": len(data["steps"]), "overwritten": existed},
        }
    except Exception as e:
        logger.error(f"导入流水线失败: {e}")
        return {"code": 500, "message": f"导入流水线失败: {str(e)}", "data": None}


# ========== APK 素材库 ==========

@router.post("/device/pipeline/upload-apk")
async def upload_pipeline_apk(apk: UploadFile = File(..., description="APK文件")):
    """上传 APK 到 data/pipelines/apks/，供「安装APK」步骤引用"""
    try:
        _ensure_dirs()
        filename = _safe_name(os.path.splitext(apk.filename or "")[0]) + ".apk"
        path = APKS_DIR / filename
        with open(path, "wb") as f:
            while True:
                chunk = await apk.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        size = path.stat().st_size
        logger.info(f"Pipeline APK uploaded: {path} ({size} bytes)")
        return {"code": 200, "message": "success", "data": {"name": filename, "size": size}}
    except Exception as e:
        logger.error(f"上传 APK 失败: {e}")
        return {"code": 500, "message": f"上传 APK 失败: {str(e)}", "data": None}


@router.post("/device/pipeline/list-apks")
def list_pipeline_apks():
    """列出已上传的 APK 素材"""
    try:
        _ensure_dirs()
        items = []
        for f in sorted(APKS_DIR.glob("*.apk")):
            items.append({"name": f.name, "size": f.stat().st_size})
        return {"code": 200, "message": "success", "data": items}
    except Exception as e:
        return {"code": 500, "message": f"获取 APK 列表失败: {str(e)}", "data": None}


@router.post("/device/pipeline/delete-apk")
def delete_pipeline_apk(request: PipelineNameRequest):
    """删除已上传的 APK 素材"""
    try:
        _ensure_dirs()
        filename = _safe_name(os.path.splitext(request.name)[0]) + ".apk"
        path = APKS_DIR / filename
        if path.exists():
            path.unlink()
            logger.info(f"Pipeline APK deleted: {path}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"删除 APK 失败: {str(e)}", "data": None}


# ========== 执行引擎 ==========

class PipelineRunRequest(BaseModel):
    name: str
    deviceIds: List[str]
    mode: str = "parallel"  # parallel | serial


class PipelineLogRequest(BaseModel):
    runId: str
    cursor: int = 0


_pipeline_runs = {}
_runs_lock = threading.Lock()


def _plog(run: dict, device_id: str, step: str, level: str, message: str):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "deviceId": device_id,
        "step": step,
        "level": level,  # info | ok | err
        "message": message,
    }
    with run["lock"]:
        run["logs"].append(entry)
    logger.info(f"[pipeline] {device_id} {step} {message}")


def _exec_step(device_id: str, step: dict, step_label: str, run: dict) -> bool:
    """在单台设备上执行一个步骤，返回是否成功（失败已记日志）"""
    stype = step.get("type")
    try:
        if stype == "installApk":
            apk_name = _safe_name(os.path.splitext(str(step.get("apkName", "")))[0]) + ".apk"
            path = APKS_DIR / apk_name
            if not path.exists():
                raise RuntimeError(f"APK {apk_name} 不存在，请先上传")
            _plog(run, device_id, step_label, "info", f"安装 {apk_name} ...")
            result = execute_adb_command(f'adb -s {device_id} install -r "{path}"', timeout=600)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "install failed").strip())
            return True

        elif stype == "simulate":
            script_name = _safe_name(str(step.get("scriptName", "")))
            path = SCRIPTS_DIR / f"{script_name}.json"
            if not path.exists():
                raise RuntimeError(f"模拟脚本 {script_name} 不存在")
            with open(path, "r", encoding="utf-8") as f:
                script = json.load(f)
            repeat = max(1, int(step.get("repeat", 1)))
            _plog(run, device_id, step_label, "info", f"执行模拟脚本 {script_name} ×{repeat} ...")
            _run_script_on_device(device_id, script, repeat)
            return True

        elif stype == "screenshot":
            save_dir = str(step.get("saveDir", "") or "").strip()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tag = _safe_device_tag(device_id)
            if not save_dir:
                # 默认落在 D:\log\{设备SN}\（与日志抓取同一目录约定）
                save_dir = rf"D:\log\{tag}"
            os.makedirs(save_dir, exist_ok=True)
            remote_tmp = "/sdcard/production_shot.png"
            local_path = os.path.join(save_dir, f"{tag}_{ts}.png")
            _plog(run, device_id, step_label, "info", "截取画面 ...")
            cap = execute_adb_command(f'adb -s {device_id} shell screencap -p {remote_tmp}', timeout=30)
            if not cap["success"]:
                raise RuntimeError((cap.get("error") or "screencap failed").strip())
            pull = execute_adb_command(f'adb -s {device_id} pull "{remote_tmp}" "{local_path}"', timeout=60)
            execute_adb_command(f'adb -s {device_id} shell rm -f {remote_tmp}', timeout=15)
            if not pull["success"]:
                raise RuntimeError((pull.get("error") or "pull failed").strip())
            _plog(run, device_id, step_label, "ok", f"已保存 {local_path}")
            return True

        elif stype == "deleteFolder":
            target = str(step.get("path", "") or "").strip()
            _plog(run, device_id, step_label, "info", f"删除 {target} ...")
            result = execute_adb_command(f'adb -s {device_id} shell rm -rf "{target}"', timeout=120)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "rm failed").strip())
            return True

        raise RuntimeError(f"未知步骤类型: {stype}")
    except Exception as e:
        _plog(run, device_id, step_label, "err", f"失败: {str(e)}")
        return False


def _run_pipeline(run_id: str, pipeline: dict, device_ids: List[str], mode: str):
    """流水线执行主体（运行于独立后台线程）"""
    run = _pipeline_runs.get(run_id)
    if run is None:
        return
    steps = pipeline.get("steps", [])
    try:
        if mode == "parallel":
            failed = set()
            for i, step in enumerate(steps):
                if run["stop"].is_set():
                    break
                active = [d for d in device_ids if d not in failed]
                if not active:
                    break
                label = f"步骤{i + 1}/{len(steps)} {STEP_LABELS.get(step.get('type'), '?')}"
                with ThreadPoolExecutor(max_workers=len(active)) as ex:
                    futures = {ex.submit(_exec_step, d, step, label, run): d for d in active}
                    for fut in as_completed(futures):
                        if not fut.result():
                            failed.add(futures[fut])
            for d in failed:
                _plog(run, d, "流水线", "err", "因步骤失败已退出本次执行")
        else:
            for d in device_ids:
                if run["stop"].is_set():
                    break
                dev_failed = False
                for i, step in enumerate(steps):
                    if run["stop"].is_set():
                        break
                    if dev_failed:
                        _plog(run, d, f"步骤{i + 1}/{len(steps)}", "err", "前序步骤失败，已跳过")
                        continue
                    label = f"步骤{i + 1}/{len(steps)} {STEP_LABELS.get(step.get('type'), '?')}"
                    dev_failed = not _exec_step(d, step, label, run)
                if dev_failed:
                    _plog(run, d, "流水线", "err", "因步骤失败已跳过剩余步骤")
        _plog(run, "全部设备", "流水线", "ok", "执行结束" + ("（已手动停止）" if run["stop"].is_set() else ""))
    except Exception as e:
        _plog(run, "全部设备", "流水线", "err", f"执行异常: {str(e)}")
    finally:
        run["running"] = False


def _check_devices_online(device_ids: List[str]):
    """一次 adb devices 解析出在线集合，返回 (online_list, offline_list)"""
    result = execute_adb_command("adb devices")
    online_set = set()
    if result["success"]:
        for line in result["stdout"].split("\n")[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                online_set.add(parts[0])
    online = [d for d in device_ids if d in online_set]
    offline = [d for d in device_ids if d not in online_set]
    return online, offline


@router.post("/device/pipeline/run")
def run_pipeline(request: PipelineRunRequest):
    """启动流水线（后台线程执行，立即返回 runId 供日志轮询）"""
    try:
        if not request.deviceIds:
            return {"code": 400, "message": "请至少选择一台设备", "data": None}
        if request.mode not in ("parallel", "serial"):
            return {"code": 400, "message": "mode 仅支持 parallel / serial", "data": None}

        _ensure_dirs()
        name = _safe_name(request.name)
        path = PIPELINES_DIR / f"{name}.json"
        if not path.exists():
            return {"code": 404, "message": f"流水线 {name} 不存在，请先保存", "data": None}
        with open(path, "r", encoding="utf-8") as f:
            pipeline = json.load(f)
        error = _validate_steps(pipeline.get("steps"))
        if error:
            return {"code": 400, "message": f"流水线校验失败: {error}", "data": None}

        online, offline = _check_devices_online(request.deviceIds)
        if not online:
            return {"code": 400, "message": "所选设备均不在线", "data": None}

        run_id = uuid.uuid4().hex[:12]
        run = {
            "logs": [],
            "lock": threading.Lock(),
            "stop": threading.Event(),
            "running": True,
        }
        with _runs_lock:
            # 只保留最近 20 次运行的日志，防止内存无限增长
            while len(_pipeline_runs) >= 20:
                oldest = next(iter(_pipeline_runs))
                del _pipeline_runs[oldest]
            _pipeline_runs[run_id] = run

        t = threading.Thread(
            target=_run_pipeline,
            args=(run_id, pipeline, online, request.mode),
            name=f"pipeline-{run_id}",
            daemon=True,
        )
        t.start()

        logger.info(f"Pipeline {name} started: runId={run_id}, devices={online}, mode={request.mode}")
        return {
            "code": 200,
            "message": "success",
            "data": {
                "runId": run_id,
                "online": online,
                "offline": offline,
                "stepCount": len(pipeline.get("steps", [])),
                "mode": request.mode,
            },
        }
    except Exception as e:
        return {"code": 500, "message": f"启动流水线失败: {str(e)}", "data": None}


@router.post("/device/pipeline/logs")
def get_pipeline_logs(request: PipelineLogRequest):
    """按游标增量拉取执行日志（前端每秒轮询）"""
    run = _pipeline_runs.get(request.runId)
    if run is None:
        return {"code": 404, "message": "运行记录不存在或已被清理", "data": None}
    with run["lock"]:
        logs = run["logs"][request.cursor:]
        total = len(run["logs"])
    return {
        "code": 200,
        "message": "success",
        "data": {"logs": logs, "total": total, "running": run["running"]},
    }


@router.post("/device/pipeline/stop")
def stop_pipeline(request: PipelineNameRequest):
    """请求停止运行（runId 通过 name 字段传入）。在当前步骤完成后生效。"""
    run = _pipeline_runs.get(request.name)
    if run is None:
        return {"code": 404, "message": "运行记录不存在或已被清理", "data": None}
    if run["running"]:
        run["stop"].set()
        return {"code": 200, "message": "已发送停止请求，当前步骤完成后生效", "data": None}
    return {"code": 200, "message": "该运行已结束", "data": None}
