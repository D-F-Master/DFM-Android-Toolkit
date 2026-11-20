import json
import shlex
import socket
import subprocess
import threading
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from config import logger
from routes.adb_utils import FileReadRequest, read_file_base64
from utils.tool_manager import get_adb_path, get_probe_apk_path

router = APIRouter(
    prefix="/api",
    tags=["探针"]
)

PACKAGE = "com.dfm.probe"
RECEIVER = PACKAGE + "/.StartReceiver"
ACTION = "com.dfm.probe.START"
DEVICE_PORT = 21104
FRAME_HEADER = b"\xaa\x55"

CMD_HEARTBEAT = 0x00
CMD_TTS = 0x01
CMD_DEVICE_INFO = 0x02
CMD_SERIAL_READ = 0x03
CMD_SHELL = 0x08
CMD_SCREENSHOT = 0x09
CMD_CAMERA = 0x0A
CMD_ACK = 0x06
CMD_ERROR = 0xFF

ACK_TIMEOUT = 0.2
MAX_RETRY = 3
RESPONSE_TIMEOUT = 120.0
CONNECT_TIMEOUT = 5.0
INSTALL_TIMEOUT = 180

_locks_lock = threading.Lock()
_device_locks = {}
_seq_by_device = {}


class ProbeRequest(BaseModel):
    deviceId: str


class ProbeMessageRequest(BaseModel):
    deviceId: str
    cmd: int = Field(..., ge=0, le=255)
    data: dict = {}


class ProbeCaptureRequest(BaseModel):
    deviceId: str
    kind: str = Field(..., pattern="^(screenshot|photo)$")
    cameraId: str = ""


def _device_lock(device_id: str) -> threading.Lock:
    with _locks_lock:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_locks[device_id] = lock
        return lock


def _next_seq(device_id: str) -> int:
    """帧序 0~255 循环递增，按设备分别计数。需在设备锁内调用。"""
    seq = (_seq_by_device.get(device_id, -1) + 1) & 0xFF
    _seq_by_device[device_id] = seq
    return seq


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


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus，与协议文档中的 C 实现一致。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def pack_frame(seq: int, cmd: int, payload: bytes) -> bytes:
    """组一帧；长度 2 字节低字节在前，数据区最长 65535 字节，CRC 低字节在前。"""
    if len(payload) > 65535:
        raise ValueError(f"数据区超过 65535 字节: {len(payload)}")
    body = bytes([seq & 0xFF, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF, cmd & 0xFF]) + payload
    crc = crc16_modbus(body)
    return FRAME_HEADER + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


class FrameReader:
    """增量解帧：处理粘包/半包，CRC 错帧丢弃后重新找帧头。"""

    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.buf = bytearray()

    def read_frame(self, timeout: float) -> tuple:
        deadline = time.monotonic() + timeout
        while True:
            frame = self._try_parse()
            if frame is not None:
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待帧超时")
            self.conn.settimeout(remaining)
            try:
                chunk = self.conn.recv(4096)
            except socket.timeout:
                raise TimeoutError("等待帧超时")
            if not chunk:
                raise RuntimeError("设备端连接已关闭")
            self.buf.extend(chunk)

    def _try_parse(self) -> tuple:
        while True:
            idx = self.buf.find(FRAME_HEADER)
            if idx < 0:
                # 只保留末尾可能是半个帧头的 0xAA
                if self.buf and self.buf[-1] == 0xAA:
                    self.buf = self.buf[-1:]
                else:
                    self.buf.clear()
                return None
            if idx > 0:
                del self.buf[:idx]
            if len(self.buf) < 6:
                return None
            seq = self.buf[2]
            length = self.buf[3] | (self.buf[4] << 8)
            cmd = self.buf[5]
            frame_len = 8 + length
            if len(self.buf) < frame_len:
                return None
            frame = bytes(self.buf[:frame_len])
            crc_lo = frame[6 + length]
            crc_hi = frame[7 + length]
            expect = crc16_modbus(frame[2:6 + length])
            if crc_lo != (expect & 0xFF) or crc_hi != ((expect >> 8) & 0xFF):
                # CRC 错：丢掉第一个字节，从后续字节重新找帧头，不回 ACK
                del self.buf[:1]
                continue
            del self.buf[:frame_len]
            # 第 4 项是收到的完整原始帧字节，页面帧日志直接展示真实字节、不重组
            return seq, cmd, frame[6:6 + length], frame


