"""
生产工具接口 - APK 素材库、设备画面快照、快捷执行脚本与执行日志

快捷执行（/device/pipeline/run-script）把单个模拟脚本包装成单步流水线，
复用 _run_pipeline 执行引擎：按设备并发执行，全程产生带设备号/步骤/级别的日志，
内存缓存，前端按游标增量拉取。
"""
import json
import re
import uuid
import threading
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List

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
APKS_DIR = get_data_path() / "apk"

# 步骤类型与中文标签（日志与前端展示用）
STEP_LABELS = {
    "installApk": "安装APK",
    "simulate": "模拟操作",
    "screenshot": "获取画面",
    "deleteFolder": "删除文件夹",
    "shell": "执行Shell命令",
    "startApp": "启动应用",
    "stopApp": "停止应用",
    "uninstallApp": "卸载应用",
    "keyevent": "模拟按键",
    "volume": "音量控制",
    "reboot": "重启设备",
    "rebootLoader": "重启进Loader",
    "cleanMemory": "清理内存",
}


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


class PipelineNameRequest(BaseModel):
    name: str


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


# ========== 设备画面快照 ==========

class ScreenRequest(BaseModel):
    deviceId: str


class InstallApkToDeviceRequest(BaseModel):
    deviceId: str
    apkName: str


@router.post("/device/pipeline/install-apk-to-device")
def install_apk_to_device(request: InstallApkToDeviceRequest):
    """把 APK 仓库（data/apk/）里的安装包装到指定设备"""
    try:
        apk_name = _safe_name(os.path.splitext(request.apkName)[0]) + ".apk"
        path = APKS_DIR / apk_name
        if not path.exists():
            return {"code": 404, "message": f"APK {apk_name} 不在仓库中", "data": None}
        logger.info(f"Installing {apk_name} to {request.deviceId} from repo")
        result = execute_adb_command(f'adb -s {request.deviceId} install -r "{path}"', timeout=600)
        if not result["success"]:
            return {"code": 500, "message": (result.get("error") or "install failed").strip()[:200], "data": None}
        logger.info(f"Installed {apk_name} to {request.deviceId}")
        return {"code": 200, "message": "success", "data": {"apkName": apk_name, "deviceId": request.deviceId}}
    except Exception as e:
        logger.error(f"从仓库安装 APK 失败: {e}")
        return {"code": 500, "message": f"安装失败: {str(e)}", "data": None}


