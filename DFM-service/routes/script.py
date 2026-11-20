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
    { "type": "reboot" },
    { "type": "tts",         "text": "开始测试" },
    { "type": "accClick",    "id": "com.x:id/btn", "text": "登录", "desc": "" },    { "type": "condition", "kind": "appInstalled", "packageName": "com.x" }
  ]
}

设备动作类步骤（installApk/deleteFolder/应用管理/按键/音量/重启）走 adb 命令，
TTS 步骤走探针帧协议（0x01）；accClick 步骤走探针 0x0D，设备端在无障碍组件树
中按 id/text/desc 匹配节点并直接执行 ACTION_CLICK（不走坐标注入）；其余注入类
步骤走 scrcpy 会话。判断步骤（condition）
求一次条件后按真/假分支动作（继续 / 跳到第 N 步 / 终止 / 判失败）决定下一步，
走 adb 或截屏，不占会话。

执行（/script/run）：
  临时起一个 scrcpy DeviceSession（复用批量远控的会话实现），
  按步骤顺序注入：每步循环 count 次，步间等待 interval ms；
  全部步骤跑完算一轮，共 repeat 轮。
"""
import base64
import io
import json
import os
import re
import time
import random
import asyncio
import threading
import zipfile

import cv2

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel
from typing import Optional, List

from config import logger
from utils.tool_manager import get_data_path
from routes.adb_utils import execute_adb_command


class DeviceIdRequest(BaseModel):
    deviceId: str

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


class ScriptExportRequest(BaseModel):
    """导出脚本包：把脚本 JSON + 引用的 APK/模板图 打成 zip"""
    name: str


class ScriptImportZipResult(BaseModel):
    """（仅类型参考）zip 导入结果"""
    scriptName: Optional[str] = None
    apkNames: Optional[List[str]] = None
    templateNames: Optional[List[str]] = None


# ========== 脚本校验 ==========

# 注入类步骤：走 scrcpy 会话
INJECT_STEP_TYPES = ("tap", "swipe", "longpress", "input")
# 设备动作类步骤：走 adb 命令或探针帧（不依赖 scrcpy 会话）
ADB_STEP_TYPES = (
    "installApk", "deleteFolder", "startApp", "stopApp", "uninstallApp",
    "keyevent", "volume", "reboot", "tts", "accClick",
)
# 视觉等待：反复截屏搜索模板图，命中才继续
VISION_STEP_TYPES = ("waitFor",)
# 判断步骤：求一次条件，按真/假分支动作决定下一步
CONDITION_STEP_TYPES = ("condition",)
CONDITION_KINDS = ("appInstalled", "template", "shell", "time", "accClick")
BRANCH_ACTIONS = ("continue", "goto", "stop", "fail")
MAX_SCRIPT_JUMPS = 100  # 每轮脚本的跳转次数上限，防止分支构成死循环
VALID_STEP_TYPES = INJECT_STEP_TYPES + ADB_STEP_TYPES + VISION_STEP_TYPES + CONDITION_STEP_TYPES


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

        # 判断步骤：按子类型校验条件参数与分支动作
        if step_type in CONDITION_STEP_TYPES:
            error = _validate_condition_step(step, i + 1, len(steps))
            if error:
                return error
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

    if step_type == "tts":
        text = step.get("text")
        if not isinstance(text, str) or not text.strip():
            return f"第 {idx} 步未填写要朗读的文本"
        if len(text.encode("utf-8")) > 60000:
            return f"第 {idx} 步的朗读文本过长（单帧上限 65535 字节）"
        return ""

    if step_type == "accClick":
        # 按无障碍组件树匹配节点后点击其中心：id/text/desc 至少填一项（多条件同时满足）
        # 录制步骤带 x/y 兜底坐标：动态文本（时间组件等）回放时匹配不到，按坐标兜底点击
        filled = 0
        for field in ("id", "text", "desc"):
            value = step.get(field)
            if value is not None and str(value).strip():
                if len(str(value)) > 200:
                    return f"第 {idx} 步的 {field} 匹配条件过长（上限 200 字符）"
                filled += 1
        if filled == 0:
            if step.get("x") is None or step.get("y") is None:
                return f"第 {idx} 步的无障碍点击需填写 id/text/desc 至少一项匹配条件"
        return ""

    return ""


def _parse_clock(value: str):
    """把 HH:mm / HH:mm:ss 解析成当天秒数，格式非法返回 None"""
    m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', str(value).strip())
    if not m:
        return None
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if hh > 23 or mm > 59 or ss > 59:
        return None
    return hh * 3600 + mm * 60 + ss


def _validate_condition_step(step: dict, idx: int, total: int) -> str:
    """校验判断步骤，返回空串表示通过"""
    kind = step.get("kind")
    if kind not in CONDITION_KINDS:
        return f"第 {idx} 步的判断类型无效: {kind}（应为 {'/'.join(CONDITION_KINDS)}）"

    if not isinstance(step.get("negate", False), bool):
        return f"第 {idx} 步的 negate 必须是布尔值"

    for label in ("onTrue", "onFalse"):
        branch = step.get(label, {"action": "continue"})
        if not isinstance(branch, dict):
            return f"第 {idx} 步的 {label} 必须是对象"
        action = branch.get("action", "continue")
        if action not in BRANCH_ACTIONS:
            return f"第 {idx} 步的 {label} 动作无效: {action}（应为 {'/'.join(BRANCH_ACTIONS)}）"
        if action == "goto":
            target = branch.get("target")
            if not isinstance(target, int) or isinstance(target, bool) or not (idx < target <= total):
                return f"第 {idx} 步的 {label} 跳转目标必须是第 {idx + 1}-{total} 步之间的序号"

    if kind == "appInstalled":
        pkg = str(step.get("packageName", "")).strip()
        if not pkg:
            return f"第 {idx} 步未填写应用包名"
        if re.search(r'[\s"\'&|;$<>`\\]', pkg):
            return f"第 {idx} 步的包名不能包含空格或特殊字符"
    elif kind == "template":
        template = str(step.get("template", "")).strip()
        if not template:
            return f"第 {idx} 步是判断组件步骤，未选择模板图"
        if "/" in template or "\\" in template or ".." in template:
            return f"第 {idx} 步的模板名不能包含路径分隔符"
        try:
            threshold = float(step.get("threshold", 0.85))
        except (TypeError, ValueError):
            return f"第 {idx} 步的匹配阈值无效"
        if not (0.5 <= threshold <= 1.0):
            return f"第 {idx} 步的匹配阈值需在 0.5-1.0 之间"
    elif kind == "shell":
        cmd = str(step.get("command", "")).strip()
        if not cmd:
            return f"第 {idx} 步未填写 Shell 命令"
        lowered = cmd.lower()
        if lowered.startswith("adb ") or lowered.startswith("adb.exe "):
            return f"第 {idx} 步的 Shell 命令不要带 adb 前缀，命令会在设备 shell 中执行"
        if '"' in cmd:
            return f"第 {idx} 步的 Shell 命令不能包含双引号"
        if not str(step.get("expected", "")):
            return f"第 {idx} 步未填写断言包含的字符串"
    elif kind == "time":
        target_time = str(step.get("targetTime", "")).strip()
        if _parse_clock(target_time) is None:
            return f"第 {idx} 步的目标时刻无效: {target_time}（应为 HH:mm 或 HH:mm:ss）"
        try:
            timeout = float(step.get("timeout", 3600))
        except (TypeError, ValueError):
            return f"第 {idx} 步的等待超时无效"
        if timeout <= 0 or timeout > 86400:
            return f"第 {idx} 步的等待超时需在 0-86400 秒之间"
        interval = step.get("interval", 1000)
        if not isinstance(interval, (int, float)) or interval < 0:
            return f"第 {idx} 步的轮询间隔必须是非负数字"
    elif kind == "accClick":
        # 无障碍点击判断：id/text/desc 至少一项，用于在无障碍树中匹配可点击节点
        acc_id = str(step.get("id", "") or "").strip()
        acc_text = str(step.get("text", "") or "").strip()
        acc_desc = str(step.get("desc", "") or "").strip()
        if not (acc_id or acc_text or acc_desc):
            return f"第 {idx} 步的无障碍点击条件需至少填写 resource-id / 文本 / 内容描述 一项"
        for label, value in (("resource-id", acc_id), ("文本", acc_text), ("内容描述", acc_desc)):
            if len(value) > 200:
                return f"第 {idx} 步的无障碍点击条件 {label} 过长（≤200 字符）"

    return ""


def _normalize_branch(branch) -> dict:
    """分支动作归一化：非 goto 不带 target，避免脏字段落盘"""
    if not isinstance(branch, dict):
        return {"action": "continue"}
    action = branch.get("action", "continue")
    if action == "goto":
        return {"action": "goto", "target": int(branch.get("target", 0))}
    return {"action": action}


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
            for field in ("apkName", "path", "packageName", "activity", "keycode", "action", "stream", "text", "id", "desc", "x", "y"):
                if step.get(field) is not None:
                    item[field] = step.get(field)
            normalized.append(item)
            continue
        # 判断步骤：只保留已校验过的条件与分支字段
        if step["type"] in CONDITION_STEP_TYPES:
            item = {
                "type": "condition",
                "kind": step.get("kind"),
                "negate": bool(step.get("negate", False)),
                "onTrue": _normalize_branch(step.get("onTrue")),
                "onFalse": _normalize_branch(step.get("onFalse")),
                "interval": step.get("interval", 1000),
                "count": 1,
            }
            for field in ("packageName", "template", "threshold", "command", "expected", "targetTime", "timeout", "id", "text", "desc"):
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

# 导出包内各资源的子目录名（zip 内的固定布局）
EXPORT_APK_DIR = "apk"
EXPORT_TEMPLATE_DIR = "templates"
# 导出包签名文件名与校验密钥：导入时验证包由本工具导出且未被篡改
EXPORT_SIGNATURE_MEMBER = "signature.txt"
EXPORT_SECRET = b"DFM-Android-Toolkit::script-bundle::v1"

import hmac
import hashlib


def _bundle_signature(manifest: str) -> str:
    """HMAC-SHA256(manifest 文本) 的十六进制签名，manifest 为逐行 '成员名 字节数 sha256' 清单"""
    return hmac.new(EXPORT_SECRET, manifest.encode("utf-8"), hashlib.sha256).hexdigest()


def _manifest_line(filename: str, file_bytes: bytes) -> str:
    """生成清单一行：成员名 字节数 sha256（sha256 基于解压后的真实字节）"""
    return f"{filename} {len(file_bytes)} {hashlib.sha256(file_bytes).hexdigest()}"


def _script_referenced_files(script: dict) -> tuple[List[str], List[str]]:
    """
    扫描脚本步骤，收集引用的 APK 文件名与模板图文件名（去重、保持出现顺序）

    返回 (apk_names, template_names)
    """
    apk_names: List[str] = []
    template_names: List[str] = []
    for step in script.get("steps", []):
        if not isinstance(step, dict):
            continue
        if step.get("type") == "installApk":
            apk = str(step.get("apkName", "")).strip()
            if apk and apk not in apk_names:
                apk_names.append(apk)
        elif step.get("type") == "waitFor" or (
            step.get("type") == "condition" and step.get("kind") == "template"
        ):
            tpl = str(step.get("template", "")).strip()
            if tpl and tpl not in template_names:
                template_names.append(tpl)
    return apk_names, template_names


@router.post("/device/script/export-zip")
def export_script_zip(request: ScriptExportRequest):
    """
    导出脚本为可迁移的 zip 包：脚本 JSON + 它引用的全部 APK/模板图

    zip 布局：
      script.json          脚本原文（与 data/scripts/{name}.json 完全一致）
      apk/{apkName}        脚本引用的 APK（installApk 步骤）
      templates/{tpl}.png  脚本引用的模板图（waitFor 步骤）
      signature.txt        HMAC 签名（对以上全部成员的清单），导入时强制校验

    zip 写到 D:\\log\\script_exports\\{name}.zip，前端提示保存位置。
    """
    try:
        _ensure_scripts_dir()
        name = _safe_name(request.name)
        path = SCRIPTS_DIR / f"{name}.json"
        if not path.exists():
            return {"code": 404, "message": f"脚本 {name} 不存在", "data": None}

        with open(path, "r", encoding="utf-8") as f:
            script = json.load(f)

        apk_names, template_names = _script_referenced_files(script)

        # 先收集缺失的资源，全部列出后一次性报错，避免导出一个不完整的包
        missing = []
        apk_paths: List = []
        for apk in apk_names:
            p = APKS_DIR / apk
            if p.exists():
                apk_paths.append(p)
            else:
                missing.append(f"APK {apk}")
        tpl_paths: List = []
        for tpl in template_names:
            p = TEMPLATES_DIR / tpl
            if p.exists():
                tpl_paths.append(p)
            else:
                missing.append(f"模板图 {tpl}")
        if missing:
            return {"code": 400, "message": "以下资源缺失，请先补齐再导出: " + "、".join(missing), "data": None}

        export_root = r"D:\log\script_exports"
        os.makedirs(export_root, exist_ok=True)
        zip_path = os.path.join(export_root, f"{name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 用 writestr 写入（而非 zf.write），便于对每个成员的真实字节做 sha256 签名
            members: List[tuple] = []  # (arcname, bytes)
            script_bytes = path.read_bytes()
            zf.writestr("script.json", script_bytes)
            members.append(("script.json", script_bytes))
            for p in apk_paths:
                arc = f"{EXPORT_APK_DIR}/{p.name}"
                b = p.read_bytes()
                zf.writestr(arc, b)
                members.append((arc, b))
            for p in tpl_paths:
                arc = f"{EXPORT_TEMPLATE_DIR}/{p.name}"
                b = p.read_bytes()
                zf.writestr(arc, b)
                members.append((arc, b))
            # 对全部成员做内容签名（成员名+字节数+sha256），任何篡改都会导致签名不匹配
            manifest = "\n".join(_manifest_line(arc, b) for arc, b in members)
            zf.writestr(EXPORT_SIGNATURE_MEMBER, _bundle_signature(manifest))

        size = os.path.getsize(zip_path)
        logger.info(f"Script exported: {zip_path} (apk={len(apk_paths)}, templates={len(tpl_paths)}, {size} bytes)")
        return {
            "code": 200,
            "message": "success",
            "data": {
                "path": zip_path,
                "size": size,
                "apkNames": [p.name for p in apk_paths],
                "templateNames": [p.name for p in tpl_paths],
            },
        }
    except Exception as e:
        logger.error(f"导出脚本包失败: {e}")
        return {"code": 500, "message": f"导出脚本包失败: {str(e)}", "data": None}


@router.post("/device/script/import-zip")
async def import_script_zip(script: UploadFile = File(..., description="脚本导出包(.zip)")):
    """
    导入脚本 zip 包：落脚本 JSON + 把包内 apk/templates 资源还原到对应目录

    与 /script/import 相同的校验链：内容不可信，先校验再落盘。
    """
    try:
        raw = await script.read()
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return {"code": 400, "message": "不是有效的 zip 文件", "data": None}

        with zf:
            names = zf.namelist()

            # 1) 签名校验：只接受由本工具导出且未被篡改的包（成员名+字节数+sha256）
            if EXPORT_SIGNATURE_MEMBER not in names:
                return {"code": 400, "message": "该 zip 不是本工具导出的脚本包（缺少签名），拒绝导入", "data": None}
            try:
                sig_in_zip = zf.read(EXPORT_SIGNATURE_MEMBER).decode("utf-8").strip()
            except Exception:
                return {"code": 400, "message": "签名文件损坏，无法校验", "data": None}
            manifest = "\n".join(
                _manifest_line(info.filename, zf.read(info.filename))
                for info in zf.infolist()
                if info.filename != EXPORT_SIGNATURE_MEMBER
            )
            if not hmac.compare_digest(sig_in_zip, _bundle_signature(manifest)):
                return {"code": 400, "message": "签名校验失败：包内容已被篡改或不是本工具导出，拒绝导入", "data": None}

            # 2) 定位脚本 JSON（固定名 script.json）
            script_member = "script.json" if "script.json" in names else None
            if script_member is None:
                json_members = [n for n in names if n.lower().endswith(".json") and "/" not in n]
                if len(json_members) == 1:
                    script_member = json_members[0]
            if script_member is None:
                return {"code": 400, "message": "包内未找到 script.json", "data": None}

            try:
                data = json.loads(zf.read(script_member).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                return {"code": 400, "message": f"脚本 JSON 解析失败: {str(e)}", "data": None}

            if not isinstance(data, dict):
                return {"code": 400, "message": "脚本根节点必须是对象", "data": None}

            steps = data.get("steps")
            error = _validate_steps(steps)
            if error:
                return {"code": 400, "message": f"脚本校验失败: {error}", "data": None}

            repeat = data.get("repeat", 1)
            if not isinstance(repeat, int) or repeat < 1:
                repeat = 1

            raw_name = data.get("name") or os.path.splitext(os.path.basename(script_member))[0]
            name = _safe_name(str(raw_name))

            # 还原资源：只收 apk/ 与 templates/ 顶层目录下的文件，防路径穿越
            imported_apks: List[str] = []
            imported_templates: List[str] = []
            _ensure_templates_dir()
            for member in names:
                if member.endswith("/"):
                    continue
                parts = member.split("/")
                if len(parts) != 2:
                    continue  # 只认顶层 apk/xxx、templates/xxx，忽略嵌套与杂项
                folder, filename = parts
                filename = os.path.basename(filename)  # 双重保险：去掉任何路径成分
                if not filename:
                    continue
                if folder == EXPORT_APK_DIR and filename.lower().endswith(".apk"):
                    target_dir = APKS_DIR
                    imported_apks.append(filename)
                elif folder == EXPORT_TEMPLATE_DIR and filename.lower().endswith(".png"):
                    target_dir = TEMPLATES_DIR
                    imported_templates.append(filename)
                else:
                    continue
                target_dir.mkdir(parents=True, exist_ok=True)
                with open(target_dir / filename, "wb") as f:
                    f.write(zf.read(member))

            # 资源就位后再落脚本，保证导入完成即可执行
            _ensure_scripts_dir()
            target = SCRIPTS_DIR / f"{name}.json"
            existed = target.exists()
            normalized = {
                "name": name,
                "repeat": repeat,
                "steps": _normalize_steps(steps),
            }
            with open(target, "w", encoding="utf-8") as f:
                json.dump(normalized, f, ensure_ascii=False, indent=2)

        logger.info(f"Script zip imported: {target} (apk={len(imported_apks)}, templates={len(imported_templates)})")
        return {
            "code": 200,
            "message": "success",
            "data": {
                "name": name,
                "path": str(target),
                "stepCount": len(normalized["steps"]),
                "repeat": repeat,
                "overwritten": existed,
                "apkNames": imported_apks,
                "templateNames": imported_templates,
            },
        }
    except Exception as e:
        logger.error(f"导入脚本包失败: {e}")
        return {"code": 500, "message": f"导入脚本包失败: {str(e)}", "data": None}


def _check_device_online(device_id: str) -> bool:
    result = execute_adb_command("adb devices")
    if result["success"]:
        for line in result["stdout"].split('\n')[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == device_id and parts[1] == "device":
                return True
    return False


def _check_package_installed(device_id: str, pkg: str) -> bool:
    """检查设备上是否安装了指定包（pm path 对不存在的包返回空输出且 exit 1）"""
    result = execute_adb_command(f'adb -s {device_id} shell pm path "{pkg}"', timeout=30)
    return bool(result["success"] and (result.get("stdout") or "").strip())


def _resolve_step_text(text: str, seq: int, round_no: int) -> str:
    """
    展开步骤文本里的变量；脚本里保留占位符，执行时才生成实际值。

    {执行次数}   → 当前第几次执行（从 1 递增，seq 由调用方传入）
    {执行轮数}   → 当前第几轮（从 1 递增，round_no 由调用方传入）
    {6位随机数}  → 6 位随机数字，n 取 1~18，每次调用重新生成，允许前导 0
    其它写法     → 原样保留
    """
    text = text.replace("{执行次数}", str(seq))
    text = text.replace("{执行轮数}", str(round_no))

    def _random_digits(m):
        n = int(m.group(1))
        if not 1 <= n <= 18:
            return m.group(0)
        return "".join(str(random.randint(0, 9)) for _ in range(n))

    return re.sub(r"\{(\d+)位随机数\}", _random_digits, text)


def _exec_adb_step(device_id: str, step: dict, repeat_count: int = 1, round_no: int = 1):
    """
    执行一个设备动作类步骤（走 adb 命令或探针帧，不需要 scrcpy 会话）

    count>1 时重复执行；interval 是每次之间的等待毫秒数。
    round_no 是脚本当前执行到第几轮，供 {执行轮数} 展开。
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
            # 保底：force-stop/monkey 对不存在的包静默成功，先确认装了没有
            if not _check_package_installed(device_id, pkg):
                raise RuntimeError(f"设备未安装应用 {pkg}，无法启动")
            activity = str(step.get("activity", "") or "").strip()
            if activity:
                cmd = f'adb -s {device_id} shell am start -n "{pkg}/{activity}"'
            else:
                cmd = f'adb -s {device_id} shell monkey -p "{pkg}" -c android.intent.category.LAUNCHER 1'
            result = execute_adb_command(cmd, timeout=60)
            combined = ((result.get("stdout") or "") + (result.get("stderr") or "")).strip()
            # adb 对启动失败经常宿主侧 exit 0，错误只在输出里（Error type 3 / Error: ...）
            if not result["success"] and not combined:
                raise RuntimeError("start failed")
            if "Error" in combined:
                raise RuntimeError(combined.splitlines()[0][:120])

        elif stype == "stopApp":
            pkg = str(step.get("packageName", "")).strip()
            # 保底：force-stop 对不存在的包 exit 0 静默返回，必须先查
            if not _check_package_installed(device_id, pkg):
                raise RuntimeError(f"设备未安装应用 {pkg}，无法停止")
            result = execute_adb_command(f'adb -s {device_id} shell am force-stop "{pkg}"', timeout=60)
            if not result["success"]:
                raise RuntimeError((result.get("error") or "force-stop failed").strip())

        elif stype == "uninstallApp":
            pkg = str(step.get("packageName", "")).strip()
            # 保底：先查有没有装，报错比 DELETE_FAILED_INTERNAL_ERROR 清晰
            if not _check_package_installed(device_id, pkg):
                raise RuntimeError(f"设备未安装应用 {pkg}，无法卸载")
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

        elif stype == "tts":
            # TTS 步骤走探针帧协议（0x01），与探针工具页一样复用已安装的探针服务
            from routes.probe import run_probe, CMD_TTS

            text = _resolve_step_text(str(step.get("text", "")), i + 1, round_no).strip()
            if not text:
                raise RuntimeError("TTS 步骤未填写朗读文本")
            d = run_probe(device_id, False, CMD_TTS, {"text": text})
            if not d.get("connected"):
                raise RuntimeError(f"探针通讯失败: {d.get('reason', '未知原因')}")
            response = d.get("response") or {}
            if not isinstance(response, dict) or response.get("code") != 200:
                message = (response.get("message") if isinstance(response, dict) else None) or "设备返回异常"
                raise RuntimeError(f"TTS 朗读失败: {message}")

        elif stype == "accClick":
            # 无障碍点击：经探针 0x0D 让设备端直接对匹配节点执行 ACTION_CLICK（不走坐标注入）
            from routes.probe import run_probe

            _acc_id = str(step.get("id", "") or "").strip()
            _acc_text = str(step.get("text", "") or "").strip()
            _acc_desc = str(step.get("desc", "") or "").strip()
            _acc_data = {}
            if _acc_id:
                _acc_data["id"] = _acc_id
            if _acc_text:
                _acc_data["text"] = _acc_text
            if _acc_desc:
                _acc_data["desc"] = _acc_desc
            d = run_probe(device_id, False, 0x0D, _acc_data)
            if not d.get("connected"):
                raise RuntimeError(f"探针通讯失败: {d.get('reason', '未知原因')}")
            response = d.get("response") or {}
            if not isinstance(response, dict) or response.get("code") != 200:
                message = (response.get("message") if isinstance(response, dict) else None) or "设备返回异常"
                # 树中匹配失败且录制时带了点击坐标（x/y 为录制时的设备显示坐标）：
                # 动态文本（如时间组件）回放时会变，按坐标兜底点击。纯 ADB 脚本没有
                # scrcpy 会话（_screen_w/_h 为 0），实时取当前屏幕尺寸；与录制基准
                # 不同才换算，相同（或基准缺失）直接按录制坐标点。
                _acc_x = step.get("x")
                _acc_y = step.get("y")
                if isinstance(_acc_x, (int, float)) and isinstance(_acc_y, (int, float)):
                    _bw = int(step.get("_base_w") or 0)
                    _bh = int(step.get("_base_h") or 0)
                    _sw = int(step.get("_screen_w") or 0)
                    _sh = int(step.get("_screen_h") or 0)
                    if _sw <= 0 or _sh <= 0:
                        try:
                            _sw, _sh = _current_screen_size(device_id, 0, 0)
                        except Exception:
                            _sw, _sh = 0, 0
                    if _bw > 0 and _bh > 0 and _sw > 0 and _sh > 0 and (_bw != _sw or _bh != _sh):
                        _cx = int(round(_acc_x * _sw / _bw))
                        _cy = int(round(_acc_y * _sh / _bh))
                    else:
                        _cx, _cy = int(_acc_x), int(_acc_y)
                    execute_adb_command(
                        f'adb -s {device_id} shell input tap {_cx} {_cy}',
                        timeout=30,
                    )
                else:
                    raise RuntimeError(f"无障碍点击失败: {message}；该步骤未录制点击坐标，无法兜底")

        else:
            raise RuntimeError(f"未知设备动作步骤类型: {stype}")

        if i < repeat_count - 1:
            time.sleep(max(0, int(step.get("interval", 0))) / 1000)


