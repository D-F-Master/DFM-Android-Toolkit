"""
MCP 服务端实现，封装 DFM-service（FastAPI，默认 http://127.0.0.1:3000）的设备探针接口。

工具分七组（7 个）：
- 无障碍树：get_accessibility_tree
- 组件点击：click_component
- 屏幕截图：get_screenshot
- 设备信息：device_info（0x02，支持关键字过滤）
- 文本输入：input_text（0x0D 定位 + 0x08 shell 输入，仅 ASCII）
- 应用列表：list_apps（转发 /api/device/apps）
- 启动应用：start_app（转发 /api/device/app/start）

前五者经 DFM-service 的探针帧接口 /api/probe/message 转发到设备上的 DFMProbe，
list_apps / start_app 经 DFM-service 的 ADB 路由（/api/device/apps、/api/device/app/start）：
- 0x02：返回设备基础信息、串口 / 摄像头 / 外设列表
- 0x08：root shell，用于 input keyevent / input text
- 0x0C：返回当前页面无障碍组件树
- 0x0D：按 id/text/desc 匹配节点并执行 ACTION_CLICK
- 0x09：设备端 screencap，返回设备路径，再经 /api/device/files/base64 读回本地保存

所有工具返回 DFM-service 统一信封 {code, message, data}：code=200 成功；
code=-1 表示无法连接 DFM-service；450 表示探针离线；500 表示设备端返回错误。
前提：DFM-service 已由用户启动，且设备已安装并运行 DFMProbe。

使用官方 MCP SDK 2.x (MCPServer)。默认 stdio 传输（最适合本地客户端）；
带 --port 参数启动时使用 Streamable HTTP（供 DFM MCP 管理工具拉起，端点 http://127.0.0.1:<port>/mcp）。
"""
import argparse
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

try:
    from importlib.metadata import version as pkg_version
except ImportError:
    from importlib_metadata import version as pkg_version

# ========== 心跳上报（DFM MCP 管理工具） ==========
SERVER_NAME = "DFM_MCP_AIProbe_service"   # 与 manifest.json 的 name 保持一致
SERVER_VERSION = "0.1.0"                  # 与 manifest.json 的 version 保持一致
MANAGER_URL = os.environ.get("DFM_MCP_MANAGER_URL", "http://localhost:3000")

# ========== DFM-service 地址 ==========
SERVICE_URL = os.environ.get("DFM_SERVICE_URL", "http://127.0.0.1:3000")
_DEFAULT_TIMEOUT = 300   # 常规探针请求（秒）
_LONG_TIMEOUT = 600      # 读回截图 base64（秒）

# 截图保存在本地的目录，可用环境变量 DFM_AIPROBE_SCREENSHOT_DIR 覆盖
SCREENSHOT_DIR = Path(os.environ.get("DFM_AIPROBE_SCREENSHOT_DIR", r"D:\log\screenshots"))

# ========== 探针帧命令字（与 DFM-service/routes/probe.py 一致） ==========
CMD_DEVICE_INFO = 0x02
CMD_SHELL = 0x08
CMD_SCREENSHOT = 0x09
CMD_ACCESSIBILITY = 0x0C
CMD_CLICK = 0x0D

# 清空输入框用的退格次数上限（覆盖常见输入框，超出部分不会再有内容可删）
_CLEAR_BACKSPACES = 123
# 单条 shell 命令里拼接的 keyevent 个数上限，过长命令在部分设备上会被截断
_KEYEVENT_BATCH = 30


