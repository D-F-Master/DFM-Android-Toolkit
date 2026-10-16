import argparse
import threading
import uvicorn
import webview
import sys
import ctypes

# 打包成 exe 后以无控制台方式被拉起时，stdout/stderr 会退化为 GBK 编码，
# 打印 emoji（如 tool_check 里的 ✅）会抛 UnicodeEncodeError 导致启动失败，
# 这里强制重配置为 UTF-8 并容忍无法编码的字符
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from config import create_app, check_tool_availability, logger, get_conf

# ========== FastAPI 应用 ==========
app = create_app()

from config.html_config import router as html_config
from routes.adb_utils import router as adb_router
from routes.port_utils import router as port_router
from routes.common import router as common_router
from routes.ip_utils import router as ip_router
from routes.test_routes import router as test_router

app.include_router(adb_router)
app.include_router(port_router)
app.include_router(common_router)
app.include_router(ip_router)
app.include_router(test_router)
app.include_router(html_config)

# ========== 获取配置 ==========
CONF = get_conf()
VERSION = CONF["VERSION"]
HOST = CONF["HOST"]
PORT = CONF["PORT"]

# ========== 启动逻辑 ==========
def hide_console():
    """隐藏 Windows 控制台窗口"""
    if sys.platform == "win32":
        # 获取控制台窗口句柄
        wh = ctypes.windll.kernel32.GetConsoleWindow()
        if wh != 0:
            # 隐藏控制台窗口
            ctypes.windll.user32.ShowWindow(wh, 0)

def start_server(host: str = HOST):
    """启动 FastAPI 服务（阻塞）"""
    check_tool_availability()
    logger.info(f"Service started on {host}:{PORT}, version {VERSION}")
    uvicorn.run(app, host=host, port=PORT, log_level="warning")


def run_desktop():
    hide_console()
    """桌面版：后台线程跑服务 + 前台跑窗口"""
    server_thread = threading.Thread(
        target=start_server,
        args=("0.0.0.0",),
        daemon=True,
    )
    server_thread.start()

    webview.create_window(
        title=f"DFM-Android-Toolkit {VERSION}",
        url=f"http://127.0.0.1:{PORT}",
        width=1050,
        height=700,
        resizable=True,
        min_size=(1050, 700),
    )
    webview.start()


def run_server():
    """无桌面版：直接阻塞运行服务"""
    start_server()

# ========== CLI 入口 ==========
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["desktop", "server"],
        default="server",
        help="运行模式: desktop(桌面版) | server(无桌面版)",
    )

    args = parser.parse_args(["--mode", "server"] + sys.argv[1:])

    logger.info(f"CONFIG: {CONF}")

    if args.mode == "desktop":
        run_desktop()
    else:
        run_server()