"""
模拟脚本 - 步骤脚本 CRUD 与回放执行

脚本数据结构（保存为 data/scripts/{name}.json）：
{
  "name": "脚本名",
  "repeat": 1,                  # 整个脚本重复轮数
  "steps": [
    {
      "type": "tap",            # tap | swipe | longpress | input
      "x1": 540, "y1": 960,     # tap/longpress 只用 x1,y1；swipe 额外用 x2,y2
      "x2": 540, "y2": 1200,
      "duration": 800,          # longpress 时长(ms)
      "interval": 500,          # 该步执行间隔(ms)，步与步之间的等待
      "count": 1                # 该步执行次数
    },
    {
      "type": "input",          # 输入文字：无坐标，输入到设备当前聚焦的输入框
      "text": "1234",
      "interval": 500,
      "count": 1
    },
    { "type": "installApk",  "apkName": "demo.apk" },
    { "type": "deleteFolder", "path": "/sdcard/xxx" },
    { "type": "startApp",    "packageName": "com.x", "activity": ".Main" },
    { "type": "stopApp",     "packageName": "com.x" },
    { "type": "uninstallApp","packageName": "com.x" },
    { "type": "keyevent",    "keycode": 4 },
    { "type": "volume",      "action": "up", "stream": "media" },
    { "type": "reboot" }
  ]
}

设备动作类步骤（installApk/deleteFolder/应用管理/按键/音量/重启）走 adb 命令，
其余注入类步骤走 scrcpy 会话。

执行（/script/run）：
  临时起一个 scrcpy DeviceSession（复用批量远控的会话实现），
  按步骤顺序注入：每步循环 count 次，步间等待 interval ms；
  全部步骤跑完算一轮，共 repeat 轮。
"""
import base64
import json
import re
import time
import threading

import cv2

from fastapi import APIRouter, File, UploadFile
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
APKS_DIR = get_data_path() / "apk"
TEMPLATES_DIR = get_data_path() / "templates"   # 视觉匹配模板图（等待组件出现）


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


class ScriptRunStepsRequest(BaseModel):
    """直接执行内存中的步骤：不落盘，避免产生 current_xxx.json 临时文件"""
    deviceId: str
    steps: List[dict]
    repeat: int = 1
    videoWidth: Optional[int] = None
    videoHeight: Optional[int] = None


class ScriptImportRequest(BaseModel):
    """导入脚本：content 为脚本 JSON 原文"""
    content: str
    name: Optional[str] = None  # 不传则用脚本内的 name，或按内容生成


# ========== 脚本校验 ==========

# 注入类步骤：走 scrcpy 会话
INJECT_STEP_TYPES = ("tap", "swipe", "longpress", "input")
# 设备动作类步骤：走 adb 命令
ADB_STEP_TYPES = (
    "installApk", "deleteFolder", "startApp", "stopApp", "uninstallApp",
    "keyevent", "volume", "reboot",
)
# 视觉等待：反复截屏搜索模板图，命中才继续
VISION_STEP_TYPES = ("waitFor",)
VALID_STEP_TYPES = INJECT_STEP_TYPES + ADB_STEP_TYPES + VISION_STEP_TYPES


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

        # input 步骤没有坐标（输入到设备当前聚焦的输入框），只校验文本
        if step_type == "input":
            text = step.get("text")
            if not isinstance(text, str) or not text:
                return f"第 {i + 1} 步是输入步骤，text 必须是非空字符串"
            interval = step.get("interval", 0)
            if not isinstance(interval, (int, float)) or interval < 0:
                return f"第 {i + 1} 步的 interval 必须是非负数字"
            count = step.get("count", 1)
            if not isinstance(count, int) or count < 1:
                return f"第 {i + 1} 步的 count 必须是大于 0 的整数"
            continue

        # 设备动作类步骤：无坐标，按各自参数校验
        if step_type in ADB_STEP_TYPES:
            error = _validate_adb_step(step_type, step, i + 1)
            if error:
                return error
            continue

        # 视觉等待步骤：校验模板与超时
        if step_type == "waitFor":
            template = str(step.get("template", "")).strip()
            if not template:
                return f"第 {i + 1} 步是等待组件步骤，未选择模板图"
            if "/" in template or "\\" in template or ".." in template:
                return f"第 {i + 1} 步的模板名不能包含路径分隔符"
            try:
                timeout = float(step.get("timeout", 15))
            except (TypeError, ValueError):
                return f"第 {i + 1} 步的超时时间无效"
            if timeout <= 0 or timeout > 600:
                return f"第 {i + 1} 步的超时时间需在 0-600 秒之间"
            try:
                threshold = float(step.get("threshold", 0.85))
            except (TypeError, ValueError):
                return f"第 {i + 1} 步的匹配阈值无效"
            if not (0.5 <= threshold <= 1.0):
                return f"第 {i + 1} 步的匹配阈值需在 0.5-1.0 之间"
            continue

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


