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
from routes.batch_remote import DeviceSession

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


class ScriptNameRequest(BaseModel):
    name: str


class ScriptRunRequest(BaseModel):
    deviceId: str
    name: str
    repeat: Optional[int] = None  # 不传则用脚本自身保存的 repeat


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

    临时起 scrcpy 会话只用于注入（不取视频流），跑完销毁。
    """
    from config.queue_config import task_queue
    import socket as socket_mod

    # 找空闲端口
    s = socket_mod.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    session = DeviceSession(device_id, port)
    if not session.start():
        raise RuntimeError("设备会话启动失败")

    try:
        steps = script.get("steps", [])
        for round_no in range(1, repeat + 1):
            logger.info(f"Script run round {round_no}/{repeat} on {device_id}")
            for idx, step in enumerate(steps):
                stype = step.get("type")
                count = max(1, int(step.get("count", 1)))
                interval = max(0, int(step.get("interval", 0)))
                for i in range(count):
                    if stype == "tap":
                        session.inject_touch(0, step["x1"], step["y1"], 1080, 2340)
                        session.inject_touch(1, step["x1"], step["y1"], 1080, 2340)
                        time.sleep(0.05)
                    elif stype == "longpress":
                        session.inject_touch(0, step["x1"], step["y1"], 1080, 2340)
                        time.sleep(max(0.1, step.get("duration", 800)) / 1000)
                        session.inject_touch(1, step["x1"], step["y1"], 1080, 2340)
                    elif stype == "swipe":
                        # 滑动：down → 多段 move → up，模拟真实手势
                        x1, y1 = step["x1"], step["y1"]
                        x2, y2 = step.get("x2", x1), step.get("y2", y1)
                        seg = 10
                        session.inject_touch(0, x1, y1, 1080, 2340)
                        for k in range(1, seg + 1):
                            session.inject_touch(
                                2,
                                x1 + (x2 - x1) * k / seg,
                                y1 + (y2 - y1) * k / seg,
                                1080, 2340,
                            )
                            time.sleep(0.01)
                        session.inject_touch(1, x2, y2, 1080, 2340)
                    else:
                        logger.warning(f"Unknown step type: {stype}")
                        break
                    if i < count - 1:
                        time.sleep(interval / 1000)
                # 步与步之间的间隔
                if idx < len(steps) - 1:
                    time.sleep(interval / 1000)
    finally:
        session.stop()
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
