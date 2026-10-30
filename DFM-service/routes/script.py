"""
模拟脚本 - 步骤脚本 CRUD 与回放执行

脚本数据结构（保存为 data/scripts/{name}.json）：
{
  "name": "脚本名",
  "repeat": 1,                  # 整个脚本重复轮数
  "steps": [
    {
      "type": "tap",            # tap | swipe | longpress
      "x1": 540, "y1": 960,     # tap/longpress 只用 x1,y1；swipe 额外用 x2,y2
      "x2": 540, "y2": 1200,
      "duration": 800,          # longpress 时长(ms)
      "interval": 500,          # 该步执行间隔(ms)，步与步之间的等待
      "count": 1                # 该步执行次数
    }
  ]
}

执行（/script/run）：
  临时起一个 scrcpy DeviceSession（复用批量远控的会话实现），
  按步骤顺序注入：每步循环 count 次，步间等待 interval ms；
  全部步骤跑完算一轮，共 repeat 轮。
"""
import json
import re
import time
import threading

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List

from config import logger
from utils.tool_manager import get_data_path
from routes.adb_utils import execute_adb_command

router = APIRouter(
    prefix="/api",
    tags=["模拟脚本"]
)

SCRIPTS_DIR = get_data_path() / "scripts"


def _ensure_scripts_dir():
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    """文件名安全过滤：只保留中英文数字下划线连字符，防止路径穿越"""
    name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', name).strip('_')
    return name or "unnamed"


class ScriptSaveRequest(BaseModel):
    name: str
    repeat: int = 1
    steps: List[dict]
    videoWidth: Optional[int] = None   # 录制时的画面宽（坐标基准）
    videoHeight: Optional[int] = None  # 录制时的画面高


class ScriptNameRequest(BaseModel):
    name: str


class ScriptRunRequest(BaseModel):
    deviceId: str
    name: str
    repeat: Optional[int] = None  # 不传则用脚本自身保存的 repeat


class ScriptImportRequest(BaseModel):
    """导入脚本：content 为脚本 JSON 原文"""
    content: str
    name: Optional[str] = None  # 不传则用脚本内的 name，或按内容生成


# ========== 脚本校验 ==========

VALID_STEP_TYPES = ("tap", "swipe", "longpress")


def _validate_steps(steps) -> str:
    """
    校验步骤列表，返回空串表示通过，否则返回错误信息

    导入的脚本来自外部文件，必须严格校验，避免脏数据进入回放流程。
    """
    if not isinstance(steps, list) or not steps:
        return "脚本 steps 必须是非空数组"

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"第 {i + 1} 步不是对象"
        step_type = step.get("type")
        if step_type not in VALID_STEP_TYPES:
            return f"第 {i + 1} 步的 type 无效: {step_type}（应为 {'/'.join(VALID_STEP_TYPES)}）"

        for field in ("x1", "y1"):
            value = step.get(field)
            if not isinstance(value, (int, float)):
                return f"第 {i + 1} 步的 {field} 必须是数字"

        if step_type == "swipe":
            for field in ("x2", "y2"):
                value = step.get(field)
                if not isinstance(value, (int, float)):
                    return f"第 {i + 1} 步的 {field} 必须是数字（滑动需要 x2/y2）"

        if step_type == "longpress":
            duration = step.get("duration")
            if not isinstance(duration, (int, float)) or duration < 0:
                return f"第 {i + 1} 步的 duration 必须是非负数字"

        interval = step.get("interval", 0)
        if not isinstance(interval, (int, float)) or interval < 0:
            return f"第 {i + 1} 步的 interval 必须是非负数字"

        count = step.get("count", 1)
        if not isinstance(count, int) or count < 1:
            return f"第 {i + 1} 步的 count 必须是大于 0 的整数"

    return ""


def _normalize_steps(steps) -> List[dict]:
    """补全步骤的缺省字段"""
    normalized = []
    for step in steps:
        item = {
            "type": step["type"],
            "x1": step["x1"],
            "y1": step["y1"],
            "interval": step.get("interval", 0),
            "count": step.get("count", 1),
        }
        if step["type"] == "swipe":
            item["x2"] = step.get("x2", step["x1"])
            item["y2"] = step.get("y2", step["y1"])
        if step["type"] == "longpress":
            item["duration"] = step.get("duration", 800)
        normalized.append(item)
    return normalized


# ========== 脚本 CRUD ==========