def _validate_adb_step(step_type: str, step: dict, idx: int) -> str:
    """校验设备动作类步骤，返回空串表示通过"""
    if step_type == "installApk":
        if not str(step.get("apkName", "")).strip():
            return f"第 {idx} 步未选择 APK 文件"
        return ""

    if step_type == "deleteFolder":
        path = str(step.get("path", "")).strip()
        if not path:
            return f"第 {idx} 步未填写要删除的文件夹路径"
        # rm -rf 的目标必须限定在用户区，防止误删系统目录
        if not (path.startswith("/sdcard/") or path.startswith("/storage/")):
            return f"第 {idx} 步的删除路径仅允许 /sdcard/ 或 /storage/ 开头"
        return ""

    if step_type in ("startApp", "stopApp", "uninstallApp"):
        pkg = str(step.get("packageName", "")).strip()
        if not pkg:
            return f"第 {idx} 步未填写应用包名"
        if re.search(r'[\s"\'&|;$<>`\\]', pkg):
            return f"第 {idx} 步的包名不能包含空格或特殊字符"
        if step_type == "startApp" and step.get("activity"):
            activity = str(step.get("activity", "")).strip()
            if re.search(r'[\s"\'&|;$<>`\\]', activity):
                return f"第 {idx} 步的 Activity 名不能包含空格或特殊字符"
        return ""

    if step_type == "keyevent":
        try:
            keycode = int(step.get("keycode"))
        except (TypeError, ValueError):
            return f"第 {idx} 步未填写有效的按键键值（数字）"
        if not (0 <= keycode <= 65535):
            return f"第 {idx} 步的键值超出范围 0-65535"
        return ""

    if step_type == "volume":
        if str(step.get("action", "")).strip() not in ("up", "down", "mute"):
            return f"第 {idx} 步的音量动作仅支持 up/down/mute"
        return ""

    if step_type == "reboot":
        return ""

    return ""


def _normalize_steps(steps) -> List[dict]:
    """补全步骤的缺省字段"""
    normalized = []
    for step in steps:
        # input 步骤没有坐标，只保留文本与执行参数
        if step["type"] == "input":
            normalized.append({
                "type": "input",
                "text": step.get("text", ""),
                "interval": step.get("interval", 0),
                "count": step.get("count", 1),
            })
            continue
        # 视觉等待步骤：保留模板与匹配参数
        if step["type"] == "waitFor":
            normalized.append({
                "type": "waitFor",
                "template": step.get("template", ""),
                "threshold": step.get("threshold", 0.85),
                "timeout": step.get("timeout", 15),
                "interval": step.get("interval", 0),
                "count": step.get("count", 1),
            })
            continue
        # 设备动作类步骤：保留各自参数（不带坐标字段）
        if step["type"] in ADB_STEP_TYPES:
            item = {
                "type": step["type"],
                "interval": step.get("interval", 0),
                "count": step.get("count", 1),
            }
            for field in ("apkName", "path", "packageName", "activity", "keycode", "action", "stream"):
                if step.get(field) is not None:
                    item[field] = step.get(field)
            normalized.append(item)
            continue
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


