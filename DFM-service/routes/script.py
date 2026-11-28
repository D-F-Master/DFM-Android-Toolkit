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
中按 id/text/desc 匹配节点并直接执行 ACTION_CLICK（不走坐标注入）；accExists
判断走探针 0x0C 拉组件树后在服务端匹配（只查不点）其余注入类
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
import subprocess
import time
import random
import asyncio
import threading
import zipfile
import shutil
from pathlib import Path

import cv2

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel
from typing import Optional, List

import logging
from config import logger
from utils.tool_manager import get_adb_path, get_data_path, get_log_path
from routes.adb_utils import execute_adb_command


def _sanitize_device_dir(device_id: str) -> str:
    """设备号清洗为合法目录名（127.0.0.1:5555 -> 127.0.0.1_5555）"""
    return re.sub(r'[\\/:*?"<>|\s]+', '_', device_id.strip())


def _new_script_log_dir(device_id: str) -> Path:
    """创建本次脚本执行的日志目录 log/{设备号}_{执行时间}/，返回路径"""
    log_dir = get_log_path() / f"{_sanitize_device_dir(device_id)}_{time.strftime('%Y%m%d_%H%M%S')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _attach_script_log_handler(log_dir: Path):
    """为本次执行挂一个独立 FileHandler，返回 (handler, formatter)"""
    handler = logging.FileHandler(str(log_dir / "run.log"), encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    return handler


def _detach_script_log_handler(handler):
    """移除并关闭本次执行的 FileHandler"""
    logger.removeHandler(handler)
    handler.close()


def _capture_error_screen(device_id: str, log_dir: Path, step_no: int):
    """报错时截取设备当前画面存入日志目录（失败不影响原异常传播）"""
    try:
        from routes.vision import capture_screen
        frame = capture_screen(device_id)
        if frame is not None:
            shot = log_dir / f"error_step{step_no}_{time.strftime('%H%M%S')}.png"
            cv2.imwrite(str(shot), frame)
            logger.info(f"[{device_id}] 错误画面已保存: {shot.name}")
    except Exception as e:
        logger.warning(f"[{device_id}] 错误画面保存失败: {e}")


class DeviceIdRequest(BaseModel):
    deviceId: str

router = APIRouter(
    prefix="/api",
    tags=["模拟脚本"]
)

SCRIPTS_DIR = get_data_path() / "scripts"
APKS_DIR = get_data_path() / "apk"
TEMPLATES_DIR = get_data_path() / "templates"   # 视觉匹配模板图（等待组件出现）
MEDIA_DIR = get_data_path() / "media"           # 脚本引用的媒体素材
# 摄像头替换素材允许的扩展名（图片 1 帧 / 视频抽帧，与设备端白名单一致）
CAM_MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi", ".webm", ".3gp", ".mkv")


def _ensure_scripts_dir():
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_media_dir():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    """文件名安全过滤：只保留中英文数字下划线连字符，防止路径穿越"""
    name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', name).strip('_')
    return name or "unnamed"


def _safe_rel_dir(rel: str) -> Path:
    """
    把用户传入的相对目录路径解析为 SCRIPTS_DIR 下的安全子目录 Path。
    逐段用 _safe_name 过滤，杜绝 .. / 绝对路径 / 反斜杠穿越；空路径返回根目录。
    """
    rel = (rel or "").strip().replace("\\", "/")
    if not rel:
        return SCRIPTS_DIR
    parts = [_safe_name(seg) for seg in rel.split("/") if seg.strip()]
    return SCRIPTS_DIR.joinpath(*parts) if parts else SCRIPTS_DIR


def _script_file(name: str, rel: str = "") -> Path:
    """脚本文件路径：目录下的 {safe_name(name)}.json"""
    return _safe_rel_dir(rel) / f"{_safe_name(name)}.json"


def _run_script_ref_path(ref: str) -> Path:
    """
    把 runScript 的脚本引用（"子目录/名字"，相对 SCRIPTS_DIR 根，POSIX 分隔）
    解析为安全文件路径；空段过滤，逐段经 _safe_name / _safe_rel_dir 净化防穿越。
    """
    parts = [p for p in (ref or "").strip().strip("/").split("/") if p.strip()]
    return _script_file(parts[-1] if parts else "", "/".join(parts[:-1]))


class ScriptSaveRequest(BaseModel):
    name: str
    repeat: int = 1
    steps: List[dict]
    videoWidth: Optional[int] = None   # 录制时的画面宽（坐标基准）
    videoHeight: Optional[int] = None  # 录制时的画面高


class ScriptNameRequest(BaseModel):
    name: str
    path: str = ""   # 所在文件夹相对路径，"" 表示根目录（仓库文件夹功能）


class FolderRequest(BaseModel):
    path: str = ""   # 文件夹相对路径，"" 表示根目录
    name: str = ""   # 新建/重命名时的名称


class ScriptMoveRequest(BaseModel):
    name: str
    fromPath: str = ""   # 源文件夹相对路径
    toPath: str = ""     # 目标文件夹相对路径


class ScriptRunRequest(BaseModel):
    deviceId: str
    name: str
    path: str = ""       # 脚本所在文件夹相对路径，"" 表示根目录
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
    path: str = ""       # 脚本所在文件夹相对路径，"" 表示根目录（仓库子文件夹用）
    targetDir: str = ""  # 导出保存目录（绝对路径），空则用默认 D:\log\script_exports


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
    "keyevent", "volume", "reboot", "tts", "accClick", "runScript",
    "sleep", "shell", "screenshot",
)
# 视觉等待：反复截屏搜索模板图，命中才继续
VISION_STEP_TYPES = ("waitFor",)
# 判断步骤：求一次条件，按真/假分支动作决定下一步
CONDITION_STEP_TYPES = ("condition",)
CONDITION_KINDS = ("appInstalled", "template", "shell", "time", "accClick", "accExists")
BRANCH_ACTIONS = ("continue", "goto", "stop", "fail", "runScript")
MAX_SCRIPT_JUMPS = 100  # 每轮脚本的跳转次数上限，防止分支构成死循环
MAX_NESTED_SCRIPT_DEPTH = 3  # 嵌套执行脚本（runScript）的最大深度，防递归套娃
MAX_STEP_RETRIES = 5  # 步骤级失败重试的最大次数（每次失败后重试一次）
STEP_RETRY_INTERVAL_CAP = 600000  # 步骤重试间隔上限（毫秒）
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

    if step_type == "runScript":
        # 嵌套执行脚本：脚本名必填、禁反斜杠/..（子文件夹引用用 / 分隔）、文件必须存在
        sub_name = str(step.get("script", "")).strip()
        if not sub_name:
            return f"第 {idx} 步未选择要执行的脚本"
        if "\\" in sub_name or ".." in sub_name:
            return f"第 {idx} 步的嵌套脚本名不能包含反斜杠或 .."
        try:
            sub_repeat = int(step.get("repeat", 1))
        except (TypeError, ValueError):
            return f"第 {idx} 步的嵌套脚本执行次数无效"
        if not (1 <= sub_repeat <= 99):
            return f"第 {idx} 步的嵌套脚本执行次数需在 1-99 之间"
        _ensure_scripts_dir()
        if not _run_script_ref_path(sub_name).exists():
            return f"第 {idx} 步的嵌套脚本 {sub_name} 不存在，请先保存"
        return ""

    if step_type == "sleep":
        # 固定延时：以 seconds 为准（count>1 时重复等待）
        try:
            seconds = float(step.get("seconds", 1))
        except (TypeError, ValueError):
            return f"第 {idx} 步的延时时长无效"
        if not (0 < seconds <= 3600):
            return f"第 {idx} 步的延时时长需在 0-3600 秒之间"
        return ""

    if step_type == "shell":
        cmd = str(step.get("command", "")).strip()
        if not cmd:
            return f"第 {idx} 步未填写 Shell 命令"
        if len(cmd) > 2000:
            return f"第 {idx} 步的 Shell 命令过长（上限 2000 字符）"
        lowered = cmd.lower()
        if lowered.startswith("adb ") or lowered.startswith("adb.exe "):
            return f"第 {idx} 步的 Shell 命令不要带 adb 前缀，命令会在设备 shell 中执行"
        if '"' in cmd:
            return f"第 {idx} 步的 Shell 命令不能包含双引号"
        try:
            timeout = int(step.get("timeout", 60))
        except (TypeError, ValueError):
            return f"第 {idx} 步的命令超时无效"
        if not (1 <= timeout <= 600):
            return f"第 {idx} 步的命令超时需在 1-600 秒之间"
        return ""

    if step_type == "screenshot":
        # 截图存档：只校验保存文件名前缀，实际文件落在本次执行的日志目录
        label = str(step.get("label", "") or "").strip()
        if len(label) > 64:
            return f"第 {idx} 步的截图名称过长（上限 64 字符）"
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
        # 分支动作两类：控制流（continue/goto/stop/fail/runScript）或直接执行某个步骤功能（VALID_STEP_TYPES）
        if action not in BRANCH_ACTIONS and action not in VALID_STEP_TYPES:
            return f"第 {idx} 步的 {label} 动作无效: {action}（应为 {'/'.join(BRANCH_ACTIONS)} 或某个步骤功能）"
        if action == "goto":
            target = branch.get("target")
            if not isinstance(target, int) or isinstance(target, bool) or not (1 <= target <= total):
                return f"第 {idx} 步的 {label} 跳转目标必须是第 1-{total} 步之间的序号"
        if action == "runScript":
            name = str(branch.get("script", "")).strip()
            if not name:
                return f"第 {idx} 步的 {label} 选择执行脚本，但未指定目标脚本"
            if "\\" in name or ".." in name:
                return f"第 {idx} 步的 {label} 目标脚本名不能包含反斜杠或 .."
            _ensure_scripts_dir()
            if not _run_script_ref_path(name).exists():
                return f"第 {idx} 步的 {label} 嵌套脚本 {name} 不存在，请先保存"

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
    elif kind == "accExists":
        # 无障碍组件存在判断：id/text/desc 至少一项，只查不点（与 accClick 同一匹配语义）
        acc_id = str(step.get("id", "") or "").strip()
        acc_text = str(step.get("text", "") or "").strip()
        acc_desc = str(step.get("desc", "") or "").strip()
        if not (acc_id or acc_text or acc_desc):
            return f"第 {idx} 步的无障碍组件判断需至少填写 resource-id / 文本 / 内容描述 一项"
        for label, value in (("resource-id", acc_id), ("文本", acc_text), ("内容描述", acc_desc)):
            if len(value) > 200:
                return f"第 {idx} 步的无障碍组件判断 {label} 过长（≤200 字符）"

    # 分支动作 = runScript 时：必须指定嵌套脚本名，且脚本文件存在
    for label in ("onTrue", "onFalse"):
        branch = step.get(label) or {}
        if branch.get("action") == "runScript":
            sub_name = str(branch.get("script", "")).strip()
            if not sub_name:
                return f"第 {idx} 步的 {label} 动作是执行脚本，但未选择脚本"
            if "\\" in sub_name or ".." in sub_name:
                return f"第 {idx} 步的 {label} 嵌套脚本名不能包含反斜杠或 .."
            _ensure_scripts_dir()
            if not _run_script_ref_path(sub_name).exists():
                return f"第 {idx} 步的 {label} 嵌套脚本 {sub_name} 不存在，请先保存"

    return ""


