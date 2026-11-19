"""
MCP 服务端实现，封装 DFM-service（FastAPI，默认 http://127.0.0.1:3000）的设备管理接口。

工具分六组（25 个）：
- 设备：list_devices / get_device_info / run_adb_command
- 应用：list_apps / get_app_info / install_app / uninstall_app / start_app / stop_app / extract_apk
- 文件：browse_files / read_text_file / upload_file / download_file / make_device_dir / delete_device_file
- 按键音量：send_keyevent / send_basic_key / control_volume / get_volume_status
- 性能：get_performance_snapshot / get_monitor_rate / get_cpu_top
- 日志抓取：capture_logcat_start / capture_logcat_stop

所有工具转发到 DFM-service 的 HTTP 接口（统一 POST + {code,message,data} 信封）。
前提：DFM-service 已由用户启动；不可达时返回 code=-1 的错误信封，不会崩溃。

使用官方 MCP SDK 2.x (MCPServer)。默认 stdio 传输（最适合本地客户端）；
带 --port 参数启动时使用 Streamable HTTP（供 DFM MCP 管理工具拉起，端点 http://127.0.0.1:<port>/mcp）。
"""
import argparse
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

try:
    from importlib.metadata import version as pkg_version
except ImportError:
    from importlib_metadata import version as pkg_version

# ========== 心跳上报（DFM MCP 管理工具） ==========
SERVER_NAME = "DFM_MCP_device_service"   # 与 manifest.json 的 name 保持一致
SERVER_VERSION = "0.1.0"                 # 与 manifest.json 的 version 保持一致
MANAGER_URL = os.environ.get("DFM_MCP_MANAGER_URL", "http://localhost:3000")

# ========== DFM-service 地址 ==========
SERVICE_URL = os.environ.get("DFM_SERVICE_URL", "http://127.0.0.1:3000")
_DEFAULT_TIMEOUT = 300   # 常规 adb 操作（秒）
_LONG_TIMEOUT = 600      # 安装/上传/提取 APK 等大文件操作（秒）


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


