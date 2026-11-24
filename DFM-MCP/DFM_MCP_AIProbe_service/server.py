"""
MCP 服务端实现，封装 DFM-service（FastAPI，默认 http://127.0.0.1:3000）的设备探针接口。

工具分三组（3 个）：
- 无障碍树：get_accessibility_tree
- 组件点击：click_component
- 屏幕截图：get_screenshot

三者都经 DFM-service 的探针帧接口 /api/probe/message 转发到设备上的 DFMProbe：
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
CMD_SCREENSHOT = 0x09
CMD_ACCESSIBILITY = 0x0C
CMD_CLICK = 0x0D


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
        instructions="AI 探针 MCP 服务，读取设备无障碍树、点击组件、获取截图。"
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