def _load_scaled_template(device_id: str, step: dict, base_w: int, base_h: int):
    """读取模板图并按录制分辨率换算到当前画面尺寸，返回 (模板, 画面宽, 画面高)"""
    from routes.vision import load_template

    template_name = str(step.get("template", "")).strip()
    template = load_template(str(TEMPLATES_DIR / template_name))

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
    return template, screen_w, screen_h


def _exec_wait_step(device_id: str, step: dict, base_w: int, base_h: int, stop_event=None):
    """
    执行「等待组件出现」步骤

    模板图按录制时的画面分辨率保存。若回放时设备分辨率不同，
    需要把模板按比例缩放到当前屏幕尺寸，否则同一组件在不同分辨率下
    尺寸不匹配会一直等不到（不是点击坐标那种简单平移能解决的）。

    stop_event：停止标志，传入后轮询等待可被停止请求提前打断。
    """
    from routes.vision import wait_for_template

    template_name = str(step.get("template", "")).strip()
    template, screen_w, screen_h = _load_scaled_template(device_id, step, base_w, base_h)

    threshold = float(step.get("threshold", 0.85))
    timeout = float(step.get("timeout", 15))

    found, elapsed, score, attempts = wait_for_template(
        device_id, template, threshold=threshold, timeout=timeout, stop_event=stop_event,
        target_size=(screen_w, screen_h),
    )
    if not found:
        # 停止请求打断的等待不报"超时"错误，抛一个上层识别的标记
        if stop_event is not None and stop_event.is_set():
            raise _ScriptStopped()
        raise RuntimeError(
            f"等待组件超时：模板 {template_name} 在 {elapsed:.1f}s 内未出现"
            f"（最高分 {score:.3f} < 阈值 {threshold}，尝试 {attempts} 次）"
        )
    logger.info(f"[{device_id}] 等待组件 {template_name} 命中，耗时 {elapsed:.2f}s（分数 {score:.3f}）")