@router.post("/device/pipeline/screen")
def get_device_screen(request: ScreenRequest):
    """
    截取设备当前画面，返回 JPEG base64（生产工具页 3 列画面网格用）

    raw screencap（~650ms）比 PNG（~4s）快得多；JPEG 质量 70 体积小，
    多设备并发轮询时网络传输更轻。
    """
    try:
        import base64
        import struct
        import numpy as np
        import cv2
        from routes.vision import capture_screen

        arr = capture_screen(request.deviceId)          # BGR (H, W, 3)
        ok, buf = cv2.imencode('.jpg', arr, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return {"code": 500, "message": "JPEG 编码失败", "data": None}
        return {
            "code": 200,
            "message": "success",
            "data": {
                "image": base64.b64encode(buf.tobytes()).decode('ascii'),
                "width": int(arr.shape[1]),
                "height": int(arr.shape[0]),
            },
        }
    except Exception as e:
        logger.error(f"截取设备画面失败: {e}")
        return {"code": 500, "message": f"截取画面失败: {str(e)}", "data": None}


# ========== 快捷执行脚本（不编排流水线，直接跑） ==========

class RunScriptRequest(BaseModel):
    deviceIds: List[str]
    scriptName: str


@router.post("/device/pipeline/run-script")
def run_quick_script(request: RunScriptRequest):
    """
    对多台设备直接执行一个已保存的模拟脚本（复用流水线执行引擎与日志体系）

    内部把脚本包装成单步流水线：[{type: simulate, scriptName: xxx, repeat: 1}]
    """
    try:
        if not request.deviceIds:
            return {"code": 400, "message": "请至少选择一台设备", "data": None}
        script_name = _safe_name(request.scriptName)
        path = SCRIPTS_DIR / f"{script_name}.json"
        if not path.exists():
            return {"code": 404, "message": f"脚本 {script_name} 不存在", "data": None}
        return _start_quick_run(script_name, request.deviceIds)
    except Exception as e:
        return {"code": 500, "message": f"启动脚本执行失败: {str(e)}", "data": None}


def _start_quick_run(script_name: str, device_ids: List[str]):
    """快捷执行的真实启动逻辑：单步 simulate 流水线，复用日志/停止/轮询全套设施"""
    path = SCRIPTS_DIR / f"{script_name}.json"
    with open(path, "r", encoding="utf-8") as f:
        script = json.load(f)

    online, offline = _check_devices_online(device_ids)
    if not online:
        return {"code": 400, "message": "所选设备均不在线", "data": None}

    run_id = uuid.uuid4().hex[:12]
    log_dir = os.path.join(PIPELINE_LOG_ROOT, f"脚本_{script_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "run.log")
    file_handle = open(log_path, "a", encoding="utf-8")

    run = {
        "logs": [],
        "lock": threading.Lock(),
        "stop": threading.Event(),
        "running": True,
        "file_handle": file_handle,
    }
    file_handle.write(f"===== 脚本 [{script_name}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                      f"设备={','.join(online)} =====\n")
    file_handle.flush()

    with _runs_lock:
        while len(_pipeline_runs) >= 20:
            oldest = next(iter(_pipeline_runs))
            del _pipeline_runs[oldest]
        _pipeline_runs[run_id] = run

    # 单步流水线：simulate 步骤
    pipeline = {
        "name": f"quick_{script_name}",
        "steps": [{"type": "simulate", "scriptName": script_name, "repeat": 1}],
    }
    t = threading.Thread(
        target=_run_pipeline,
        args=(run_id, pipeline, online, "parallel"),
        name=f"quick-{run_id}",
        daemon=True,
    )
    t.start()

    logger.info(f"Quick script {script_name} started: runId={run_id}, devices={online}")
    return {
        "code": 200,
        "message": "success",
        "data": {
            "runId": run_id,
            "online": online,
            "offline": offline,
            "stepCount": 1,
            "logDir": log_dir,
        },
    }


# ========== 执行引擎 ==========

class PipelineLogRequest(BaseModel):
    runId: str
    cursor: int = 0


_pipeline_runs = {}
_runs_lock = threading.Lock()

# 执行日志根目录（与日志抓取同一 D:\log 约定）
PIPELINE_LOG_ROOT = r"D:\log"


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
        # 同步写入合并日志文件（D:\log\{流水线名}_{日期}\run.log）
        fh = run.get("file_handle")
        if fh:
            try:
                fh.write(f"[{entry['time']}] [{entry['level'].upper()}] [{device_id}] {step} - {message}\n")
                fh.flush()
            except Exception:
                pass
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

        elif stype == "shell":
            cmd = str(step.get("command", "") or "").strip()
            _plog(run, device_id, step_label, "info", f"$ {cmd}")
            result = execute_adb_command(f'adb -s {device_id} shell "{cmd}"', timeout=300)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "shell command failed").strip())
            output = (result.get("stdout") or "").strip()
            if output:
                for line in output.splitlines()[:20]:
                    _plog(run, device_id, step_label, "info", f"  {line}")
            return True

        elif stype == "deleteFolder":
            target = str(step.get("path", "") or "").strip()
            _plog(run, device_id, step_label, "info", f"删除 {target} ...")
            result = execute_adb_command(f'adb -s {device_id} shell rm -rf "{target}"', timeout=120)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "rm failed").strip())
            return True

        elif stype == "startApp":
            pkg = str(step.get("packageName", "")).strip()
            activity = str(step.get("activity", "") or "").strip()
            if activity:
                _plog(run, device_id, step_label, "info", f"启动 {pkg}/{activity} ...")
                cmd = f'adb -s {device_id} shell am start -n "{pkg}/{activity}"'
            else:
                _plog(run, device_id, step_label, "info", f"启动应用 {pkg} ...")
                cmd = f'adb -s {device_id} shell monkey -p "{pkg}" -c android.intent.category.LAUNCHER 1'
            result = execute_adb_command(cmd, timeout=60)
            if not result["success"]:
                raise RuntimeError((result.get("error") or result.get("stdout") or "start failed").strip())
            out = (result.get("stdout") or "").strip()
            # am start 失败（如 Activity 不存在）时 adb 仍返回成功，需检查输出
            if "Error" in out:
                raise RuntimeError(out.splitlines()[0][:120])
            return True

        elif stype == "stopApp":
            pkg = str(step.get("packageName", "")).strip()
            _plog(run, device_id, step_label, "info", f"停止应用 {pkg} ...")
            result = execute_adb_command(f'adb -s {device_id} shell am force-stop "{pkg}"', timeout=60)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "force-stop failed").strip())
            return True

        elif stype == "uninstallApp":
            pkg = str(step.get("packageName", "")).strip()
            _plog(run, device_id, step_label, "info", f"卸载应用 {pkg} ...")
            result = execute_adb_command(f'adb -s {device_id} uninstall "{pkg}"', timeout=120)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "uninstall failed").strip())
            return True

        elif stype == "keyevent":
            keycode = int(step.get("keycode"))
            repeat = max(1, min(20, int(step.get("repeat", 1) or 1)))
            _plog(run, device_id, step_label, "info", f"发送按键 {keycode} ×{repeat} ...")
            for _ in range(repeat):
                result = execute_adb_command(f"adb -s {device_id} shell input keyevent {keycode}", timeout=30)
                if not result["success"]:
                    raise RuntimeError((result.get("error") or "keyevent failed").strip())
            return True

        elif stype == "volume":
            action = str(step.get("action", "")).strip()
            stream = str(step.get("stream", "") or "").strip() or "media"
            action_label = {"up": "音量+", "down": "音量-", "mute": "静音"}.get(action, action)
            _plog(run, device_id, step_label, "info", f"{action_label}（{stream}）...")
            if action in ("up", "down"):
                key = "KEYCODE_VOLUME_UP" if action == "up" else "KEYCODE_VOLUME_DOWN"
                result = execute_adb_command(f"adb -s {device_id} shell input keyevent {key}", timeout=30)
                if not result["success"]:
                    raise RuntimeError((result.get("error") or "volume key failed").strip())
            else:
                # 静音：复用 ADB工具页的 service call 音频服务方案（root/系统权限失败则报错）
                from routes.adb_utils import get_stream_type
                result = execute_adb_command(
                    f"adb -s {device_id} shell service call audio {7 + int(get_stream_type(stream))} i32 1",
                    timeout=30,
                )
                if not result["success"]:
                    raise RuntimeError((result.get("error") or "mute failed").strip())
            return True

        elif stype == "reboot":
            _plog(run, device_id, step_label, "info", "重启设备 ...")
            result = execute_adb_command(f"adb -s {device_id} reboot", timeout=30)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "reboot failed").strip())
            return True

        elif stype == "rebootLoader":
            _plog(run, device_id, step_label, "info", "重启进 bootloader ...")
            result = execute_adb_command(f"adb -s {device_id} reboot bootloader", timeout=30)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "reboot bootloader failed").strip())
            return True

        elif stype == "cleanMemory":
            _plog(run, device_id, step_label, "info", "清理内存（杀后台缓存进程+释放页缓存）...")
            result = execute_adb_command(f"adb -s {device_id} shell am kill-all", timeout=15)
            if not result["success"]:
                _plog(run, device_id, step_label, "info", "am kill-all 失败，继续尝试释放页缓存")
            execute_adb_command(f'adb -s {device_id} shell "sync; echo 3 > /proc/sys/vm/drop_caches"', timeout=15)
            _plog(run, device_id, step_label, "ok", "清理完成")
            return True

        raise RuntimeError(f"未知步骤类型: {stype}")
    except Exception as e:
        _plog(run, device_id, step_label, "err", f"失败: {str(e)}")
        return False


def _wait_step_delay(step: dict, run: dict, label: str):
    """步骤执行成功后的可选间隔（delayAfter 秒），停止请求可中断"""
    try:
        delay = int(step.get("delayAfter") or 0)
    except (TypeError, ValueError):
        delay = 0
    if delay <= 0:
        return
    _plog(run, "全部设备", label, "info", f"等待 {delay}s 后继续 ...")
    run["stop"].wait(delay)


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
                _wait_step_delay(step, run, label)
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
                    if not dev_failed:
                        _wait_step_delay(step, run, label)
                if dev_failed:
                    _plog(run, d, "流水线", "err", "因步骤失败已跳过剩余步骤")
        _plog(run, "全部设备", "流水线", "ok", "执行结束" + ("（已手动停止）" if run["stop"].is_set() else ""))
    except Exception as e:
        _plog(run, "全部设备", "流水线", "err", f"执行异常: {str(e)}")
    finally:
        run["running"] = False
        fh = run.get("file_handle")
        if fh:
            try:
                fh.close()
            except Exception:
                pass
            run["file_handle"] = None


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