def _report_heartbeat(port: int):
    """每 10 秒向管理端上报一次心跳，携带版本、mcp SDK 版本与端口"""
    def loop():
        while True:
            try:
                payload = json.dumps({
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "mcpVersion": pkg_version("mcp"),
                    "port": port,
                }).encode("utf-8")
                req = urllib.request.Request(
                    MANAGER_URL + "/api/mcp/heartbeat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass  # 管理端未启动时静默跳过
            time.sleep(10)

    threading.Thread(target=loop, daemon=True).start()


# ========== DFM-service HTTP 封装 ==========

def _post(path: str, payload: dict | None = None, timeout: int = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """POST JSON 到 DFM-service，原样返回统一信封 {code,message,data}；传输失败返回 code=-1 信封"""
    try:
        req = urllib.request.Request(
            SERVICE_URL + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 422 参数校验等：FastAPI 会带 JSON 错误体，尽量透传
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            if isinstance(err_body, dict):
                return err_body
        except Exception:
            pass
        return {"code": e.code, "message": f"DFM-service HTTP {e.code}", "data": None}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"code": -1, "message": f"无法连接 DFM-service ({SERVICE_URL})，请确认服务已启动: {e}", "data": None}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"code": -1, "message": f"DFM-service 返回非 JSON 响应: {e}", "data": None}
    if not isinstance(body, dict):
        return {"code": -1, "message": "DFM-service 返回格式异常", "data": None}
    return body


def _probe_frame(device_id: str, cmd: int, data: dict | None = None) -> dict[str, Any]:
    """向设备探针发一帧，把 DFM-service 的两层信封（转发层 + 设备应答层）收成一层。

    成功（code=200）返回 {"code","message","data"}，data 为设备端 response.data；
    探针离线返回 code=450，设备端报错返回 code=500，转发层异常原样返回。
    """
    result = _post("/api/probe/message", {"deviceId": device_id, "cmd": cmd, "data": data or {}})
    if result.get("code") != 200:
        return result
    forwarded = result.get("data") or {}
    if not forwarded.get("connected"):
        return {"code": 450, "message": f"探针不在线：{forwarded.get('reason', '通讯失败')}", "data": None}
    response = forwarded.get("response") or {}
    if response.get("code") != 200:
        return {"code": 500, "message": response.get("message", "设备端返回错误"), "data": None}
    return {"code": 200, "message": "success", "data": response.get("data")}


def _shell(device_id: str, command: str, timeout: int = 30) -> dict[str, Any]:
    """执行设备端 shell（cmd=0x08，root=true）。

    input / am 这类命令注入按键事件需要 INJECT_EVENTS 权限，非 root 会被拒绝
    （实测 java.lang.SecurityException: Injecting to another application requires
    INJECT_EVENTS permission），所以固定走 su。
    成功返回 {"code":200,"message":"success","data":{stdout,stderr,exit,truncated}}。
    """
    return _probe_frame(device_id, CMD_SHELL, {"command": command, "root": True, "timeout": timeout})


def _input_text_command(text: str) -> str:
    """拼 input text 命令：双引号包住，转义 shell 会特殊解释的 \\ " $ `

    实测（设备 202600210090 / Android 11）：
    - % 在双引号内原样保留，转义成 %% 反而会输入字面量 %%；所以 % 不转义。
    - $ 不转义会被 shell 当变量替换（a$b → a）；` 不转义会当命令替换。
    - \\ 与 " 需反斜杠转义。; & | < > ( ) * 在双引号内安全，无需转义。
    """
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("$", "\\$").replace("`", "\\`"))
    return f'input text "{escaped}"'