def recv_ack(reader: FrameReader, conn: socket.socket, seq: int, cmd: int, frame: bytes) -> tuple:
    """停等 ACK：T=ACK_TIMEOUT，超时用相同帧序重发，最多再发 MAX_RETRY 次。

    返回 (是否收到 ACK, 期间缓存的同帧序响应帧 (cmd, data, raw) 或 None, ACK 帧原始字节)。
    """
    pending = None
    got_ack = False
    ack_raw = b""
    for _ in range(MAX_RETRY + 1):
        conn.sendall(frame)
        deadline = time.monotonic() + ACK_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                r_seq, r_cmd, r_data, r_raw = reader.read_frame(remaining)
            except TimeoutError:
                break
            if r_seq != seq:
                continue
            if r_cmd == CMD_ACK:
                got_ack = True
                ack_raw = r_raw
                break
            if r_cmd in (cmd | 0x80, CMD_ERROR):
                pending = (r_cmd, r_data, r_raw)
        if got_ack or pending is not None:
            break
    return got_ack, pending, ack_raw


def recv_response(reader: FrameReader, seq: int, cmd: int, pending: tuple = None) -> tuple:
    """取同帧序的响应帧（命令字 cmd|0x80 或 0xFF），返回 (解析后的 JSON, 原始帧字节)。"""
    if pending is None:
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        while True:
            r_seq, r_cmd, r_data, r_raw = reader.read_frame(deadline - time.monotonic())
            if r_seq == seq and r_cmd in (cmd | 0x80, CMD_ERROR):
                pending = (r_cmd, r_data, r_raw)
                break
    return json.loads(pending[1].decode("utf-8")), pending[2]


def _connect_with_retry(host: str, port: int, attempts: int = 10, interval: float = 0.3) -> socket.socket:
    """START 广播是异步的，服务可能还没 bind 好，需要短暂重试。"""
    last = None
    for _ in range(attempts):
        try:
            return socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            last = e
            time.sleep(interval)
    raise RuntimeError(f"连接 {host}:{port} 失败: {last}")


def _suppress_notification(device_id: str) -> list:
    """设备侧隐藏探针通知的多层兜底，幂等，单条失败不影响其余命令。

    APK 内部已用 IMPORTANCE_NONE 渠道抑制显示；部分 ROM/系统版本不服从渠道抑制，
    这里再叠加两层：
    - appops POST_NOTIFICATION ignore：等价于在设置里手动关闭应用的「显示通知」开关；
    - pm revoke POST_NOTIFICATIONS：撤销 Android 13+ 的通知运行时权限，
      FGS 通知不再进通知栏（仅在 FGS 任务管理器可见）。
    """
    argv_list = [
        ("appops", "set", PACKAGE, "POST_NOTIFICATION", "ignore"),
        ("pm", "revoke", PACKAGE, "android.permission.POST_NOTIFICATIONS"),
    ]
    results = []
    for argv in argv_list:
        rc, out, err = _adb(device_id, "shell", *argv)
        detail = (out or err or "").strip()
        results.append(f"adb shell {' '.join(argv)} -> rc={rc} {detail}")
        logger.info(
            f"probe suppress-notification device={device_id} cmd={' '.join(argv)} rc={rc} {detail}"
        )
    return results


def run_probe(device_id: str, install: bool, cmd: int = CMD_HEARTBEAT, data: dict = None) -> dict:
    """对指定设备发一帧业务帧并取回响应。

    adb 只负责安装、启动广播与 USB 端口映射；业务数据全部走设备 21104 上的帧协议。
    同一设备的请求串行化，避免帧序与 forward 端口互相干扰。
    """
    with _device_lock(device_id):
        return _run_probe(device_id, install, cmd, data)