@router.post("/device/script/save")
def save_script(request: ScriptSaveRequest):
    """保存脚本到 data/scripts/{name}.json"""
    try:
        _ensure_scripts_dir()
        if not request.steps:
            return {"code": 400, "message": "脚本步骤不能为空", "data": None}
        name = _safe_name(request.name)
        data = {
            "name": name,
            "repeat": max(1, request.repeat),
            "steps": request.steps,
        }
        # 记录录制时的画面尺寸：回放时若会话分辨率不同（设备转屏/异构设备），
        # 以此为基准等比换算坐标，保证点击位置与录制时一致
        if request.videoWidth and request.videoHeight:
            data["videoWidth"] = int(request.videoWidth)
            data["videoHeight"] = int(request.videoHeight)
        path = SCRIPTS_DIR / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Script saved: {path}")
        return {"code": 200, "message": "success", "data": {"name": name, "path": str(path)}}
    except Exception as e:
        return {"code": 500, "message": f"保存脚本失败: {str(e)}", "data": None}


@router.post("/device/script/list")
def list_scripts():
    """列出所有脚本（名称 + 概要）"""
    try:
        _ensure_scripts_dir()
        scripts = []
        for f in sorted(SCRIPTS_DIR.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                scripts.append({
                    "name": data.get("name", f.stem),
                    "repeat": data.get("repeat", 1),
                    "stepCount": len(data.get("steps", [])),
                })
            except Exception:
                continue
        return {"code": 200, "message": "success", "data": scripts}
    except Exception as e:
        return {"code": 500, "message": f"获取脚本列表失败: {str(e)}", "data": None}


@router.post("/device/script/load")
def load_script(request: ScriptNameRequest):
    """读取单个脚本完整内容"""
    try:
        _ensure_scripts_dir()
        name = _safe_name(request.name)
        path = SCRIPTS_DIR / f"{name}.json"
        if not path.exists():
            return {"code": 404, "message": f"脚本 {name} 不存在", "data": None}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:
        return {"code": 500, "message": f"读取脚本失败: {str(e)}", "data": None}


@router.post("/device/script/delete")
def delete_script(request: ScriptNameRequest):
    """删除脚本文件"""
    try:
        _ensure_scripts_dir()
        name = _safe_name(request.name)
        path = SCRIPTS_DIR / f"{name}.json"
        if path.exists():
            path.unlink()
            logger.info(f"Script deleted: {path}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"删除脚本失败: {str(e)}", "data": None}


@router.post("/device/script/import")
def import_script(request: ScriptImportRequest):
    """
    导入脚本：解析 JSON 原文，校验后存入 data/scripts/{name}.json

    导入来源可能是外部文件，内容不可信，必须先校验再落盘。
    """
    try:
        content = (request.content or "").strip()
        if not content:
            return {"code": 400, "message": "脚本内容为空", "data": None}

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return {"code": 400, "message": f"JSON 解析失败: {str(e)}", "data": None}

        if not isinstance(data, dict):
            return {"code": 400, "message": "脚本根节点必须是对象", "data": None}

        steps = data.get("steps")
        error = _validate_steps(steps)
        if error:
            return {"code": 400, "message": f"脚本校验失败: {error}", "data": None}

        repeat = data.get("repeat", 1)
        if not isinstance(repeat, int) or repeat < 1:
            repeat = 1

        # 名称优先级：请求参数 > 脚本内 name > 按时间戳生成
        raw_name = request.name or data.get("name") or f"imported_{int(time.time() * 1000)}"
        name = _safe_name(str(raw_name))

        _ensure_scripts_dir()
        target = SCRIPTS_DIR / f"{name}.json"
        existed = target.exists()

        script = {
            "name": name,
            "repeat": repeat,
            "steps": _normalize_steps(steps),
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

        logger.info(f"Script imported: {target}")
        return {
            "code": 200,
            "message": "success",
            "data": {
                "name": name,
                "path": str(target),
                "stepCount": len(script["steps"]),
                "repeat": repeat,
                "overwritten": existed,
            },
        }
    except Exception as e:
        logger.error(f"导入脚本失败: {e}")
        return {"code": 500, "message": f"导入脚本失败: {str(e)}", "data": None}


# ========== 脚本执行 ==========

def _check_device_online(device_id: str) -> bool:
    result = execute_adb_command("adb devices")
    if result["success"]:
        for line in result["stdout"].split('\n')[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == device_id and parts[1] == "device":
                return True
    return False


def _run_script_on_device(device_id: str, script: dict, repeat: int):
    """
    在设备上回放脚本（阻塞，运行于 task_queue 工作线程）

    复用共享 scrcpy 会话注入（与屏幕预览/批量投屏共用同一会话，
    避免重复启动 server 互相干扰），跑完归还引用。

    坐标换算：步骤坐标以「录制时的画面尺寸」为基准（脚本保存了
    videoWidth/videoHeight）。回放时若会话分辨率与基准不同
    （设备转屏 / 流水线对异构设备执行同一脚本），按比例换算到
    当前会话坐标系，保证点击落在录制时的相对位置上。
    """
    from routes.remote_ws import acquire_session, release_session

    # wait_first_frame：新建会话时等首帧视频就绪再注入，
    # 避免 server 初始化窗口期吞掉第一步点击（流水线新起会话时必现）
    session = acquire_session(device_id, wait_first_frame=True)
    if session is None:
        raise RuntimeError("设备会话启动失败")

    try:
        # 会话当前分辨率
        w, h = session.video_width, session.video_height
        # 录制时基准（未保存则视为与会话一致，行为向后兼容）
        base_w = int(script.get("videoWidth") or 0)
        base_h = int(script.get("videoHeight") or 0)
        if not (base_w and base_h) or (base_w == w and base_h == h):
            scale_x = scale_y = 1.0
        else:
            scale_x = w / base_w
            scale_y = h / base_h
            logger.info(
                f"Script resolution mismatch on {device_id}: "
                f"recorded {base_w}x{base_h} -> session {w}x{h}, scaling ({scale_x:.3f}, {scale_y:.3f})"
            )

        def _sx(v) -> float:
            return v * scale_x

        def _sy(v) -> float:
            return v * scale_y

        steps = script.get("steps", [])
        for round_no in range(1, repeat + 1):
            logger.info(f"Script run round {round_no}/{repeat} on {device_id}")
            for idx, step in enumerate(steps):
                stype = step.get("type")
                count = max(1, int(step.get("count", 1)))
                interval = max(0, int(step.get("interval", 0)))
                for i in range(count):
                    if stype == "tap":
                        session.inject_touch(0, _sx(step["x1"]), _sy(step["y1"]), w, h)
                        session.inject_touch(1, _sx(step["x1"]), _sy(step["y1"]), w, h)
                        time.sleep(0.05)
                    elif stype == "longpress":
                        session.inject_touch(0, _sx(step["x1"]), _sy(step["y1"]), w, h)
                        time.sleep(max(0.1, step.get("duration", 800)) / 1000)
                        session.inject_touch(1, _sx(step["x1"]), _sy(step["y1"]), w, h)
                    elif stype == "swipe":
                        # 滑动：down → 多段 move → up，模拟真实手势
                        x1, y1 = _sx(step["x1"]), _sy(step["y1"])
                        x2, y2 = _sx(step.get("x2", step["x1"])), _sy(step.get("y2", step["y1"]))
                        seg = 10
                        session.inject_touch(0, x1, y1, w, h)
                        for k in range(1, seg + 1):
                            session.inject_touch(
                                2,
                                x1 + (x2 - x1) * k / seg,
                                y1 + (y2 - y1) * k / seg,
                                w, h,
                            )
                            time.sleep(0.01)
                        session.inject_touch(1, x2, y2, w, h)
                    else:
                        logger.warning(f"Unknown step type: {stype}")
                        break
                    if i < count - 1:
                        time.sleep(interval / 1000)
                # 步与步之间的间隔
                if idx < len(steps) - 1:
                    time.sleep(interval / 1000)
    finally:
        release_session(device_id, session)
        logger.info(f"Script run finished on {device_id}")


@router.post("/device/script/run")
def run_script(request: ScriptRunRequest):
    """执行脚本（异步入队，立即返回）"""
    try:
        if not _check_device_online(request.deviceId):
            return {"code": 400, "message": f"设备 {request.deviceId} 不在线", "data": None}

        _ensure_scripts_dir()
        name = _safe_name(request.name)
        path = SCRIPTS_DIR / f"{name}.json"
        if not path.exists():
            return {"code": 404, "message": f"脚本 {name} 不存在", "data": None}
        with open(path, "r", encoding="utf-8") as f:
            script = json.load(f)

        repeat = request.repeat if request.repeat is not None else script.get("repeat", 1)
        repeat = max(1, int(repeat))

        from config.queue_config import task_queue
        task_queue.submit_task(
            _run_script_on_device,
            request.deviceId,
            script,
            repeat,
        )
        logger.info(f"Script {name} queued on {request.deviceId}, repeat={repeat}")
        return {
            "code": 200,
            "message": "success",
            "data": {"name": name, "repeat": repeat, "stepCount": len(script.get("steps", []))},
        }
    except Exception as e:
        return {"code": 500, "message": f"执行脚本失败: {str(e)}", "data": None}