def _normalize_branch(branch) -> dict:
    """
    分支动作归一化：
    - goto 带 target；runScript 带 script；
    - action=某个步骤功能（VALID_STEP_TYPES）时透传该功能的参数字段
      （text/id/desc/packageName/keycode/path/apkName/threshold/template/command/expected 等），
      供 _run_branch_step 构造临时步骤用；
    - 纯控制流（continue/stop/fail）不带参数，避免脏字段落盘。
    """
    if not isinstance(branch, dict):
        return {"action": "continue"}
    action = branch.get("action", "continue")
    if action == "goto":
        return {"action": "goto", "target": int(branch.get("target", 0))}
    if action == "runScript":
        return {"action": "runScript", "script": str(branch.get("script", "")).strip()}
    if action in VALID_STEP_TYPES:
        keep = {k: v for k, v in branch.items() if k not in ("action", "target")}
        keep["action"] = action
        return keep
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
            for field in ("apkName", "path", "packageName", "activity", "keycode", "action", "stream", "text", "id", "desc", "x", "y",
                          "seconds", "command", "timeout", "label", "expectOutput", "failOnError", "waitOnline", "onlineTimeout",
                          "file", "dev", "mirror"):
                if step.get(field) is not None:
                    item[field] = step.get(field)
            if step["type"] == "runScript":
                item["script"] = str(step.get("script", "")).strip()
                item["repeat"] = max(1, min(99, int(step.get("repeat", 1) or 1)))
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
    # 步骤级失败重试：所有类型通用，字段归一化后统一补（0=不重试）
    for item, raw in zip(normalized, steps):
        item["retry"] = max(0, min(MAX_STEP_RETRIES, int(raw.get("retry", 0) or 0)))
        item["retryInterval"] = max(0, min(STEP_RETRY_INTERVAL_CAP, int(raw.get("retryInterval", 2000) or 0)))
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
        # 递归含子文件夹；path 为相对 SCRIPTS_DIR 的目录（"" = 根目录，POSIX 分隔），
        # 供 runScript 引用与载入对话按「子目录/名字」定位脚本
        for f in sorted(SCRIPTS_DIR.rglob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                rel_dir = f.parent.relative_to(SCRIPTS_DIR).as_posix()
                scripts.append({
                    "name": data.get("name", f.stem),
                    "repeat": data.get("repeat", 1),
                    "stepCount": len(data.get("steps", [])),
                    "path": "" if rel_dir == "." else rel_dir,
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
        path = _script_file(name, request.path)
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
        path = _script_file(name, request.path)
        if path.exists():
            path.unlink()
            logger.info(f"Script deleted: {path}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"删除脚本失败: {str(e)}", "data": None}


# ========== 仓库文件夹管理 ==========

@router.post("/device/script/folder/list")
def list_script_folder(request: dict):
    """列出某文件夹下的子文件夹与脚本概要（path="" 表示根目录）"""
    try:
        _ensure_scripts_dir()
        rel = str((request or {}).get("path", "") or "")
        folder = _safe_rel_dir(rel)
        if not folder.exists() or not folder.is_dir():
            return {"code": 404, "message": "文件夹不存在", "data": None}
        dirs, scripts = [], []
        for child in sorted(folder.iterdir(), key=lambda p: p.name):
            if child.is_dir():
                dirs.append(child.name)
            elif child.suffix == ".json":
                try:
                    with open(child, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    scripts.append({
                        "name": data.get("name", child.stem),
                        "fileName": child.stem,
                        "repeat": data.get("repeat", 1),
                        "stepCount": len(data.get("steps", [])),
                    })
                except Exception:
                    continue
        return {"code": 200, "message": "success", "data": {"path": rel, "dirs": dirs, "scripts": scripts}}
    except Exception as e:
        return {"code": 500, "message": f"读取文件夹失败: {str(e)}", "data": None}


@router.post("/device/script/folder/create")
def create_script_folder(request: FolderRequest):
    """在当前路径下新建子文件夹"""
    try:
        _ensure_scripts_dir()
        folder = _safe_rel_dir(request.path) / _safe_name(request.name)
        if folder.exists():
            return {"code": 400, "message": "文件夹已存在", "data": None}
        folder.mkdir(parents=True)
        logger.info(f"Script folder created: {folder}")
        return {"code": 200, "message": "success", "data": {"name": folder.name}}
    except Exception as e:
        return {"code": 500, "message": f"创建文件夹失败: {str(e)}", "data": None}


@router.post("/device/script/folder/delete")
def delete_script_folder(request: FolderRequest):
    """删除文件夹（含其中全部脚本，禁止删根目录）"""
    try:
        _ensure_scripts_dir()
        target = _safe_rel_dir(request.path) / _safe_name(request.name)
        if target == SCRIPTS_DIR or not target.exists() or not target.is_dir():
            return {"code": 400, "message": "文件夹不存在或不可删除", "data": None}
        shutil.rmtree(target)
        logger.info(f"Script folder deleted: {target}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"删除文件夹失败: {str(e)}", "data": None}


@router.post("/device/script/rename")
def rename_script(request: dict):
    """重命名脚本文件（同一文件夹内改名）"""
    try:
        _ensure_scripts_dir()
        body = request or {}
        rel = str(body.get("path", "") or "")
        old_name = _safe_name(str(body.get("name", "")))
        new_name = _safe_name(str(body.get("newName", "")))
        if not new_name:
            return {"code": 400, "message": "新名称不能为空", "data": None}
        src = _script_file(old_name, rel)
        dst = _script_file(new_name, rel)
        if not src.exists():
            return {"code": 404, "message": "脚本不存在", "data": None}
        if dst.exists():
            return {"code": 400, "message": "同名脚本已存在", "data": None}
        src.rename(dst)
        # 同步更新脚本内的 name 字段，保证载入后显示的是新名
        try:
            with open(dst, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            data["name"] = new_name
            with open(dst, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass
        logger.info(f"Script renamed: {src} -> {dst}")
        return {"code": 200, "message": "success", "data": {"name": new_name}}
    except Exception as e:
        return {"code": 500, "message": f"重命名失败: {str(e)}", "data": None}


@router.post("/device/script/move")
def move_script(request: ScriptMoveRequest):
    """把脚本从源文件夹移动到目标文件夹"""
    try:
        _ensure_scripts_dir()
        name = _safe_name(request.name)
        src = _script_file(name, request.fromPath)
        dst_dir = _safe_rel_dir(request.toPath)
        if not src.exists():
            return {"code": 404, "message": "脚本不存在", "data": None}
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{name}.json"
        if dst.exists():
            return {"code": 400, "message": "目标文件夹已有同名脚本", "data": None}
        src.rename(dst)
        logger.info(f"Script moved: {src} -> {dst}")
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"移动脚本失败: {str(e)}", "data": None}


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
EXPORT_MEDIA_DIR = "media"
# 方案3：签名关键字机制已退役（旧 EXPORT_SECRET/EXPORT_SIGNATURE_MEMBER/_bundle_signature
# 已删除，见 docx/脚本方案3-设备端JS执行设计.md §5.1）；导出包为普通 zip，
# 防护转移为推送入口受控 + 设备端 apiVersion 校验。

def _script_referenced_files(script: dict) -> tuple[List[str], List[str], List[str]]:
    """
    扫描脚本步骤，收集引用的 APK 文件名、模板图文件名与摄像头替换素材名（去重、保持出现顺序）

    返回 (apk_names, template_names, media_names)
    """
    apk_names: List[str] = []
    template_names: List[str] = []
    media_names: List[str] = []
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
    return apk_names, template_names, media_names


@router.post("/device/script/export-zip")
def export_script_zip(request: ScriptExportRequest):
    """
    导出脚本为可迁移的 zip 包：脚本 JSON + 它引用的全部 APK/模板图素材

    zip 布局：
      script.json          脚本原文（与 data/scripts/{name}.json 完全一致）
      apk/{apkName}        脚本引用的 APK（installApk 步骤）
      templates/{tpl}.png  脚本引用的模板图（waitFor / condition-template 步骤）
      media/{file}         脚本引用的媒体素材

    zip 写到 D:\\log\\script_exports\\{name}.zip，前端提示保存位置。
    """
    try:
        _ensure_scripts_dir()
        name = _safe_name(request.name)
        path = _script_file(name, request.path)
        if not path.exists():
            return {"code": 404, "message": f"脚本 {name} 不存在", "data": None}

        with open(path, "r", encoding="utf-8") as f:
            script = json.load(f)

        apk_names, template_names, media_names = _script_referenced_files(script)

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
        media_paths: List = []
        for media in media_names:
            p = MEDIA_DIR / media
            if p.exists():
                media_paths.append(p)
            else:
                missing.append(f"摄像头素材 {media}")
        if missing:
            return {"code": 400, "message": "以下资源缺失，请先补齐再导出: " + "、".join(missing), "data": None}

        # 导出目录：前端目录选择框传入则用之（校验为绝对路径，拒绝相对路径防落到工作目录下），
        # 未传则用默认 D:\log\script_exports
        export_root = r"D:\log\script_exports"
        target_dir = str(request.targetDir or "").strip()
        if target_dir:
            if not os.path.isabs(target_dir):
                return {"code": 400, "message": "导出目录必须是绝对路径", "data": None}
            export_root = target_dir
        os.makedirs(export_root, exist_ok=True)
        zip_path = os.path.join(export_root, f"{name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            script_bytes = path.read_bytes()
            zf.writestr("script.json", script_bytes)
            for p in apk_paths:
                zf.writestr(f"{EXPORT_APK_DIR}/{p.name}", p.read_bytes())
            for p in tpl_paths:
                zf.writestr(f"{EXPORT_TEMPLATE_DIR}/{p.name}", p.read_bytes())
            for p in media_paths:
                zf.writestr(f"{EXPORT_MEDIA_DIR}/{p.name}", p.read_bytes())

        size = os.path.getsize(zip_path)
        logger.info(f"Script exported: {zip_path} (apk={len(apk_paths)}, templates={len(tpl_paths)}, media={len(media_paths)}, {size} bytes)")
        return {
            "code": 200,
            "message": "success",
            "data": {
                "path": zip_path,
                "size": size,
                "apkNames": [p.name for p in apk_paths],
                "templateNames": [p.name for p in tpl_paths],
                "mediaNames": [p.name for p in media_paths],
            },
        }
    except Exception as e:
        logger.error(f"导出脚本包失败: {e}")
        return {"code": 500, "message": f"导出脚本包失败: {str(e)}", "data": None}


@router.post("/device/script/import-zip")
async def import_script_zip(script: UploadFile = File(..., description="脚本导出包(.zip)")):
    """
    导入脚本 zip 包：落脚本 JSON + 把包内 apk/templates/media 资源还原到对应目录

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

            # 1) 定位脚本 JSON（固定名 script.json；兼容未指定名的单 JSON 包）
            # 方案3：签名校验已退役（见 docx/脚本方案3-设备端JS执行设计.md §5.1），
            # 导入不再要求本工具导出的签名包。
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

            # 还原资源：只收 apk/ templates/ media/ 顶层目录下的文件，防路径穿越
            imported_apks: List[str] = []
            imported_templates: List[str] = []
            imported_media: List[str] = []
            _ensure_templates_dir()
            _ensure_media_dir()
            for member in names:
                if member.endswith("/"):
                    continue
                parts = member.split("/")
                if len(parts) != 2:
                    continue  # 只认顶层 apk/xxx、templates/xxx、media/xxx，忽略嵌套与杂项
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
                elif folder == EXPORT_MEDIA_DIR and filename.lower().endswith(CAM_MEDIA_EXTS):
                    target_dir = MEDIA_DIR
                    imported_media.append(filename)
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

        logger.info(f"Script zip imported: {target} (apk={len(imported_apks)}, templates={len(imported_templates)}, media={len(imported_media)})")
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
                "mediaNames": imported_media,
            },
        }
    except Exception as e:
        logger.error(f"导入脚本包失败: {e}")
        return {"code": 500, "message": f"导入脚本包失败: {str(e)}", "data": None}



def _run_branch_step(device_id: str, action: str, branch: dict, script: dict, w: int, h: int, round_no: int,
                     session=None, scale_x: float = 1.0, scale_y: float = 1.0):
    """
    判断分支动作=执行某个步骤功能（需求③：分支可选左边功能区的全部功能）。

    把分支对象当作一个临时步骤执行，按类型分流：
    - ADB 类（手势外的设备动作，含 runScript 嵌套）→ _exec_adb_step
    - waitFor → _exec_wait_step；condition → _eval_condition（其分支动作在此忽略，防套娃）
    - 手势类（tap/longpress/swipe/input）需要 scrcpy 会话，由调用方传入当前持有的
      session 注入一次手势（输入文本会展开变量占位符，round_no 沿用父脚本当前轮次）
    """
    # 构造临时步骤：分支里除 action/target 外的字段都透传（script/id/text/desc/packageName 等）
    step = {k: v for k, v in branch.items() if k not in ("action", "target")}
    step["type"] = action
    # ADB 类分支步骤默认执行一次
    step.setdefault("interval", 0)
    step.setdefault("count", 1)
    if action == "volume":
        # 分支对象的 action 键被分支动作占用，音量子动作另存 volumeAction
        step["action"] = str(branch.get("volumeAction", "up"))
        step.pop("volumeAction", None)

    if action in ADB_STEP_TYPES:
        _exec_adb_step(
            device_id,
            {**step, "_base_w": int(script.get("videoWidth") or 0), "_base_h": int(script.get("videoHeight") or 0),
             "_screen_w": w, "_screen_h": h,
             "_log_dir": script.get("_log_dir"), "_stop_event": script.get("_stop_event")},
            1,
            round_no,
        )
        return

    if action in VISION_STEP_TYPES:
        base_w = int(script.get("videoWidth") or 0) or w
        base_h = int(script.get("videoHeight") or 0) or h
        _exec_wait_step(device_id, step, base_w, base_h)
        return

    if action in CONDITION_STEP_TYPES:
        # 分支动作=再嵌一个判断步骤：允许，但为防递归套娃其分支动作忽略（按 continue 处理）
        base_w = int(script.get("videoWidth") or 0) or w
        base_h = int(script.get("videoHeight") or 0) or h
        ok, reason = _eval_condition(device_id, step, base_w, base_h)
        if not ok:
            logger.warning(f"[{device_id}] 分支嵌套判断未成立（忽略其分支动作）: {reason}")
        return

    if action in INJECT_STEP_TYPES:
        # 手势类：调用方传入当前 scrcpy 控制会话，注入一次手势
        if session is None:
            raise RuntimeError("判断分支执行手势类功能缺少控制会话")
        _inject_gesture_step(session, step, 0, w, h, scale_x, scale_y, round_no, 0, script.get("_stop_event"), 1)
        return

    raise RuntimeError(f"判断分支不支持执行该功能: {action}")


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


def _run_device_shell(device_id: str, command: str, timeout: int = 60) -> dict:
    """
    在设备 shell 中执行一条命令，返回 {"code": 返回码, "stdout": 输出, "stderr": 错误}

    不能用 execute_adb_command：它会把命令串里的 'adb ' 全局替换成 Windows adb 路径，
    用户命令里只要出现 'adb ' 就会被污染（见 adb_utils.execute_adb_command 注释）。
    这里用参数列表调用，命令作为单个参数交给 adb，不做任何字符串替换，
    设备端的管道/重定向/分号串联都能原样生效。
    """
    try:
        proc = subprocess.run(
            [get_adb_path(), "-s", device_id, "shell", command],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        return {"code": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except subprocess.TimeoutExpired:
        return {"code": -1, "stdout": "", "stderr": f"命令超时（{timeout}s）"}
    except Exception as e:
        return {"code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


def _wait_device_online(device_id: str, timeout: float = 180, stop_event=None) -> float:
    """
    等设备重新上线（reboot 后必用），返回实际等待秒数

    两段等待：① 先等它从 adb devices 里消失（重启瞬间设备可能还在列表里，
    立即判在线会误通过）；② 再等它带 device 状态重新出现。
    超过 timeout 秒仍未上线抛 RuntimeError。
    """
    start = time.monotonic()
    deadline = start + max(1.0, timeout)
    gone = False
    logger.info(f"[{device_id}] 等待设备重新上线（上限 {timeout:g}s）")
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            raise _ScriptStopped()
        online = _check_device_online(device_id)
        if not gone:
            # 设备还没断连：说明重启命令刚发出，继续等它掉线
            if not online:
                gone = True
                logger.info(f"[{device_id}] 设备已断开，等待重新上线")
        elif online:
            elapsed = time.monotonic() - start
            logger.info(f"[{device_id}] 设备已重新上线，耗时 {elapsed:.1f}s")
            return elapsed
        if stop_event is not None:
            stop_event.wait(2.0)
        else:
            time.sleep(2.0)
    raise RuntimeError(f"等待设备重新上线超时（{timeout:g}s 内未恢复）")


def _save_step_screenshot(device_id: str, log_dir, label: str = "") -> Path:
    """
    截图步骤：把当前画面存进本次执行的日志目录，返回文件路径

    与报错截图（_capture_error_screen）同一目录，方便事后把
    「步骤 n 的画面」和 run.log 的时间线对照着看。
    """
    from routes.vision import capture_screen

    if log_dir is None:
        raise RuntimeError("截图步骤缺少日志目录（可能不在脚本执行流程中调用）")
    frame = capture_screen(device_id)
    if frame is None:
        raise RuntimeError("截图失败：未取到设备画面")
    prefix = _safe_name(label) if str(label or "").strip() else "step"
    shot = Path(log_dir) / f"{prefix}_{time.strftime('%H%M%S')}.png"
    if not cv2.imwrite(str(shot), frame):
        raise RuntimeError(f"截图保存失败: {shot}")
    logger.info(f"[{device_id}] 截图已保存: {shot.name}")
    return shot


# 嵌套执行脚本（runScript）的当前深度：同一执行线程内传递（asyncio/线程池下互不干扰）
_script_nest_depth = threading.local()


def _exec_run_script_step(device_id: str, step: dict):
    """
    执行 runScript 步骤：加载嵌套脚本并同步跑完（阻塞）。

    复用 _run_script_on_device 引擎：注入类子步骤会临时 acquire 共享会话
    （引用计数，父脚本已持有则直接复用，不重复起 scrcpy server）；子脚本
    继承父脚本的停止标志——用户点停止时父/子一起停。深度超过
    MAX_NESTED_SCRIPT_DEPTH 直接报错，防止 A 套 B、B 又套 A 的递归套娃。
    """
    name = str(step.get("script", "")).strip()
    sub_repeat = max(1, min(99, int(step.get("repeat", 1) or 1)))
    if not name:
        raise RuntimeError("嵌套执行脚本未指定脚本名")

    depth = getattr(_script_nest_depth, "value", 0)
    if depth >= MAX_NESTED_SCRIPT_DEPTH:
        raise RuntimeError(f"嵌套执行脚本深度超过上限 {MAX_NESTED_SCRIPT_DEPTH}，请检查是否存在递归嵌套")

    _ensure_scripts_dir()
    path = _run_script_ref_path(name)
    if not path.exists():
        raise RuntimeError(f"嵌套脚本 {name} 不存在，请先保存")
    try:
        with open(path, "r", encoding="utf-8") as f:
            sub_script = json.load(f)
    except Exception as e:
        raise RuntimeError(f"读取嵌套脚本 {name} 失败: {str(e)}")

    sub_steps = sub_script.get("steps") or []
    if not sub_steps:
        raise RuntimeError(f"嵌套脚本 {name} 没有步骤，无法执行")

    _script_nest_depth.value = depth + 1
    # 继承父脚本的停止标志：用户点停止时父/子一起停（_run_script_on_device
    # 检测到 _script_nest_depth.stop_event 即为嵌套模式，复用执行槽不重复占）
    _script_nest_depth.stop_event = getattr(_script_nest_depth, "stop_event", None) or _script_runs.get(device_id)
    try:
        logger.info(f"[{device_id}] runScript step: 嵌套执行「{name}」repeat={sub_repeat} (depth={depth + 1})")
        _run_script_on_device(device_id, {**sub_script, "name": name}, sub_repeat)
    finally:
        _script_nest_depth.value = depth


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
            # 等待设备重新上线：reboot 只是发命令，设备随即断连，
            # 不等待的话后续步骤必然报"设备不在线"。
            # 只在最后一轮等（count>1 连发重启没有意义，且上线等待是分钟级阻塞）
            if step.get("waitOnline", True) and i >= repeat_count - 1:
                _wait_device_online(device_id, float(step.get("onlineTimeout", 180)), step.get("_stop_event"))

        elif stype == "sleep":
            # 固定延时步骤：只等待、不做任何设备操作
            seconds = float(step.get("seconds", 1))
            _interruptible_sleep(seconds * 1000, step.get("_stop_event"), raise_on_stop=True)
            logger.info(f"[{device_id}] 固定延时 {seconds:g}s 完成")

        elif stype == "shell":
            # 自由 Shell 执行：命令直接交给设备 shell，输出整体记入日志
            cmd = str(step.get("command", "")).strip()
            timeout = max(1, min(600, int(step.get("timeout", 60) or 60)))
            result = _run_device_shell(device_id, cmd, timeout)
            stdout = (result.get("stdout") or "").strip()
            stderr = (result.get("stderr") or "").strip()
            summary = (stdout or stderr)[:500]
            logger.info(f"[{device_id}] Shell「{cmd}」返回码 {result['code']}：{summary or '(无输出)'}")
            # expectOutput 非空时要求输出包含该串，否则视为步骤失败
            expect = str(step.get("expectOutput", "") or "")
            if expect and expect not in stdout:
                raise RuntimeError(f"Shell 步骤输出未包含「{expect}」：{summary[:200] or '(无输出)'}")
            # failOnError=true 时命令返回码非 0 视为失败（默认只记录不判失败）
            if step.get("failOnError") and result["code"] != 0:
                raise RuntimeError(f"Shell 命令返回码 {result['code']}：{summary[:200] or '(无输出)'}")

        elif stype == "screenshot":
            # 截图存档：存进本次执行的日志目录，与 run.log/报错截图放一起
            _save_step_screenshot(device_id, step.get("_log_dir"), step.get("label", ""))

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
            # v2.15 匹配语义：id 精确；text/desc 精确相等，支持通配符 * ? [abc] {a,b}
            # （规则见前端提示与 docx/DFM-probe_接口文档.md）。找不到匹配节点直接失败，
            # 不再多轮降级/坐标兜底（v2.14 及以前的 attempts 链会误点文字相近的无关节点）。
            from routes.probe import run_probe

            _acc_id = str(step.get("id", "") or "").strip()
            _acc_text = str(step.get("text", "") or "").strip()
            _acc_desc = str(step.get("desc", "") or "").strip()
            if not (_acc_id or _acc_text or _acc_desc):
                raise RuntimeError("无障碍点击步骤需至少填写 id/text/desc 一项")

            _data = {"id": _acc_id, "text": _acc_text, "desc": _acc_desc}
            d = run_probe(device_id, False, 0x0D, _data)
            if not d.get("connected"):
                raise RuntimeError(f"探针通讯失败: {d.get('reason', '未知原因')}")
            response = d.get("response") or {}
            if not (isinstance(response, dict) and response.get("code") == 200):
                _msg = (response.get("message") if isinstance(response, dict) else None) or "设备返回异常"
                raise RuntimeError(f"无障碍点击失败: {_msg}")

        elif stype == "runScript":
            # 嵌套执行脚本：加载目标脚本文件，复用同一执行引擎跑完再继续
            # （同步执行、共享同一停止标志；深度防护防脚本 A→B→A 递归套娃）
            _exec_run_script_step(device_id, step)

        else:
            raise RuntimeError(f"未知设备动作步骤类型: {stype}")

        if i < repeat_count - 1:
            _interruptible_sleep(max(0, int(step.get("interval", 0))), step.get("_stop_event"))


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


def _glob_to_regex(pattern: str) -> str:
    """把 accClick/accExists 的 text/desc 通配符翻译成正则（与设备端 globMatch 同规则）。

    *        任意数量任意字符
    ?        单个任意字符
    [abc]    方括号内任一字符（[a-z] 区间、[!a] 取反）
    {a,b}    花括号内任一选项
    不含通配符时调用方直接用 == 精确比较；含通配符时整串匹配（非子串）。
    """
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == '*':
            out.append('.*')
            i += 1
        elif c == '?':
            out.append('.')
            i += 1
        elif c == '[':
            end = pattern.find(']', i + 1)
            if end < 0:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:end]
                neg = body.startswith('!') or body.startswith('^')
                if neg:
                    body = body[1:]
                body = body.replace('\\', '\\\\')
                # 区间保留原样（re 字符类同语法）；首尾 ] 转义由 find(']') 已处理
                out.append(('[' if not neg else '[^') + body + ']')
                i = end + 1
        elif c == '{':
            # 找配对 }，按 ',' 拆选项递归翻译（选项内支持嵌套通配符）
            depth, end = 0, -1
            for j in range(i, n):
                if pattern[j] == '{':
                    depth += 1
                elif pattern[j] == '}':
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            if end < 0:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:end]
                opts, depth, cur = [], 0, []
                for ch in body:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                    if ch == ',' and depth == 0:
                        opts.append(''.join(cur))
                        cur = []
                    else:
                        cur.append(ch)
                opts.append(''.join(cur))
                out.append('(?:' + '|'.join(_glob_to_regex(o) for o in opts) + ')')
                i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return ''.join(out)


def _acc_str_matches(pattern: str, value: str) -> bool:
    """text/desc 单字段匹配：无通配符精确相等；含 * ? [] {} 时整串通配匹配。"""
    if not any(ch in pattern for ch in '*?[]{}'):
        return pattern == value
    return re.fullmatch(_glob_to_regex(pattern), value or '', re.DOTALL) is not None


def _acc_node_matches(node: dict, acc_id: str, acc_text: str, acc_desc: str) -> bool:
    """
    服务端复刻探针设备端 nodeMatches（ProbeService.java nodeMatches/globMatch，v2.15）：
    id 精确相等；text/desc 无通配符时精确相等（不再 contains，避免「设置」误点
    「其他设置」），含通配符时按 glob 规则整串匹配；填了的条件需同时满足。
    """
    if acc_id and str(node.get("id", "") or "") != acc_id:
        return False
    if acc_text and not _acc_str_matches(acc_text, str(node.get("text", "") or "")):
        return False
    if acc_desc and not _acc_str_matches(acc_desc, str(node.get("desc", "") or "")):
        return False
    return True


def _acc_tree_find(root: dict, acc_id: str, acc_text: str, acc_desc: str) -> bool:
    """
    在探针 0x0C 返回的无障碍树 JSON 中深度优先查找匹配节点。
    设备端 matchWithAncestorId 的祖先组合对纯存在判断是冗余的
    （完整匹配的祖先本身也会被 DFS 访问到），故只做直接匹配即可与
    0x0D 点击保持同一命中语义。
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, dict) and _acc_node_matches(node, acc_id, acc_text, acc_desc):
            return True
        stack.extend(node.get("children") or [])
    return False


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
    elif kind == "accExists":
        # 无障碍组件存在判断：经探针 0x0C 拉当前窗口组件树，服务端按与 0x0D 相同的
        # 匹配语义（id 精确、text/desc 包含）查找节点，存在 = 条件成立（只查不点）
        from routes.probe import run_probe

        acc_id = str(step.get("id", "") or "").strip()
        acc_text = str(step.get("text", "") or "").strip()
        acc_desc = str(step.get("desc", "") or "").strip()
        d = run_probe(device_id, False, 0x0C, {})
        if not d.get("connected"):
            ok = False
            reason = f"探针通讯失败: {d.get('reason', '未知原因')}"
        else:
            response = d.get("response") or {}
            data = (response.get("data") or {}) if isinstance(response, dict) else {}
            if isinstance(response, dict) and response.get("code") == 200:
                root = data.get("root")
                if isinstance(root, dict):
                    cond_text = f"id={acc_id or '-'} text={acc_text or '-'} desc={acc_desc or '-'}"
                    ok = _acc_tree_find(root, acc_id, acc_text, acc_desc)
                    reason = f"无障碍组件{'存在' if ok else '不存在'}（{cond_text}）"
                    # 树超过 150 节点被截断时，未命中可能是漏扫而非真不存在，明示出来
                    if not ok and data.get("truncated"):
                        reason += "；组件树超出 150 节点上限被截断，结果可能不完整"
                else:
                    ok = False
                    reason = "无障碍树返回异常（root 缺失）"
            else:
                message = (response.get("message") if isinstance(response, dict) else None) or "设备返回异常"
                ok = False
                reason = f"读取无障碍树失败: {message}"
    else:
        reason = f"未知判断类型: {kind}"

    if negate:
        ok = not ok
        reason += "（已取反）"
    return ok, reason


def _inject_gesture_step(session, step: dict, i: int, w: int, h: int, scale_x: float, scale_y: float,
                         round_no: int, interval: int, stop_event, count: int):
    """
    执行一次手势注入（tap/longpress/swipe/input），供重试包装复用

    从 _run_script_on_device 的循环体原样抽出，行为不变；
    抽出的目的是让手势步骤也能套 _run_step_with_retry。
    """
    stype = step.get("type")

    def _sx(v) -> float:
        return v * scale_x

    def _sy(v) -> float:
        return v * scale_y

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
            session.inject_touch(2, x1 + (x2 - x1) * k / seg, y1 + (y2 - y1) * k / seg, w, h)
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
        return
    if i < count - 1:
        _interruptible_sleep(interval, stop_event)


def _run_step_with_retry(step: dict, stop_event, runner, step_no: int = 0):
    """
    执行步骤，失败时按步骤的 retry/retryInterval 重试

    retry=0（默认）等价于直接调用 runner 一次，行为与改造前完全一致。
    重试只针对执行失败（RuntimeError 等）；用户停止（_ScriptStopped）不重试。
    """
    retries = max(0, int(step.get("retry", 0) or 0))
    retry_interval = max(0, int(step.get("retryInterval", 2000) or 0))
    last_error = None
    for attempt in range(retries + 1):
        try:
            return runner()
        except _ScriptStopped:
            raise
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            logger.warning(f"步骤 {step_no} 第 {attempt + 1} 次执行失败，{retry_interval / 1000:g}s 后重试: {e}")
            _interruptible_sleep(retry_interval, stop_event, raise_on_stop=True)
    raise last_error


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

    # 本次执行的独立日志目录：log/{设备号}_{执行时间}/run.log
    log_dir = _new_script_log_dir(device_id)
    log_handler = _attach_script_log_handler(log_dir)
    script_name = script.get("name") or "(未命名)"
    steps = script.get("steps", [])
    logger.info(
        f"[{device_id}] 开始执行脚本「{script_name}」repeat={repeat} steps={len(steps)} "
        f"日志目录: {log_dir}"
    )

    # 同设备并发执行防护：拿不到执行槽说明已有一个在跑。
    # 嵌套执行（runScript 步骤）复用父脚本的执行槽与停止标志，不重复占槽。
    stop_event = getattr(_script_nest_depth, "stop_event", None)
    nested = stop_event is not None
    if not nested:
        stop_event = _script_runs.get(device_id)
        if stop_event is not None:
            raise RuntimeError(f"设备 {device_id} 已有脚本在执行中，请先停止或等待完成")
        stop_event = threading.Event()
        _script_runs[device_id] = stop_event

    # 全脚本没有注入类步骤时不需要 scrcpy 会话
    # （设备动作与视觉等待都走 adb，纯这类脚本更快、更稳）
    needs_session = any(s.get("type") in INJECT_STEP_TYPES for s in steps)

    session = None
    w = h = 0
    scale_x = scale_y = 1.0
    current_step_no = 0

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
                current_step_no = idx + 1
                logger.info(f"[{device_id}] 执行步骤 {current_step_no}/{len(steps)}: {stype} (count={count}, interval={interval}ms)")

                # 视觉等待步骤：反复截屏搜索模板，命中才继续
                if stype in VISION_STEP_TYPES:
                    # 基准分辨率优先用脚本保存的录制尺寸，缺失则用会话尺寸
                    base_w = int(script.get("videoWidth") or 0) or w
                    base_h = int(script.get("videoHeight") or 0) or h
                    _run_step_with_retry(
                        step,
                        stop_event,
                        lambda: [_exec_wait_step(device_id, step, base_w, base_h, stop_event) for _ in range(count)],
                        current_step_no,
                    )
                # 设备动作类步骤走 adb 命令或探针帧（不依赖 scrcpy 会话），count 在函数内部循环
                elif stype in ADB_STEP_TYPES:
                    # 传入录制基准分辨率：accClick 兜底坐标换算用（缺省 0 时兜底不可用）
                    # _log_dir 供截图步骤落盘，_stop_event 供延时/等上线步骤响应停止
                    _run_step_with_retry(
                        step,
                        stop_event,
                        lambda: _exec_adb_step(
                            device_id,
                            {**step, "_base_w": int(script.get("videoWidth") or 0), "_base_h": int(script.get("videoHeight") or 0),
                             "_screen_w": w, "_screen_h": h,
                             "_log_dir": log_dir, "_stop_event": stop_event},
                            count,
                            round_no,
                        ),
                        current_step_no,
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
                        if not (0 <= next_idx < len(steps)):
                            raise RuntimeError(f"判断步骤跳转目标无效: 第 {branch.get('target')} 步")
                        idx = next_idx
                        continue
                    if action == "stop":
                        logger.info(f"[{device_id}] 判断步骤终止脚本 at step {idx + 1}")
                        return
                    if action == "fail":
                        raise RuntimeError(f"判断步骤未通过：{reason}")
                    if action == "runScript":
                        # 分支动作=执行脚本：同步嵌套跑完目标脚本再继续
                        _exec_run_script_step(device_id, {"script": branch.get("script", "")})
                    elif action in VALID_STEP_TYPES:
                        # 分支动作=执行某个步骤功能（手势/设备动作/等待）：
                        # 把分支参数当作一个临时步骤执行（参数可省，用默认值）
                        _run_branch_step(
                            device_id, action, branch, script, w, h, round_no,
                            session=session, scale_x=scale_x, scale_y=scale_y,
                        )
                else:
                    _run_step_with_retry(
                        step,
                        stop_event,
                        lambda: [
                            _inject_gesture_step(session, step, i, w, h, scale_x, scale_y, round_no, interval, stop_event, count)
                            for i in range(count)
                        ],
                        current_step_no,
                    )
                # 步与步之间的间隔（interval 单位毫秒）
                idx += 1
                if idx < len(steps):
                    _interruptible_sleep(interval, stop_event)
    except _ScriptStopped:
        logger.info(f"[{device_id}] 脚本被用户停止 at step {current_step_no}")
        raise
    except Exception as e:
        # 报错现场留痕：截当前画面 + 异常堆栈写入本次日志目录
        logger.exception(f"[{device_id}] 脚本执行报错 at step {current_step_no}: {e}")
        _capture_error_screen(device_id, log_dir, current_step_no)
        raise
    finally:
        logger.info(f"[{device_id}] 脚本执行结束，日志目录: {log_dir}")
        _detach_script_log_handler(log_handler)
        if not nested:
            # 嵌套执行共享父脚本的执行槽，只有顶层脚本负责释放
            _script_runs.pop(device_id, None)
        if session is not None:
            release_session(device_id, session)
        logger.info(f"Script run finished on {device_id}")


# 每台设备当前执行的停止标志（设备号 -> Event）
_script_runs: dict = {}


def _interruptible_sleep(ms: float, stop_event: threading.Event, raise_on_stop: bool = False):
    """
    可被停止请求提前唤醒的等待

    raise_on_stop=True 时，等待被停止请求打断会抛 _ScriptStopped（「固定延时」
    这类纯等待步骤必须抛——否则会白白跑完剩余步骤，停不下来）。
    """
    if ms <= 0:
        return
    if stop_event is None:
        time.sleep(ms / 1000)
        return
    stop_event.wait(ms / 1000)
    if raise_on_stop and stop_event.is_set():
        raise _ScriptStopped()


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
        path = _script_file(name, request.path)
        if not path.exists():
            return {"code": 404, "message": f"脚本 {request.name} 不存在", "data": None}
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


# ========== 设备端 JS 执行（方案3 P1：编译 → 推送 → 0x13） ==========

class ScriptRunJsRequest(BaseModel):
    """编译脚本 JSON 并在设备端以 JS 引擎（0x13）执行

    path 为脚本所在文件夹相对路径（"" 表示根目录），与 runScript 引用
    嵌套子脚本的解析规则一致。
    """
    deviceId: str
    name: str
    path: str = ""
    repeat: Optional[int] = None  # 不传则用脚本自身保存的 repeat


def _build_js_zip(name: str, script: dict, repeat: int) -> tuple:
    """编译 + 打包脚本 zip（script.js + metadata.json + apk/ + templates/），
    返回 (zip_path, apks, steps)"""
    from routes.script_compiler import compile_script, CompileError
    try:
        r = compile_script(script, SCRIPTS_DIR, APKS_DIR, name,
                           templates_dir=TEMPLATES_DIR)
    except CompileError as e:
        raise RuntimeError(str(e))
    build_dir = get_data_path() / "script_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    zip_path = build_dir / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("script.js", r["js"])
        zf.writestr("metadata.json", json.dumps(
            {"name": name, "repeat": repeat, "steps": r["steps"],
             "videoWidth": r["videoWidth"], "videoHeight": r["videoHeight"],
             "apiVersion": 1}, ensure_ascii=False).encode("utf-8"))
        for apk in r["apks"]:
            zf.write(APKS_DIR / apk, f"apk/{apk}")
        for tpl in r.get("templates") or []:
            zf.write(TEMPLATES_DIR / tpl, f"templates/{tpl}")
    return zip_path, r["apks"], r["steps"]


@router.post("/device/script/run-js")
def run_script_js(request: ScriptRunJsRequest):
    """编译脚本并推送到设备，用设备端 JS 引擎（0x13）同步执行。

    与 PC 引擎 /device/script/run 不同：脚本在设备本地解释执行（Rhino），
    不依赖 scrcpy 会话；坐标在设备端按 videoWidth/Height 换算。
    同步返回 dfm.log 收集的逐步日志；执行时长受帧协议 RESPONSE_TIMEOUT=120s 限制。
    """
    try:
        if not _check_device_online(request.deviceId):
            return {"code": 400, "message": f"设备 {request.deviceId} 不在线", "data": None}
        _ensure_scripts_dir()
        path = _script_file(request.name, request.path)
        if not path.exists():
            return {"code": 404, "message": f"脚本 {request.name} 不存在", "data": None}
        script = json.loads(path.read_text(encoding="utf-8"))
        repeat = max(1, int(request.repeat if request.repeat is not None else script.get("repeat", 1)))

        # 编译 + 打包（编译失败的消息直接面向用户：如不支持的功能/嵌套过深/APK 缺失）
        zip_path, apks, steps = _build_js_zip(request.name, script, repeat)

        # 推送 + 0x0E 解压 + 0x13 执行（probe 路由已有现成链路，直接函数级调用）
        from routes.probe import probe_script_import, probe_script_run_js
        imp = probe_script_import(type("Req", (), {"deviceId": request.deviceId, "zipPath": str(zip_path)})())
        resp = (imp.get("data") or {}).get("response") or {}
        if imp.get("code") != 200 or resp.get("code") != 200:
            return {"code": 500, "message": f"脚本推送失败: {imp.get('message')}" +
                    (f" / {resp.get('message')}" if resp else ""), "data": None}
        run = probe_script_run_js(type("Req", (), {"deviceId": request.deviceId,
                                                   "script": request.name, "repeat": repeat})())
        rresp = (run.get("data") or {}).get("response") or {}
        ok = run.get("code") == 200 and rresp.get("code") == 200
        rdata = rresp.get("data") or {}
        logs = rdata.get("logs") or []
        summary = rdata.get("summary") or {}
        script_error = rdata.get("error")
        # 设备信封 code=200 只代表帧通讯成功，脚本本身的成败看 summary.ok
        ok = ok and bool(summary.get("ok", True))
        logger.info(f"Script JS run finished: {request.name} repeat={repeat} ok={ok} "
                    f"steps={steps} durationMs={summary.get('durationMs')}")
        if not ok:
            # 设备端执行失败：把日志带回（失败原因在最后几条日志里）
            tail = " | ".join((lg.get("message") or "")[:80] for lg in logs[-3:])
            msg = script_error or rresp.get("message") or "执行失败"
            return {"code": 500, "message": f"{msg}{('：' + tail) if tail else ''}",
                    "data": {"logs": logs, "summary": summary}}
        return {
            "code": 200,
            "message": "success",
            "data": {"engine": "device-js", "repeat": repeat, "stepCount": steps,
                     "apks": apks, "logs": logs, "summary": summary,
                     "truncated": bool(rdata.get("truncated"))},
        }
    except Exception as e:
        return {"code": 500, "message": f"设备端 JS 执行失败: {str(e)}", "data": None}


class ScriptRunJsStepsRequest(BaseModel):
    """内存步骤 → 设备端 JS 引擎执行（不落盘；面板执行/重复按钮默认走这里）"""
    deviceId: str
    steps: List[dict]
    repeat: int = 1
    videoWidth: Optional[int] = None
    videoHeight: Optional[int] = None


@router.post("/device/script/run-js-steps")
def run_js_steps(request: ScriptRunJsStepsRequest):
    """把面板传入的步骤编译为 script.js，推送到设备端 JS 引擎（0x13）同步执行。

    与 run-js 的区别：步骤来自面板内存（不读已保存脚本）；编译失败会抛出
    面向用户的消息（如 waitFor 等设备端暂不支持的功能），前端据此回退 PC 引擎。
    """
    try:
        if not _check_device_online(request.deviceId):
            return {"code": 400, "message": f"设备 {request.deviceId} 不在线", "data": None}
        error = _validate_steps(request.steps)
        if error:
            return {"code": 400, "message": f"步骤校验失败: {error}", "data": None}
        repeat = max(1, int(request.repeat or 1))
        script = {
            "name": "面板步骤",
            "steps": _normalize_steps(request.steps),
            "repeat": repeat,
        }
        if request.videoWidth and request.videoHeight:
            script["videoWidth"] = int(request.videoWidth)
            script["videoHeight"] = int(request.videoHeight)

        zip_path, apks, steps = _build_js_zip("current_steps", script, repeat)

        from routes.probe import probe_script_import, probe_script_run_js
        imp = probe_script_import(type("Req", (), {"deviceId": request.deviceId,
                                                   "zipPath": str(zip_path)})())
        resp = (imp.get("data") or {}).get("response") or {}
        if imp.get("code") != 200 or resp.get("code") != 200:
            return {"code": 500, "message": f"脚本推送失败: {imp.get('message')}" +
                    (f" / {resp.get('message')}" if resp else ""), "data": None}
        run = probe_script_run_js(type("Req", (), {"deviceId": request.deviceId,
                                                   "script": "current_steps",
                                                   "repeat": repeat})())
        rresp = (run.get("data") or {}).get("response") or {}
        ok = run.get("code") == 200 and rresp.get("code") == 200
        rdata = rresp.get("data") or {}
        logs = rdata.get("logs") or []
        summary = rdata.get("summary") or {}
        script_error = rdata.get("error")
        ok = ok and bool(summary.get("ok", True))
        if not ok:
            tail = " | ".join((lg.get("message") or "")[:80] for lg in logs[-3:])
            msg = script_error or rresp.get("message") or "执行失败"
            return {"code": 500, "message": f"{msg}{('：' + tail) if tail else ''}",
                    "data": {"logs": logs, "summary": summary}}
        return {
            "code": 200,
            "message": "success",
            "data": {"engine": "device-js", "repeat": repeat, "stepCount": steps,
                     "apks": apks, "logs": logs, "summary": summary},
        }
    except Exception as e:
        return {"code": 500, "message": f"设备端 JS 执行失败: {str(e)}", "data": None}


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