def _run_probe(device_id: str, install: bool, cmd: int, data: dict) -> dict:
    conn = None
    forward_port = None
    try:
        rc, out, err = _adb(device_id, "get-state")
        if rc != 0 or out != "device":
            return {"connected": False, "reason": f"设备不在线: {err or out}"}

        request = data if data is not None else {}
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(payload) > 65535:
            return {"connected": False, "reason": f"数据区超过 65535 字节: {len(payload)}"}

        if install:
            apk_path = get_probe_apk_path()
            if not apk_path.is_file():
                return {"connected": False, "reason": f"找不到 APK: {apk_path}"}
            rc, out, err = _adb(
                device_id, "install", "-r", str(apk_path), timeout=INSTALL_TIMEOUT
            )
            if rc != 0 or "Success" not in out:
                return {"connected": False, "reason": f"安装失败: {err or out}"}
            # 部署后自动叠加设备侧隐藏通知兜底（IMPORTANCE_NONE 渠道之外的第二、三层）
            _suppress_notification(device_id)

        # 每次都发幂等启动广播；服务已在监听时 onStartCommand 不会重复 bind
        rc, out, err = _adb(
            device_id, "shell", "am", "broadcast",
            "--include-stopped-packages", "-n", RECEIVER, "-a", ACTION,
        )
        if rc != 0:
            return {"connected": False, "reason": f"启动广播失败: {err or out}"}

        if ":" in device_id:
            # 网络设备：APK 启动后直接连它的 21104
            host = device_id.split(":")[0]
            port = DEVICE_PORT
        else:
            # USB 设备：用系统分配的随机本地端口做 forward，避免多设备抢端口
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            finally:
                probe.close()
            rc, out, err = _adb(
                device_id, "forward", f"tcp:{port}", f"tcp:{DEVICE_PORT}"
            )
            if rc != 0:
                return {"connected": False, "reason": f"adb forward 失败: {err or out}"}
            forward_port = port
            host = "127.0.0.1"

        conn = _connect_with_retry(host, port)
        reader = FrameReader(conn)
        seq = _next_seq(device_id)
        frame = pack_frame(seq, cmd, payload)
        # 页面帧日志展示真实收到的字节：发送帧 / ACK 帧 / 响应帧三段 hex
        frames = {"tx": frame.hex(" ").upper(), "ack": "", "rx": ""}
        got_ack, pending, ack_raw = recv_ack(reader, conn, seq, cmd, frame)
        frames["ack"] = ack_raw.hex(" ").upper()
        if not got_ack and pending is None:
            return {
                "connected": False,
                "reason": f"ACK 等待超时（共发 {MAX_RETRY + 1} 次）",
                "frames": frames,
            }
        response, rx_raw = recv_response(reader, seq, cmd, pending)
        frames["rx"] = rx_raw.hex(" ").upper()

        return {
            "connected": True,
            "deviceId": device_id,
            "seq": seq,
            "cmd": cmd,
            "request": request,
            "response": response,
            "frame": frames["tx"],
            "frames": frames,
            "host": host,
            "port": port,
        }
    except Exception as e:
        return {"connected": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if forward_port is not None:
            try:
                _adb(device_id, "forward", "--remove", f"tcp:{forward_port}")
            except Exception as e:
                logger.warning(f"adb forward --remove failed: {e}")
        # 服务常驻监听 21104，不再 force-stop


def _result(device_id: str, install: bool, cmd: int = CMD_HEARTBEAT, data: dict = None) -> dict:
    device_id = (device_id or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": {"connected": False}}
    d = run_probe(device_id, install, cmd, data)
    connected = bool(d.get("connected"))
    logger.info(
        f"probe device={device_id} install={install} cmd=0x{cmd:02x} "
        f"connected={connected} {d.get('reason', '')}"
    )
    return {
        "code": 200,
        "message": "通讯正常" if connected else d.get("reason", "通讯失败"),
        "data": d,
    }


@router.post("/probe/check")
def probe_check(req: ProbeRequest):
    """只做一次探测，不安装 APK。"""
    return _result(req.deviceId, False)


@router.post("/probe/connect")
def probe_connect(req: ProbeRequest):
    """安装/覆盖安装 APK 后再做一次探测，供页面点击「安装通讯」使用。"""
    return _result(req.deviceId, True)


@router.post("/probe/hide-notification")
def probe_hide_notification(req: ProbeRequest):
    """手动对已安装设备执行隐藏通知兜底（appops + Android 13+ 权限撤销），幂等。"""
    device_id = (req.deviceId or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": {"results": []}}
    results = _suppress_notification(device_id)
    return {"code": 200, "message": "已执行隐藏通知兜底命令", "data": {"results": results}}


@router.post("/probe/cameras")
def probe_cameras(req: ProbeRequest):
    """返回设备摄像头列表（id / facing / resolution），复用 0x02 设备详细信息帧。"""
    device_id = (req.deviceId or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": {"cameras": []}}
    d = run_probe(device_id, False, CMD_DEVICE_INFO, {})
    if not d.get("connected"):
        return {"code": 500, "message": d.get("reason", "通讯失败"), "data": {"cameras": []}}
    resp = d.get("response") or {}
    if resp.get("code") != 200:
        return {"code": 500, "message": resp.get("message", "设备返回错误"), "data": {"cameras": []}}
    cameras = (resp.get("data") or {}).get("cameras") or []
    return {"code": 200, "message": "success", "data": {"cameras": cameras}}


@router.post("/probe/capture")
def probe_capture(req: ProbeCaptureRequest):
    """截图 / 拍照。

    kind=screenshot 发 0x09 screencap，响应里带设备端路径，前端经 /device/files/base64 读取预览；
    kind=photo 先 pm grant 授予 CAMERA 运行时权限（probe 不弹授权框），再拉起透明
    GhostActivity 让进程短暂回到前台（Android 11 后台启动的 FGS 无 CAMERA capability，
    CameraService 会拒 CAMERA_DISABLED disabled by policy），然后发 0x0A 拍照。
    照片由服务端读成 base64 放进响应 data.response.data.base64，随后立即删除设备端文件，
    设备上不留存照片。
    """
    device_id = (req.deviceId or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": None}
    if req.kind == "screenshot":
        return _result(device_id, False, CMD_SCREENSHOT, {})
    rc, out, err = _adb(device_id, "shell", "pm", "grant", PACKAGE, "android.permission.CAMERA")
    if rc != 0:
        logger.warning(f"probe pm grant CAMERA device={device_id} rc={rc} {err or out}")
        return {"code": 500, "message": f"授予 CAMERA 权限失败: {err or out}", "data": None}
    # 拉起透明 GhostActivity（singleInstance、不拦截触摸、15s 自退）：
    # 让 probe 进程短暂进入前台，绕过 Android 11 while-in-use 相机限制。
    # 旧版 APK 无此 Activity 时 am start 失败，仍继续发帧（行为同旧版）。
    grc, gout, gerr = _adb(device_id, "shell", "am", "start", "-n", f"{PACKAGE}/.GhostActivity")
    if grc != 0:
        logger.warning(f"probe ghost activity device={device_id} rc={grc} {gerr or gout}")
    time.sleep(1.0)
    data = {"cameraId": req.cameraId} if req.cameraId else {}
    result = _result(device_id, False, CMD_CAMERA, data)
    resp = (result.get("data") or {}).get("response") or {}
    info = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    path = (info.get("path") or "").strip()
    if resp.get("code") == 200 and path:
        try:
            pulled = read_file_base64(FileReadRequest(deviceId=device_id, remotePath=path))
            if pulled.get("code") == 200:
                info["base64"] = (pulled.get("data") or {}).get("base64", "")
            else:
                info["readError"] = pulled.get("message", "读取照片失败")
        except Exception as e:
            info["readError"] = f"{type(e).__name__}: {e}"
        # 读成功与否都删除，避免设备端残留照片
        try:
            drc, _, derr = _adb(device_id, "shell", "rm", "-f", shlex.quote(path))
            info["deleted"] = drc == 0
            if drc != 0:
                logger.warning(f"probe delete photo failed device={device_id} path={path} {derr}")
        except Exception as e:
            info["deleted"] = False
            logger.warning(f"probe delete photo failed device={device_id} path={path} {e}")
    return result


@router.post("/probe/message")
def probe_message(req: ProbeMessageRequest):
    """按命令字向设备探针发一帧，返回设备应答，供页面调试。

    命令字接受 0~255 任意值，供页面「自定义发送」填写；设备端已登记的
    0x00 心跳 / 0x01 TTS / 0x02 设备详细信息 / 0x03 串口读取 / 0x04 打开串口会话 /
    0x05 串口发送 / 0x07 关闭串口会话 / 0x08 shell 命令执行 / 0x09 截屏 /
    0x0A 摄像头拍照 / 0x0B 跳转无障碍设置页 / 0x0C 无障碍组件树返回业务应答 /
    0x0D 无障碍点击（设备端按 id/text/desc 匹配节点并执行 ACTION_CLICK），
    其余命令字由设备端回 0xFF CMD_ERROR。
    """
    return _result(req.deviceId, False, req.cmd, req.data)