def _match_keyword(value: Any, keyword: str) -> bool:
    """字符串化后做不区分大小写的包含匹配；容器按 JSON 文本匹配"""
    if isinstance(value, str):
        return keyword.lower() in value.lower()
    try:
        return keyword.lower() in json.dumps(value, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        return False


def _filter_info(info: dict, keyword: str) -> dict[str, Any]:
    """在 0x02 设备信息里按关键字过滤，返回命中项列表 + 未命中的顶层键名

    命中项统一为 {"key": 顶层字段名, "path": 命中位置, "value": 命中的值}：
    - 标量字段命中 → path 就是字段名；
    - 列表字段（serialPorts/cameras/peripherals）逐个元素匹配，
      path 形如 peripherals[2].name 或 peripherals[2]，既可按字段也可按整条命中。
    """
    hits = []
    for key, value in info.items():
        if isinstance(value, list):
            for i, item in enumerate(value):
                if _match_keyword(item, keyword):
                    hits.append({"key": key, "path": f"{key}[{i}]", "value": item})
                elif isinstance(item, dict):
                    for sub_key, sub_value in item.items():
                        if _match_keyword(sub_value, keyword):
                            hits.append({"key": key, "path": f"{key}[{i}].{sub_key}", "value": sub_value})
        elif _match_keyword(value, keyword):
            hits.append({"key": key, "path": key, "value": value})
    return {"hits": hits, "missingKeys": [k for k in info if k not in {h["key"] for h in hits}]}


def _guard(**fields) -> dict[str, Any] | None:
    """必填字符串非空校验：任一为空返回 code=400 错误信封，全部通过返回 None"""
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            return {"code": 400, "message": f"参数错误：{name} 不能为空", "data": None}
    return None


def _write_screenshot(base64_text: str, device_id: str) -> Path:
    """把截图 base64 写到 SCREENSHOT_DIR/{设备目录}/，返回本地路径

    设备 ID 可能是 192.168.0.60:5555 或序列号，冒号在 Windows 路径里非法，替换为下划线。
    """
    name = (
        device_id.strip().replace(":", "_").replace("\\", "_").replace("/", "_").replace("*", "_")
        .replace("?", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("|", "_")
    )
    out_dir = SCREENSHOT_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
    path.write_bytes(base64.b64decode(base64_text, validate=True))
    return path


def _build_server() -> MCPServer:
    """构建并注册全部工具的 MCPServer 实例"""
    server = MCPServer(
        name="DFM-MCP-AIProbe-Service",
        version=SERVER_VERSION,
        instructions="AI 探针 MCP 服务，读取设备无障碍树、点击组件、获取截图、读取设备信息、输入文本、"
                     "获取已安装应用列表、启动应用。"
                     "前提：DFM-service 已运行（默认 http://127.0.0.1:3000，可用环境变量 DFM_SERVICE_URL 覆盖），"
                     "且目标设备已安装并运行 DFMProbe 探针。"
                     "所有工具返回统一信封 {code,message,data}：200 成功，400 参数错误，450 探针不在线，"
                     "500 设备端返回错误，-1 无法连接 DFM-service。",
    )

    # --- 无障碍树 ---
    @server.tool(name="get_accessibility_tree", title="获取无障碍树",
                 description="读取设备当前页面的无障碍组件树。返回 data.package 当前包名、data.nodeCount 节点总数、"
                             "data.truncated 是否被截断、data.root 根节点（含 class/text/desc/id/bounds/clickable/"
                             "scrollable/enabled/children 递归结构）。可用节点的 id/text/desc 作为 click_component 的匹配值")
    def _get_accessibility_tree(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _probe_frame(deviceId.strip(), CMD_ACCESSIBILITY)

    # --- 组件点击 ---
    @server.tool(name="click_component", title="点击组件",
                 description="按 id/text/desc 匹配设备当前页面的组件并点击（三者至少填一项，都可填时需同时匹配）。"
                             "返回 data.clicked 是否点击成功，以及实际命中的 id/text/desc。"
                             "匹配值与 get_accessibility_tree 返回值同源")
    def _click_component(deviceId: str, id: str = "", text: str = "", desc: str = "") -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        matched = {k: v.strip() for k, v in (("id", id), ("text", text), ("desc", desc))
                   if isinstance(v, str) and v.strip()}
        if not matched:
            return {"code": 400, "message": "参数错误：id/text/desc 至少填一项", "data": None}
        return _probe_frame(deviceId.strip(), CMD_CLICK, matched)

    # --- 设备信息（0x02 + 服务端关键字过滤） ---
    @server.tool(name="device_info", title="获取设备信息",
                 description="读取设备信息（0x02）：androidVersion/sdk/model/manufacturer/device/displayVersion/"
                             "sn/buildDate/kernelVersion，以及 serialPorts/cameras/peripherals 三个列表。"
                             "可选 keyword：不传返回全量；传了则在服务端按关键字过滤（不区分大小写、包含匹配，"
                             "列表项支持命中子字段），返回 data.hits（每项含 key/path/value）与 data.missingKeys"
                             "（未命中的顶层字段名）。用于确认型号、摄像头、串口或 USB 外设是否在设备上")
    def _device_info(deviceId: str, keyword: str = "") -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        info = _probe_frame(deviceId.strip(), CMD_DEVICE_INFO)
        if info.get("code") != 200:
            return info
        payload = info.get("data") if isinstance(info.get("data"), dict) else {}
        word = keyword.strip() if isinstance(keyword, str) else ""
        if not word:
            return {"code": 200, "message": "success", "data": payload}
        filtered = _filter_info(payload, word)
        return {
            "code": 200,
            "message": "success" if filtered["hits"] else f"未匹配到关键字：{word}",
            "data": {
                "keyword": word,
                "matched": len(filtered["hits"]),
                **filtered,
            },
        }

    # --- 已安装应用列表 ---
    @server.tool(name="list_apps", title="获取应用列表",
                 description="获取设备上已安装的第三方应用包名列表。返回 data 为包名数组。"
                             "列表中的包名可作为 start_app 的 packageName")
    def _list_apps(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/apps", {"deviceId": deviceId.strip()})

    # --- 启动已安装应用 ---
    @server.tool(name="start_app", title="启动应用",
                 description="启动设备上已安装的应用。packageName 为包名（可用 list_apps 获取）；"
                             "activity 可选：填了用 am start -n <pkg>/<activity>，不填用 monkey LAUNCHER 方式拉起。"
                             "返回 data 为 null 表示已下发启动命令")
    def _start_app(deviceId: str, packageName: str, activity: str = "") -> dict[str, Any]:
        err = _guard(deviceId=deviceId, packageName=packageName)
        if err:
            return err
        payload: dict[str, Any] = {"deviceId": deviceId.strip(), "packageName": packageName.strip()}
        if isinstance(activity, str) and activity.strip():
            payload["activity"] = activity.strip()
        return _post("/api/device/app/start", payload)

    # --- 文本输入 ---
    @server.tool(name="input_text", title="输入文本",
                 description="向输入框输入文本：可先按 id/text/desc 定位并点击输入框（都不填则直接对当前焦点输入），"
                             "clear=true（默认）先清空原内容再输入。"
                             "注意：仅支持 ASCII（设备端 input text 不支持非 ASCII，传中文会返回 400）；"
                             "命令经 root shell 执行，\\ \" $ ` 会自动转义。"
                             "返回 data.text 实际输入的文本、data.target 命中的输入框")
    def _input_text(deviceId: str, text: str, clear: bool = True, id: str = "",
                    elementText: str = "", desc: str = "") -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        if not text:
            return {"code": 400, "message": "参数错误：text 不能为空", "data": None}
        # input text 无法传递非 ASCII 字符（设备端会抛 NullPointerException），本地直接拦掉
        if not all(0x20 <= ord(ch) <= 0x7E for ch in text):
            return {"code": 400,
                    "message": "参数错误：text 仅支持 ASCII 可见字符（设备端 input text 不支持中文与换行）",
                    "data": None}
        device_id = deviceId.strip()
        target = {k: v.strip() for k, v in (("id", id), ("text", elementText), ("desc", desc))
                  if isinstance(v, str) and v.strip()}
        if target:
            clicked = _probe_frame(device_id, CMD_CLICK, target)
            if clicked.get("code") != 200:
                return clicked
            time.sleep(0.3)  # 等软键盘与焦点就绪，否则清除退格可能落到旧焦点上

        commands = []
        if clear:
            # KEYCODE_MOVE_END(123) 把光标移到文本末尾，再连发 DEL(67) 逐字删除；
            # 分批发送：过长的单条命令在部分设备上会被截断
            commands.append("input keyevent 123")
            for _ in range(_CLEAR_BACKSPACES // _KEYEVENT_BATCH):
                commands.append("input keyevent " + " ".join(["67"] * _KEYEVENT_BATCH))
        commands.append(_input_text_command(text))

        for command in commands:
            result = _shell(device_id, command)
            if result.get("code") != 200:
                return result
            body = result.get("data") or {}
            if body.get("exit") != 0:
                return {"code": 500,
                        "message": f"设备端执行失败（exit={body.get('exit')}）：{(body.get('stderr') or '').strip()[:300]}",
                        "data": body}
        return {
            "code": 200,
            "message": "success",
            "data": {"deviceId": device_id, "text": text, "clear": clear, "target": target or None},
        }

    # --- 屏幕截图 ---
    @server.tool(name="get_screenshot", title="获取截图",
                 description="截取设备当前屏幕，保存到本地并返回图片路径。返回 data.localPath 本地图片绝对路径、"
                             "data.size 设备端 PNG 文件大小（字节）、data.devicePath 设备端路径、data.via 截图方式")
    def _get_screenshot(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        device_id = deviceId.strip()
        shot = _probe_frame(device_id, CMD_SCREENSHOT)
        if shot.get("code") != 200:
            return shot
        info = shot.get("data") if isinstance(shot.get("data"), dict) else {}
        remote_path = (info.get("path") or "").strip()
        if not remote_path:
            return {"code": 500, "message": "设备端截图成功但未返回图片路径", "data": info}
        pulled = _post("/api/device/files/base64", {"deviceId": device_id, "remotePath": remote_path},
                       timeout=_LONG_TIMEOUT)
        if pulled.get("code") != 200:
            return {"code": pulled.get("code", 500),
                    "message": f"读取截图失败：{pulled.get('message', '未知错误')}", "data": info}
        base64_text = (pulled.get("data") or {}).get("base64") or ""
        if not base64_text:
            return {"code": 500, "message": "读取截图失败：设备返回的 base64 为空", "data": info}
        try:
            local_path = _write_screenshot(base64_text, device_id)
        except (OSError, ValueError) as e:
            return {"code": 500, "message": f"保存截图到本地失败（{SCREENSHOT_DIR}）：{e}", "data": info}
        return {
            "code": 200,
            "message": "success",
            "data": {
                "deviceId": device_id,
                "localPath": str(local_path),
                "localSize": local_path.stat().st_size,
                "size": info.get("size"),
                "devicePath": remote_path,
                "via": info.get("via"),
            },
        }

    return server


server = _build_server()


def main():
    """主入口：默认 stdio 传输；带 --port 时用 Streamable HTTP 供管理工具拉起"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="指定端口则以 Streamable HTTP 运行")
    args = parser.parse_args()

    if args.port is None:
        # transport 默认 'stdio'，最适合桌面客户端（Claude Desktop/Cursor 等）
        server.run()
    else:
        _report_heartbeat(args.port)
        server.run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()