def _exec_adb_step(device_id: str, step: dict, repeat_count: int = 1):
    """
    执行一个设备动作类步骤（走 adb 命令，不需要 scrcpy 会话）

    count>1 时重复执行；interval 是每次之间的等待毫秒数。
    失败抛出 RuntimeError，由调用方（流水线/面板）记录。
    """
    stype = step.get("type")

    for i in range(max(1, repeat_count)):
        if stype == "installApk":
            apk_name = str(step.get("apkName", "")).strip()
            path = APKS_DIR / apk_name
            if not path.exists():
                raise RuntimeError(f"APK {apk_name} 不存在，请先上传到 APK 仓库")
            result = execute_adb_command(f'adb -s {device_id} install -r "{path}"', timeout=600)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "install failed").strip())

        elif stype == "deleteFolder":
            target = str(step.get("path", "")).strip()
            # 兜底拦截：校验层已限制，执行层再确认一次，防止未校验数据进入
            if not (target.startswith("/sdcard/") or target.startswith("/storage/")):
                raise RuntimeError(f"删除路径仅允许 /sdcard/ 或 /storage/ 开头: {target}")
            result = execute_adb_command(f'adb -s {device_id} shell rm -rf "{target}"', timeout=120)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "rm failed").strip())

        elif stype == "startApp":
            pkg = str(step.get("packageName", "")).strip()
            activity = str(step.get("activity", "") or "").strip()
            if activity:
                cmd = f'adb -s {device_id} shell am start -n "{pkg}/{activity}"'
            else:
                cmd = f'adb -s {device_id} shell monkey -p "{pkg}" -c android.intent.category.LAUNCHER 1'
            result = execute_adb_command(cmd, timeout=60)
            if not result["success"]:
                raise RuntimeError((result.get("error") or result.get("stdout") or "start failed").strip())
            out = (result.get("stdout") or "").strip()
            # am start 失败（如 Activity 不存在）时 adb 仍返回成功，需检查输出
            if "Error" in out:
                raise RuntimeError(out.splitlines()[0][:120])

        elif stype == "stopApp":
            pkg = str(step.get("packageName", "")).strip()
            result = execute_adb_command(f'adb -s {device_id} shell am force-stop "{pkg}"', timeout=60)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "force-stop failed").strip())

        elif stype == "uninstallApp":
            pkg = str(step.get("packageName", "")).strip()
            result = execute_adb_command(f'adb -s {device_id} uninstall "{pkg}"', timeout=120)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "uninstall failed").strip())

        elif stype == "keyevent":
            keycode = int(step.get("keycode"))
            result = execute_adb_command(f"adb -s {device_id} shell input keyevent {keycode}", timeout=30)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "keyevent failed").strip())

        elif stype == "volume":
            action = str(step.get("action", "")).strip()
            stream = str(step.get("stream", "") or "").strip() or "media"
            if action in ("up", "down"):
                key = "KEYCODE_VOLUME_UP" if action == "up" else "KEYCODE_VOLUME_DOWN"
                result = execute_adb_command(f"adb -s {device_id} shell input keyevent {key}", timeout=30)
                if not result["success"]:
                    raise RuntimeError((result.get("error") or "volume key failed").strip())
            else:
                # 静音：复用 ADB工具页的 service call 音频服务方案
                from routes.adb_utils import get_stream_type
                result = execute_adb_command(
                    f"adb -s {device_id} shell service call audio {7 + int(get_stream_type(stream))} i32 1",
                    timeout=30,
                )
                if not result["success"]:
                    raise RuntimeError((result.get("error") or "mute failed").strip())

        elif stype == "reboot":
            result = execute_adb_command(f"adb -s {device_id} reboot", timeout=30)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "reboot failed").strip())

        else:
            raise RuntimeError(f"未知设备动作步骤类型: {stype}")

        if i < repeat_count - 1:
            time.sleep(max(0, int(step.get("interval", 0))) / 1000)


def _exec_wait_step(device_id: str, step: dict, base_w: int, base_h: int):
    """
    执行「等待组件出现」步骤

    模板图按录制时的画面分辨率保存。若回放时设备分辨率不同，
    需要把模板按比例缩放到当前屏幕尺寸，否则同一组件在不同分辨率下
    尺寸不匹配会一直等不到（不是点击坐标那种简单平移能解决的）。
    """
    from routes.vision import load_template, wait_for_template, capture_screen

    template_name = str(step.get("template", "")).strip()
    path = TEMPLATES_DIR / template_name
    template = load_template(str(path))

    threshold = float(step.get("threshold", 0.85))
    timeout = float(step.get("timeout", 15))

    # 模板尺寸换算：以录制分辨率为基准，对齐当前屏幕
    try:
        screen_w, screen_h = _current_screen_size(device_id, base_w, base_h)
    except Exception:
        screen_w, screen_h = 0, 0

    if screen_w and screen_h and base_w and base_h and (base_w != screen_w or base_h != screen_h):
        scale_x = screen_w / base_w
        scale_y = screen_h / base_h
        th, tw = template.shape[:2]
        new_w = max(1, int(round(tw * scale_x)))
        new_h = max(1, int(round(th * scale_y)))
        if new_w != tw or new_h != th:
            template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
            logger.info(
                f"模板按分辨率换算: 录制 {base_w}x{base_h} -> 当前 {screen_w}x{screen_h}，"
                f"{tw}x{th} -> {new_w}x{new_h}"
            )

    found, elapsed, score, attempts = wait_for_template(
        device_id, template, threshold=threshold, timeout=timeout
    )
    if not found:
        raise RuntimeError(
            f"等待组件超时：模板 {template_name} 在 {elapsed:.1f}s 内未出现"
            f"（最高分 {score:.3f} < 阈值 {threshold}，尝试 {attempts} 次）"
        )
    logger.info(f"[{device_id}] 等待组件 {template_name} 命中，耗时 {elapsed:.2f}s（分数 {score:.3f}）")