def _post_multipart(path: str, fields: dict[str, str], file_field: str,
                    file_path: str, timeout: int = _LONG_TIMEOUT) -> dict[str, Any]:
    """multipart/form-data 上传本地文件（install_app / upload_file 用），返回统一信封"""
    fp = Path(file_path)
    if not fp.is_file():
        return {"code": 400, "message": f"本地文件不存在: {file_path}", "data": None}
    boundary = "----DFMMCPDevice" + uuid.uuid4().hex
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    buf.write(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{fp.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
    )
    buf.write(fp.read_bytes())
    buf.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    try:
        req = urllib.request.Request(
            SERVICE_URL + path,
            data=buf.getvalue(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
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


def _guard(**fields) -> dict[str, Any] | None:
    """必填字符串非空校验：任一为空返回 code=400 错误信封，全部通过返回 None"""
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            return {"code": 400, "message": f"参数错误：{name} 不能为空", "data": None}
    return None


def _build_server() -> MCPServer:
    """构建并注册全部工具的 MCPServer 实例"""
    server = MCPServer(
        name="DFM-MCP-Device-Service",
        version=SERVER_VERSION,
        instructions="设备管理 MCP 服务，封装 DFM-service 的 ADB 接口。"
                     "前提：DFM-service 已运行（默认 http://127.0.0.1:3000，可用环境变量 DFM_SERVICE_URL 覆盖）。"
                     "所有工具返回统一信封 {code,message,data}，code=200 成功，-1 表示无法连接 DFM-service。",
    )

    # --- 设备组 ---
    @server.tool(name="list_devices", title="获取设备列表", description="获取当前已连接的 ADB 设备列表")
    def _list_devices() -> dict[str, Any]:
        return _post("/api/devices")

    @server.tool(name="get_device_info", title="获取设备信息", description="获取指定设备的详细信息（状态/型号/系统版本/内核/构建日期/解锁状态）")
    def _get_device_info(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/info", {"deviceId": deviceId})

    @server.tool(name="run_adb_command", title="执行自定义 ADB 命令", description="执行任意 ADB 命令（慎用，命令需带 adb 前缀，如 'adb devices'）")
    def _run_adb_command(command: str) -> dict[str, Any]:
        err = _guard(command=command)
        if err:
            return err
        return _post("/api/command", {"command": command})

    # --- 应用组 ---
    @server.tool(name="list_apps", title="获取应用列表", description="获取设备上安装的第三方应用包名列表")
    def _list_apps(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/apps", {"deviceId": deviceId})

    @server.tool(name="get_app_info", title="获取应用信息", description="获取指定应用的版本信息（versionName/versionCode）与应用名")
    def _get_app_info(deviceId: str, packageName: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, packageName=packageName)
        if err:
            return err
        return _post("/api/device/app/info", {"deviceId": deviceId, "packageName": packageName})

    @server.tool(name="install_app", title="安装应用", description="把本地 APK 文件安装到设备（大文件耗时较长）")
    def _install_app(deviceId: str, localApkPath: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, localApkPath=localApkPath)
        if err:
            return err
        return _post_multipart("/api/device/app/install", {"deviceId": deviceId}, "apk", localApkPath)

    @server.tool(name="uninstall_app", title="卸载应用", description="从设备卸载指定包名的应用")
    def _uninstall_app(deviceId: str, packageName: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, packageName=packageName)
        if err:
            return err
        return _post("/api/device/app/uninstall", {"deviceId": deviceId, "packageName": packageName})

    @server.tool(name="start_app", title="启动应用", description="启动设备上的应用；activity 可选（填了用 am start -n，否则用 monkey LAUNCHER 方式）")
    def _start_app(deviceId: str, packageName: str, activity: str | None = None) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, packageName=packageName)
        if err:
            return err
        payload: dict[str, Any] = {"deviceId": deviceId, "packageName": packageName}
        if activity and activity.strip():
            payload["activity"] = activity.strip()
        return _post("/api/device/app/start", payload)

    @server.tool(name="stop_app", title="停止应用", description="停止（force-stop）设备上指定包名的应用")
    def _stop_app(deviceId: str, packageName: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, packageName=packageName)
        if err:
            return err
        return _post("/api/device/app/stop", {"deviceId": deviceId, "packageName": packageName})

    @server.tool(name="extract_apk", title="提取 APK", description="从设备提取指定应用的 APK 保存到本地路径（如 D:/output/app.apk）")
    def _extract_apk(deviceId: str, packageName: str, localPath: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, packageName=packageName, localPath=localPath)
        if err:
            return err
        return _post("/api/device/app/extract", {"deviceId": deviceId, "packageName": packageName, "localPath": localPath})

    # --- 文件组 ---
    @server.tool(name="browse_files", title="浏览设备文件系统", description="浏览设备指定路径下的文件与文件夹列表")
    def _browse_files(deviceId: str, path: str = "/sdcard/") -> dict[str, Any]:
        err = _guard(deviceId=deviceId, path=path)
        if err:
            return err
        return _post("/api/device/files/browse", {"deviceId": deviceId, "path": path})

    @server.tool(name="read_text_file", title="读取设备文本文件", description="读取设备上的文本文件全文；仅限文本文件，大文件建议用 download_file")
    def _read_text_file(deviceId: str, remotePath: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, remotePath=remotePath)
        if err:
            return err
        return _post("/api/device/files/read", {"deviceId": deviceId, "remotePath": remotePath})

    @server.tool(name="upload_file", title="上传文件到设备", description="上传本地文件到设备指定路径（remotePath 可选，默认 /sdcard/）")
    def _upload_file(deviceId: str, localPath: str, remotePath: str = "/sdcard/") -> dict[str, Any]:
        err = _guard(deviceId=deviceId, localPath=localPath, remotePath=remotePath)
        if err:
            return err
        return _post_multipart("/api/device/files/upload", {"deviceId": deviceId, "remotePath": remotePath}, "file", localPath)

    @server.tool(name="download_file", title="从设备下载文件", description="从设备下载文件到本地路径；localPath 目录不存在会自动创建，无扩展名或以 / 结尾视为目录")
    def _download_file(deviceId: str, remotePath: str, localPath: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, remotePath=remotePath, localPath=localPath)
        if err:
            return err
        return _post("/api/device/files/download", {"deviceId": deviceId, "remotePath": remotePath, "localPath": localPath})

    @server.tool(name="make_device_dir", title="创建设备文件夹", description="在设备上创建文件夹")
    def _make_device_dir(deviceId: str, path: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, path=path)
        if err:
            return err
        return _post("/api/device/files/mkdir", {"deviceId": deviceId, "path": path})

    @server.tool(name="delete_device_file", title="删除设备文件", description="删除设备上的文件或文件夹（危险操作，删除前请与用户确认路径）")
    def _delete_device_file(deviceId: str, path: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, path=path)
        if err:
            return err
        return _post("/api/device/files/delete", {"deviceId": deviceId, "path": path})

    # --- 按键音量组 ---
    @server.tool(name="send_keyevent", title="发送按键事件", description="向设备发送 Android keycode 按键事件（数字键值，如 4=返回 3=HOME 26=电源 24/25=音量）")
    def _send_keyevent(deviceId: str, keycode: int) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        if not isinstance(keycode, int) or isinstance(keycode, bool):
            return {"code": 400, "message": f"参数错误：keycode 必须是数字键值，收到 {keycode!r}", "data": None}
        return _post("/api/device/keyevent", {"deviceId": deviceId, "keycode": keycode})

    @server.tool(name="send_basic_key", title="发送常用按键", description="发送常用按键；key 支持 power/volume_up/volume_down/back/home/menu/recent/camera/search")
    def _send_basic_key(deviceId: str, key: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId, key=key)
        if err:
            return err
        return _post("/api/device/key", {"deviceId": deviceId, "key": key.strip()})

    @server.tool(name="control_volume", title="控制设备音量", description="控制设备音量；action 支持 up/down/mute/unmute，stream 支持 media/ring/alarm/notification（可选，默认 media）")
    def _control_volume(deviceId: str, action: str, stream: str = "media") -> dict[str, Any]:
        err = _guard(deviceId=deviceId, action=action)
        if err:
            return err
        return _post("/api/device/volume", {"deviceId": deviceId, "action": action.strip(), "stream": stream})

    @server.tool(name="get_volume_status", title="获取音量状态", description="获取设备当前音量状态（当前值/最大值/是否静音）；stream 支持 media/ring/alarm/notification（可选，默认 media）")
    def _get_volume_status(deviceId: str, stream: str = "media") -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/volume/status", {"deviceId": deviceId, "stream": stream})

    # --- 性能组 ---
    @server.tool(name="get_performance_snapshot", title="获取性能快照", description="获取设备内存占用、CPU 各核频率、/data 磁盘使用情况（单次 adb 往返）")
    def _get_performance_snapshot(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/monitor", {"deviceId": deviceId})

    @server.tool(name="get_monitor_rate", title="获取实时速率", description="采样获取设备 CPU 占用率与各网卡上传/下载速率；duration 为采样秒数 1-5（可选，默认 1）")
    def _get_monitor_rate(deviceId: str, duration: int = 1) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1 or duration > 5:
            return {"code": 400, "message": f"参数错误：duration 必须是 1-5 的整数，收到 {duration!r}", "data": None}
        return _post("/api/device/monitor/rate", {"deviceId": deviceId, "duration": duration})

    @server.tool(name="get_cpu_top", title="获取 CPU Top 进程", description="获取设备 CPU 占用 Top 10 进程快照（约 1.3-1.5 秒，含 1 秒设备端采样）")
    def _get_cpu_top(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/monitor/top", {"deviceId": deviceId})

    # --- 日志抓取组 ---
    @server.tool(name="capture_logcat_start", title="开始抓取日志", description="开始持续抓取设备前台应用的 logcat 日志到本地 D:\\log\\{SN}\\{时间}.log；重复调用幂等")
    def _capture_logcat_start(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/logs/capture/start", {"deviceId": deviceId})

    @server.tool(name="capture_logcat_stop", title="停止抓取日志", description="结束指定设备的日志抓取，返回日志文件路径与大小；没有进行中的抓取返回 code 400")
    def _capture_logcat_stop(deviceId: str) -> dict[str, Any]:
        err = _guard(deviceId=deviceId)
        if err:
            return err
        return _post("/api/device/logs/capture/stop", {"deviceId": deviceId})

    return server


def main():
    """主入口：默认 stdio 传输；带 --port 时用 Streamable HTTP 供管理工具拉起"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="指定端口则以 Streamable HTTP 运行")
    args = parser.parse_args()

    server = _build_server()
    if args.port is None:
        # transport 默认 'stdio'，最适合桌面客户端（Claude Desktop/Cursor 等）
        server.run()
    else:
        _report_heartbeat(args.port)
        server.run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
