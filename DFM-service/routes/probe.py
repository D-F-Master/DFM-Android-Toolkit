import json
import socket
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from config import logger
from utils.tool_manager import get_adb_path

router = APIRouter(
    prefix="/api",
    tags=["探针"]
)

PACKAGE = "com.dfm.probe"
RECEIVER = PACKAGE + "/.ConnectReceiver"
ACTION = "com.dfm.probe.CONNECT"
CONNECT_TIMEOUT = 15.0
# 自定义消息可能触发 TTS 朗读（引擎初始化最长 30s + 等待 onDone 最长 60s），单独放宽读超时
MESSAGE_TIMEOUT = 120.0
INSTALL_TIMEOUT = 180
# routes/probe.py -> routes/ -> DFM-service/ -> DFM-Android-Toolkit/
APK_PATH = Path(__file__).resolve().parents[2] / "DFM-probe" / "DFMProbe.apk"


class ProbeRequest(BaseModel):
    deviceId: str


class ProbeMessageRequest(BaseModel):
    deviceId: str
    message: dict


def _adb(device_id: str, *args: str, timeout: int = 15) -> tuple:
    """执行一条 adb 命令，返回 (returncode, stdout, stderr)。"""
    cmd = [get_adb_path(), "-s", device_id] + [str(a) for a in args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _read_line(conn: socket.socket, timeout: float) -> dict:
    """按行读取一条 JSON 消息，对端关闭或超时都抛异常。"""
    conn.settimeout(timeout)
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(4096)
        if not chunk:
            raise RuntimeError("设备端连接已关闭")
        buf += chunk
    return json.loads(buf.decode("utf-8").strip())


def _send(conn: socket.socket, obj: dict) -> None:
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def _stop_agent(device_id: str) -> None:
    """探测结束后停掉常驻服务，避免它对已关闭的端口空转重连。"""
    try:
        _adb(device_id, "shell", "am", "force-stop", PACKAGE)
    except Exception as e:
        logger.warning(f"force-stop {PACKAGE} failed: {e}")


def run_probe(device_id: str, install: bool, message: dict = None) -> dict:
    """
    对指定设备完成一次探针通讯：
    可选安装 APK -> 广播 PC 地址 -> 等设备反向连回 -> hello + 校验。

    message 为 None 时走默认 ping/pong 探测；否则把该 dict 原样发给设备，
    并把设备应答放进 data.response，供页面调试任意消息（含 tts）。

    使用 adb reverse 把设备上的 127.0.0.1:port 转发到 PC，
    这样 USB 设备与网络设备都能连通，也不依赖 PC 的入站防火墙规则。
    """
    listener = None
    conn = None
    reverse_port = None
    try:
        rc, out, err = _adb(device_id, "get-state")
        if rc != 0 or out != "device":
            return {"connected": False, "reason": f"设备不在线: {err or out}"}

        if install:
            if not APK_PATH.is_file():
                return {"connected": False, "reason": f"找不到 APK: {APK_PATH}"}
            rc, out, err = _adb(
                device_id, "install", "-r", str(APK_PATH), timeout=INSTALL_TIMEOUT
            )
            if rc != 0 or "Success" not in out:
                return {"connected": False, "reason": f"安装失败: {err or out}"}

        _adb(device_id, "shell", "am", "force-stop", PACKAGE)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        rc, out, err = _adb(
            device_id, "reverse", f"tcp:{port}", f"tcp:{port}"
        )
        if rc != 0:
            return {"connected": False, "reason": f"adb reverse 失败: {err or out}"}
        reverse_port = port

        rc, out, err = _adb(
            device_id, "shell", "am", "broadcast",
            "--include-stopped-packages", "-n", RECEIVER, "-a", ACTION,
            "--es", "host", "127.0.0.1", "--ei", "port", str(port),
        )
        if rc != 0:
            return {"connected": False, "reason": f"广播失败: {err or out}"}

        listener.settimeout(CONNECT_TIMEOUT)
        conn, addr = listener.accept()
        hello = _read_line(conn, CONNECT_TIMEOUT)
        if hello.get("type") != "hello":
            return {"connected": False, "reason": f"首包不是 hello: {hello}"}

        if message is None:
            request = {"type": "ping", "payload": f"probe-{int(time.time())}"}
            read_timeout = CONNECT_TIMEOUT
        else:
            request = message
            # tts 朗读可能要等引擎初始化(30s)与 onDone(60s)，单独放宽
            read_timeout = MESSAGE_TIMEOUT

        _send(conn, request)
        response = _read_line(conn, read_timeout)
        if message is None and (
            response.get("type") != "pong" or response.get("payload") != request["payload"]
        ):
            return {"connected": False, "reason": f"ping/pong 校验失败: {response}"}

        try:
            _send(conn, {"type": "quit"})
        except Exception:
            pass

        return {
            "connected": True,
            "deviceId": device_id,
            "host": "127.0.0.1",
            "port": port,
            "peer": str(addr),
            "hello": hello,
            "request": request,
            "response": response,
        }
    except Exception as e:
        return {"connected": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
        if reverse_port is not None:
            try:
                _adb(device_id, "reverse", "--remove", f"tcp:{reverse_port}")
            except Exception as e:
                logger.warning(f"adb reverse --remove failed: {e}")
        _stop_agent(device_id)


def _result(device_id: str, install: bool, message: dict = None) -> dict:
    device_id = (device_id or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": {"connected": False}}
    data = run_probe(device_id, install, message)
    connected = bool(data.get("connected"))
    logger.info(f"probe device={device_id} install={install} connected={connected} {data.get('reason', '')}")
    return {
        "code": 200,
        "message": "通讯正常" if connected else data.get("reason", "通讯失败"),
        "data": data,
    }


@router.post("/probe/check")
def probe_check(req: ProbeRequest):
    """只做一次探测，不安装 APK。"""
    return _result(req.deviceId, False)


@router.post("/probe/connect")
def probe_connect(req: ProbeRequest):
    """安装/覆盖安装 APK 后再做一次探测，供页面点击「安装通讯」使用。"""
    return _result(req.deviceId, True)


@router.post("/probe/message")
def probe_message(req: ProbeMessageRequest):
    """把一条自定义 JSON 消息发给设备探针，原样返回设备应答，供页面调试。"""
    return _result(req.deviceId, False, req.message)