def _current_screen_size(device_id: str, base_w: int, base_h: int):
    """取当前设备屏幕像素尺寸（wm size 解析），失败返回 (0, 0)"""
    result = execute_adb_command(f"adb -s {device_id} shell wm size", timeout=15)
    if not result["success"]:
        return 0, 0
    m = re.search(r'(\d+)x(\d+)', result.get("stdout") or "")
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def _run_script_on_device(device_id: str, script: dict, repeat: int):
    """
    在设备上回放脚本（阻塞，运行于 task_queue 工作线程）

    注入类步骤（tap/swipe/longpress/input）复用共享 scrcpy 会话注入
    （与屏幕预览/批量投屏共用同一会话，避免重复启动 server 互相干扰），
    跑完归还引用。设备动作类步骤（安装/删除/应用管理/按键/音量/重启）
    直接走 adb 命令，不占用会话。

    坐标换算：步骤坐标以「录制时的画面尺寸」为基准（脚本保存了
    videoWidth/videoHeight）。回放时若会话分辨率与基准不同
    （设备转屏 / 流水线对异构设备执行同一脚本），按比例换算到
    当前会话坐标系，保证点击落在录制时的相对位置上。
    """
    from routes.remote_ws import acquire_session, release_session

    steps = script.get("steps", [])
    # 全脚本没有注入类步骤时不需要 scrcpy 会话
    # （设备动作与视觉等待都走 adb，纯这类脚本更快、更稳）
    needs_session = any(s.get("type") in INJECT_STEP_TYPES for s in steps)

    session = None
    w = h = 0
    scale_x = scale_y = 1.0

    if needs_session:
        # wait_first_frame：新建会话时等首帧视频就绪再注入，
        # 避免 server 初始化窗口期吞掉第一步点击（流水线新起会话时必现）
        session = acquire_session(device_id, wait_first_frame=True)
        if session is None:
            raise RuntimeError("设备会话启动失败")

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

    try:
        for round_no in range(1, repeat + 1):
            logger.info(f"Script run round {round_no}/{repeat} on {device_id}")
            for idx, step in enumerate(steps):
                stype = step.get("type")
                count = max(1, int(step.get("count", 1)))
                interval = max(0, int(step.get("interval", 0)))

                # 视觉等待步骤：反复截屏搜索模板，命中才继续
                if stype in VISION_STEP_TYPES:
                    # 基准分辨率优先用脚本保存的录制尺寸，缺失则用会话尺寸
                    base_w = int(script.get("videoWidth") or 0) or w
                    base_h = int(script.get("videoHeight") or 0) or h
                    for _ in range(count):
                        _exec_wait_step(device_id, step, base_w, base_h)
                # 设备动作类步骤走 adb 命令（不依赖 scrcpy 会话），count 在函数内部循环
                elif stype in ADB_STEP_TYPES:
                    _exec_adb_step(device_id, step, count)
                else:
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
                        elif stype == "input":
                            # 输入文字：注入到设备当前聚焦的输入框
                            # （通常紧跟在一个点击步骤之后，点哪个框就往哪个框打字）
                            text = step.get("text") or ""
                            if text:
                                session.inject_text(text)
                                # 给输入法/应用处理 IME 事件留出时间，避免后续步骤抢跑
                                time.sleep(0.1)
                            else:
                                logger.warning("Input step with empty text skipped")
                        else:
                            logger.warning(f"Unknown step type: {stype}")
                            break
                        if i < count - 1:
                            time.sleep(interval / 1000)
                # 步与步之间的间隔
                if idx < len(steps) - 1:
                    time.sleep(interval / 1000)
    finally:
        if session is not None:
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