class _ScriptStopped(Exception):
    """用户停止脚本时用于中断执行流程的内部标记"""


def _current_screen_size(device_id: str, base_w: int, base_h: int):
    """取当前画面尺寸：有投屏视频帧时用它（与录制模板同坐标系），否则 wm size"""
    from routes.vision import video_frame_size
    size = video_frame_size(device_id)
    if size:
        return size
    result = execute_adb_command(f"adb -s {device_id} shell wm size", timeout=15)
    if not result["success"]:
        return 0, 0
    m = re.search(r'(\d+)x(\d+)', result.get("stdout") or "")
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def _eval_condition(device_id: str, step: dict, base_w: int, base_h: int, stop_event=None):
    """
    求一次判断条件，返回 (成立?, 说明文字)

    appInstalled / template / shell 都是单次求值；time 会轮询设备本地时钟
    直到到点或超时（当天目标时刻已过则立即成立，不等明天）。
    """
    from routes.vision import wait_for_template

    kind = step.get("kind")
    negate = bool(step.get("negate", False))
    ok = False
    reason = ""

    if kind == "appInstalled":
        pkg = str(step.get("packageName", "")).strip()
        ok = _check_package_installed(device_id, pkg)
        reason = f"应用 {pkg} {'已安装' if ok else '未安装'}"
    elif kind == "template":
        template_name = str(step.get("template", "")).strip()
        template, screen_w, screen_h = _load_scaled_template(device_id, step, base_w, base_h)
        threshold = float(step.get("threshold", 0.85))
        # timeout=0 让 wait_for_template 只截一帧、匹配一次（单次判断语义）
        found, _elapsed, score, _attempts = wait_for_template(
            device_id, template, threshold=threshold, timeout=0, interval=0,
            stop_event=stop_event, target_size=(screen_w, screen_h),
        )
        if stop_event is not None and stop_event.is_set():
            raise _ScriptStopped()
        ok = found
        reason = f"组件 {template_name} {'出现' if found else '未出现'}（最高分 {score:.3f}，阈值 {threshold}）"
    elif kind == "shell":
        cmd = str(step.get("command", "")).strip()
        expected = str(step.get("expected", ""))
        result = execute_adb_command(f'adb -s {device_id} shell "{cmd}"', timeout=60)
        stdout = (result.get("stdout") or "").strip()
        if not result["success"]:
            reason = f"Shell 命令执行失败: {(result.get('error') or '').strip()}"
        else:
            ok = expected in stdout
            reason = f"Shell 输出「{stdout[:120]}」{'包含' if ok else '未包含'}「{expected}」"
    elif kind == "time":
        target_time = str(step.get("targetTime", "")).strip()
        target_sec = _parse_clock(target_time)
        if target_sec is None:
            return False, f"目标时刻无效: {target_time}"
        timeout = float(step.get("timeout", 3600))
        poll = max(0.1, float(step.get("interval", 1000)) / 1000)
        deadline = time.monotonic() + timeout
        # 只读一次设备时钟，按本地单调时钟推算剩余等待，不再每轮起 adb 进程
        clock = execute_adb_command(f'adb -s {device_id} shell date "+%H:%M:%S"', timeout=15)
        m = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', (clock.get("stdout") or ""))
        if not (m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59 and int(m.group(3)) <= 59):
            ok = False
            reason = f"读取设备时间失败: {((clock.get('stdout') or '') + ' ' + (clock.get('error') or '')).strip()[:120]}"
        else:
            now_text = m.group(0)
            now_sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            # 读取耗时计入：读取那一刻距目标还差 target_sec - now_sec 秒，扣除已流逝的 elapsed
            elapsed = time.monotonic() - (deadline - timeout)
            wait_sec = (target_sec - now_sec) - elapsed
            if wait_sec <= 0:
                ok = True
                reason = f"设备时间 {now_text} 已到 {target_time}"
            elif wait_sec > timeout:
                ok = False
                reason = f"设备时间 {now_text} 未到 {target_time}（需等 {wait_sec:.1f}s 超过 timeout {timeout:g}s）"
            else:
                remaining = wait_sec
                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise _ScriptStopped()
                    if remaining <= 0:
                        break
                    step_wait = min(poll, remaining)
                    if stop_event is not None:
                        stop_event.wait(step_wait)
                    else:
                        time.sleep(step_wait)
                    remaining -= step_wait
                ok = True
                reason = f"设备时间 {now_text} 已到 {target_time}（本地等待 {wait_sec:.1f}s）"
    elif kind == "accClick":
        # 无障碍点击判断：经探针 0x0D 在无障碍树中匹配节点并执行 ACTION_CLICK，
        # 点到了 = 条件成立（未启用无障碍/未找到节点/点击失败 = 不成立）
        from routes.probe import run_probe

        acc_id = str(step.get("id", "") or "").strip()
        acc_text = str(step.get("text", "") or "").strip()
        acc_desc = str(step.get("desc", "") or "").strip()
        acc_data = {}
        if acc_id:
            acc_data["id"] = acc_id
        if acc_text:
            acc_data["text"] = acc_text
        if acc_desc:
            acc_data["desc"] = acc_desc
        d = run_probe(device_id, False, 0x0D, acc_data)
        if not d.get("connected"):
            ok = False
            reason = f"探针通讯失败: {d.get('reason', '未知原因')}"
        else:
            response = d.get("response") or {}
            if isinstance(response, dict) and response.get("code") == 200:
                ok = True
                reason = f"无障碍点击成功（id={acc_id or '-'} text={acc_text or '-'} desc={acc_desc or '-'}）"
            else:
                message = (response.get("message") if isinstance(response, dict) else None) or "设备返回异常"
                ok = False
                reason = f"无障碍点击未成功: {message}"
    else:
        reason = f"未知判断类型: {kind}"

    if negate:
        ok = not ok
        reason += "（已取反）"
    return ok, reason


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

    每台设备同一时间只允许一个执行（同设备重复提交会立刻失败）；
    支持停止：/script/stop-run 置停止标志后，当前步骤完成后不再继续。
    """
    from routes.remote_ws import acquire_session, release_session

    # 同设备并发执行防护：拿不到执行槽说明已有一个在跑
    stop_event = _script_runs.get(device_id)
    if stop_event is not None:
        raise RuntimeError(f"设备 {device_id} 已有脚本在执行中，请先停止或等待完成")
    stop_event = threading.Event()
    _script_runs[device_id] = stop_event

    steps = script.get("steps", [])
    # 全脚本没有注入类步骤时不需要 scrcpy 会话
    # （设备动作与视觉等待都走 adb，纯这类脚本更快、更稳）
    needs_session = any(s.get("type") in INJECT_STEP_TYPES for s in steps)

    session = None
    w = h = 0
    scale_x = scale_y = 1.0

    try:
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

        for round_no in range(1, repeat + 1):
            if stop_event.is_set():
                break
            logger.info(f"Script run round {round_no}/{repeat} on {device_id}")
            idx = 0
            jumps = 0
            while idx < len(steps):
                if stop_event.is_set():
                    logger.info(f"Script stopped by user on {device_id} at step {idx + 1}")
                    break
                step = steps[idx]
                stype = step.get("type")
                count = max(1, int(step.get("count", 1)))
                interval = max(0, int(step.get("interval", 0)))

                # 视觉等待步骤：反复截屏搜索模板，命中才继续
                if stype in VISION_STEP_TYPES:
                    # 基准分辨率优先用脚本保存的录制尺寸，缺失则用会话尺寸
                    base_w = int(script.get("videoWidth") or 0) or w
                    base_h = int(script.get("videoHeight") or 0) or h
                    for _ in range(count):
                        if stop_event.is_set():
                            break
                        _exec_wait_step(device_id, step, base_w, base_h, stop_event)
                # 设备动作类步骤走 adb 命令或探针帧（不依赖 scrcpy 会话），count 在函数内部循环
                elif stype in ADB_STEP_TYPES:
                    # 传入录制基准分辨率：accClick 兜底坐标换算用（缺省 0 时兜底不可用）
                    _exec_adb_step(
                        device_id,
                        {**step, "_base_w": int(script.get("videoWidth") or 0), "_base_h": int(script.get("videoHeight") or 0),
                         "_screen_w": w, "_screen_h": h},
                        count,
                        round_no,
                    )
                # 判断步骤：求一次条件，按真/假分支动作决定下一步
                elif stype in CONDITION_STEP_TYPES:
                    base_w = int(script.get("videoWidth") or 0) or w
                    base_h = int(script.get("videoHeight") or 0) or h
                    ok, reason = _eval_condition(device_id, step, base_w, base_h, stop_event)
                    branch = (step.get("onTrue") if ok else step.get("onFalse")) or {}
                    action = branch.get("action", "continue")
                    logger.info(f"[{device_id}] 判断步骤 step {idx + 1}: {reason} -> {action}")
                    if action == "goto":
                        jumps += 1
                        if jumps > MAX_SCRIPT_JUMPS:
                            raise RuntimeError(f"判断步骤跳转次数超过上限 {MAX_SCRIPT_JUMPS}，可能存在死循环")
                        next_idx = int(branch.get("target", 0)) - 1
                        if not (idx < next_idx < len(steps)):
                            raise RuntimeError(f"判断步骤跳转目标无效: 第 {branch.get('target')} 步")
                        idx = next_idx
                        continue
                    if action == "stop":
                        logger.info(f"[{device_id}] 判断步骤终止脚本 at step {idx + 1}")
                        return
                    if action == "fail":
                        raise RuntimeError(f"判断步骤未通过：{reason}")
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
                            text = _resolve_step_text(str(step.get("text") or ""), i + 1, round_no)
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
                            _interruptible_sleep(interval, stop_event)
                # 步与步之间的间隔（interval 单位毫秒）
                idx += 1
                if idx < len(steps):
                    _interruptible_sleep(interval, stop_event)
    finally:
        _script_runs.pop(device_id, None)
        if session is not None:
            release_session(device_id, session)
        logger.info(f"Script run finished on {device_id}")


# 每台设备当前执行的停止标志（设备号 -> Event）
_script_runs: dict = {}


def _interruptible_sleep(ms: float, stop_event: threading.Event):
    """可被停止请求提前唤醒的等待"""
    if ms <= 0:
        return
    stop_event.wait(ms / 1000)


@router.post("/device/script/stop-run")
def stop_script_run(request: DeviceIdRequest):
    """请求停止设备上正在执行的脚本（当前步骤完成后生效）"""
    stop_event = _script_runs.get(request.deviceId)
    if stop_event is None:
        return {"code": 200, "message": "该设备当前没有脚本在执行", "data": None}
    stop_event.set()
    logger.info(f"Script stop requested on {request.deviceId}")
    return {"code": 200, "message": "已发送停止请求，当前步骤完成后生效", "data": None}


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
    直接执行传入的步骤（不落盘），**同步等待执行完成**并返回真实结果

    面板上「执行/重复」按钮走这里。同步是因为步骤里的应用管理/等待组件
    等动作需要把失败原因（如设备未安装该应用）反馈给用户——之前的异步
    入队方式错误只进日志，界面上看不到。
    脚本通常几秒内完成；HTTP 客户端侧另有超时保护。
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

        # 直接在请求线程执行（FastAPI sync endpoint 跑在线程池，不阻塞事件循环）
        _run_script_on_device(request.deviceId, script, repeat)
        logger.info(f"Script steps finished on {request.deviceId}, repeat={repeat}, steps={len(script['steps'])}")
        return {
            "code": 200,
            "message": "success",
            "data": {"repeat": repeat, "stepCount": len(script["steps"])},
        }
    except _ScriptStopped:
        logger.info(f"Script steps stopped by user on {request.deviceId}")
        return {"code": 200, "message": "已停止", "data": {"stopped": True}}
    except RuntimeError as e:
        # 步骤执行失败（未安装应用/等待超时/命令失败等），把原因带回给前端
        logger.warning(f"Script steps failed on {request.deviceId}: {e}")
        return {"code": 500, "message": f"执行失败: {str(e)}", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"执行脚本失败: {str(e)}", "data": None}


# ========== 视觉匹配模板管理 ==========

class TemplateNameRequest(BaseModel):
    name: str


@router.post("/device/script/app-names")
def get_app_names(request: DeviceIdRequest):
    """
    获取设备上第三方应用的「包名 -> 中文名」映射（脚本页应用下拉用）

    数据来源两层：
    1. data/app_labels.json 缓存（应用管理页历史上积累的中文名，最准最快）
    2. 一条 `dumpsys package` 全量输出解析 application-label（单次 adb 往返）

    缓存没有且 dumpsys 也提取不到的包，值回落为包名本身。
    """
    try:
        if not request.deviceId:
            return {"code": 400, "message": "deviceId 不能为空", "data": None}

        from utils.app_label_utils import load_label_cache
        cache = load_label_cache()

        # 包名列表（-3 = 第三方应用）
        pkgs_result = execute_adb_command(f'adb -s {request.deviceId} shell pm list packages -3', timeout=30)
        if not pkgs_result["success"]:
            return {"code": 500, "message": "获取应用列表失败", "data": None}
        packages = [ln[8:].strip() for ln in (pkgs_result.get("stdout") or "").split("\n") if ln.startswith("package:")]
        if not packages:
            return {"code": 200, "message": "success", "data": {}}

        # 全部包名先落到结果表，缓存命中的直接用中文名
        names = {p: cache.get(p, p) for p in packages}

        # 缓存没覆盖的包，用一条全量 dumpsys 解析 label（比逐包 dumpsys 快几十倍）
        missing = [p for p in packages if names[p] == p]
        if missing:
            dump_result = execute_adb_command(f'adb -s {request.deviceId} shell dumpsys package', timeout=60)
            if dump_result["success"]:
                from utils.app_label_utils import extract_app_name_from_dumpsys
                # dumpsys package 输出按 "Package [com.xxx]" 分段，逐段提取 label
                current_pkg = None
                seg_lines: list = []
                seg_map: dict = {}
                for line in (dump_result.get("stdout") or "").split("\n"):
                    m = re.match(r'\s*Package \[([^\]]+)\]', line)
                    if m:
                        if current_pkg and current_pkg in missing and current_pkg not in seg_map:
                            label = extract_app_name_from_dumpsys("\n".join(seg_lines))
                            if label:
                                seg_map[current_pkg] = label
                        current_pkg = m.group(1)
                        seg_lines = []
                    elif current_pkg:
                        seg_lines.append(line)
                # 最后一段
                if current_pkg and current_pkg in missing and current_pkg not in seg_map:
                    label = extract_app_name_from_dumpsys("\n".join(seg_lines))
                    if label:
                        seg_map[current_pkg] = label

                for p, label in seg_map.items():
                    names[p] = label

        return {"code": 200, "message": "success", "data": names}
    except Exception as e:
        logger.error(f"获取应用名映射失败: {e}")
        return {"code": 500, "message": f"获取应用名映射失败: {str(e)}", "data": None}


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
        # 解码是 CPU 阻塞操作，放线程池里跑，避免卡住事件循环拖慢其他请求
        from routes.vision import load_template
        img = await asyncio.to_thread(load_template, str(path))
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


@router.post("/device/script/get-template")
def get_template_image(request: TemplateNameRequest):
    """返回单张模板图的 base64，供等待组件步骤展开时预览已选中的图片"""
    filename = _safe_name(os.path.splitext(request.name)[0]) + ".png"
    path = TEMPLATES_DIR / filename
    if not path.is_file():
        return {"code": 404, "message": "模板图不存在", "data": None}
    data = base64.b64encode(path.read_bytes()).decode()
    return {"code": 200, "message": "success", "data": "data:image/png;base64," + data}


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