@router.post("/device/script/run-steps")
def run_steps(request: ScriptRunStepsRequest):
    """
    直接执行传入的步骤（不落盘）

    面板上「执行/重复」按钮走这里：步骤只在内存里，
    不再生成 current_xxx.json 临时文件，脚本目录不会被执行动作污染。
    """
    try:
        if not _check_device_online(request.deviceId):
            return {"code": 400, "message": f"设备 {request.deviceId} 不在线", "data": None}

        error = _validate_steps(request.steps)
        if error:
            return {"code": 400, "message": f"步骤校验失败: {error}", "data": None}

        repeat = max(1, int(request.repeat or 1))
        script = {
            "steps": _normalize_steps(request.steps),
            "repeat": repeat,
        }
        # 坐标换算基准：与保存脚本时一致，保证面板坐标按当前会话比例还原
        if request.videoWidth and request.videoHeight:
            script["videoWidth"] = int(request.videoWidth)
            script["videoHeight"] = int(request.videoHeight)

        from config.queue_config import task_queue
        task_queue.submit_task(
            _run_script_on_device,
            request.deviceId,
            script,
            repeat,
        )
        logger.info(f"Script steps queued on {request.deviceId}, repeat={repeat}, steps={len(script['steps'])}")
        return {
            "code": 200,
            "message": "success",
            "data": {"repeat": repeat, "stepCount": len(script["steps"])},
        }
    except Exception as e:
        return {"code": 500, "message": f"执行脚本失败: {str(e)}", "data": None}


# ========== 视觉匹配模板管理 ==========

class TemplateNameRequest(BaseModel):
    name: str


class TemplateUploadBase64Request(BaseModel):
    """前端从投屏画面裁剪出的模板（base64 PNG）"""
    name: str
    image: str


def _ensure_templates_dir():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/device/script/upload-template")
async def upload_template(template: UploadFile = File(..., description="模板图片(PNG)")):
    """上传视觉匹配模板图到 data/templates/"""
    try:
        _ensure_templates_dir()
        raw_name = os.path.splitext(template.filename or "template")[0]
        filename = _safe_name(raw_name) + ".png"
        path = TEMPLATES_DIR / filename
        with open(path, "wb") as f:
            while True:
                chunk = await template.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        # 立即校验能否解码，避免存进去一个坏图运行时才炸
        from routes.vision import load_template
        img = load_template(str(path))
        size = path.stat().st_size
        logger.info(f"Template uploaded: {path} ({img.shape[1]}x{img.shape[0]}, {size} bytes)")
        return {
            "code": 200,
            "message": "success",
            "data": {"name": filename, "width": img.shape[1], "height": img.shape[0], "size": size},
        }
    except Exception as e:
        logger.error(f"上传模板失败: {e}")
        return {"code": 500, "message": f"上传模板失败: {str(e)}", "data": None}


@router.post("/device/script/save-template")
def save_template(request: TemplateUploadBase64Request):
    """保存前端从画面上裁剪出的模板（base64 PNG）"""
    try:
        _ensure_templates_dir()
        data_uri = request.image or ""
        # 兼容带 data:image/png;base64, 前缀的形式
        if "," in data_uri:
            data_uri = data_uri.split(",", 1)[1]
        raw = base64.b64decode(data_uri)
        filename = _safe_name(request.name) + ".png"
        path = TEMPLATES_DIR / filename
        with open(path, "wb") as f:
            f.write(raw)

        from routes.vision import load_template
        img = load_template(str(path))
        logger.info(f"Template saved from canvas: {path} ({img.shape[1]}x{img.shape[0]})")
        return {
            "code": 200,
            "message": "success",
            "data": {"name": filename, "width": img.shape[1], "height": img.shape[0]},
        }
    except Exception as e:
        logger.error(f"保存模板失败: {e}")
        return {"code": 500, "message": f"保存模板失败: {str(e)}", "data": None}


@router.post("/device/script/list-templates")
def list_templates():
    """列出所有模板图（名称 + 尺寸）"""
    try:
        _ensure_templates_dir()
        from routes.vision import load_template
        items = []
        for f in sorted(TEMPLATES_DIR.glob("*.png")):
            try:
                img = load_template(str(f))
                items.append({
                    "name": f.name,
                    "width": img.shape[1],
                    "height": img.shape[0],
                    "size": f.stat().st_size,
                })
            except Exception:
                continue
        return {"code": 200, "message": "success", "data": items}
    except Exception as e:
        return {"code": 500, "message": f"获取模板列表失败: {str(e)}", "data": None}


@router.post("/device/script/delete-template")
def delete_template(request: TemplateNameRequest):
    """删除模板图"""
    try:
        _ensure_templates_dir()
        name = _safe_name(os.path.splitext(request.name)[0]) + ".png"
        path = TEMPLATES_DIR / name
        if path.exists():
            path.unlink()
            logger.info(f"Template deleted: {path}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"删除模板失败: {str(e)}", "data": None}
