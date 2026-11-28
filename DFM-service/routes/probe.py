import json
import shlex
import socket
import subprocess
import threading
import time
from typing import Union

from fastapi import APIRouter
from pydantic import BaseModel, Field

from config import logger
from routes.adb_utils import FileReadRequest, read_file_base64
from utils.tool_manager import get_adb_path, get_data_path, get_probe_apk_path

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

# 设备端离线脚本命令字（0x0E 解压 / 0x0F 列表 / 0x10 执行）
CMD_SCRIPT_UNZIP = 0x0E
CMD_SCRIPT_LIST = 0x0F
CMD_SCRIPT_RUN = 0x10
CMD_SCRIPT_RUN_JS = 0x13
CMD_SCRIPT_STOP = 0x14

# 脚本 zip 在设备端的固定推送地址（应用外部私有目录下）
DEVICE_SCRIPT_DIR = "/sdcard/Android/data/com.dfm.probe/files/script_in"

ACK_TIMEOUT = 0.2
MAX_RETRY = 3
RESPONSE_TIMEOUT = 120.0
CONNECT_TIMEOUT = 5.0
INSTALL_TIMEOUT = 180

_locks_lock = threading.Lock()
_device_locks = {}
_seq_by_device = {}
_seq_lock = threading.Lock()

# 连接复用池：device_id -> {"device_id","conn","reader","host","port","forward_port"}
# 命中时直接在旧连接上发帧，省去重复广播 / forward / 建连
_pool_lock = threading.Lock()
_conn_pool = {}


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


class ProbeSendRequest(BaseModel):
    """通用透传：cmd 可为 int 或字符串（支持 0x 前缀的十六进制/十进制）。"""
    deviceId: str
    cmd: Union[int, str]
    data: dict = Field(default_factory=dict)
    timeout: float = Field(default=30, ge=1, le=300)


def _device_lock(device_id: str) -> threading.Lock:
    with _locks_lock:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_locks[device_id] = lock
        return lock


def _next_seq(device_id: str) -> int:
    """帧序 0~255 循环递增，按设备分别计数。全局锁保护：0x14 停止
    请求绕过设备锁并发发送，与 0x13 并发取号必须互斥。"""
    with _seq_lock:
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


def recv_response(reader: FrameReader, seq: int, cmd: int, pending: tuple = None,
                  timeout: float = RESPONSE_TIMEOUT) -> tuple:
    """取同帧序的响应帧（命令字 cmd|0x80 或 0xFF），返回 (解析后的 JSON, 原始帧字节)。"""
    if pending is None:
        deadline = time.monotonic() + timeout
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


def _close_pool_entry(entry: dict) -> None:
    """关闭池条目持有的连接并回收其 forward 端口。"""
    try:
        entry["conn"].close()
    except Exception:
        pass
    fp = entry.get("forward_port")
    if fp is not None:
        try:
            _adb(entry["device_id"], "forward", "--remove", f"tcp:{fp}")
        except Exception as e:
            logger.warning(f"adb forward --remove failed: {e}")


def _drop_pooled(device_id: str) -> None:
    """丢弃指定设备的复用连接（install 重启 APK、连接失效时调用）。"""
    with _pool_lock:
        entry = _conn_pool.pop(device_id, None)
    if entry is not None:
        _close_pool_entry(entry)


def _probe_via_pool(device_id: str, cmd: int, request: dict, payload: bytes,
                    response_timeout: float = RESPONSE_TIMEOUT):
    """尝试复用池中连接完成一次请求，省去重复广播 / forward / 建连。

    命中且成功 → 连接放回池中并返回响应 dict；
    未命中或复用失败（连接失效 / ACK 超时）→ 关闭旧连接返回 None，由调用方走完整重建路径。
    仅可在该设备的锁内调用。
    """
    with _pool_lock:
        entry = _conn_pool.pop(device_id, None)
    if entry is None:
        return None
    conn = entry["conn"]
    reader = entry["reader"]
    logger.info(f"probe pool device={device_id} 复用连接 {entry['host']}:{entry['port']}")
    try:
        seq = _next_seq(device_id)
        frame = pack_frame(seq, cmd, payload)
        frames = {"tx": frame.hex(" ").upper(), "ack": "", "rx": ""}
        got_ack, pending, ack_raw = recv_ack(reader, conn, seq, cmd, frame)
        frames["ack"] = ack_raw.hex(" ").upper()
        if not got_ack and pending is None:
            _close_pool_entry(entry)
            logger.info(f"probe pool device={device_id} 复用失败（ACK 超时），转重建")
            return None
        response, rx_raw = recv_response(reader, seq, cmd, pending, response_timeout)
        frames["rx"] = rx_raw.hex(" ").upper()
        with _pool_lock:
            _conn_pool[device_id] = entry
        return {
            "connected": True,
            "deviceId": device_id,
            "seq": seq,
            "cmd": cmd,
            "request": request,
            "response": response,
            "frame": frames["tx"],
            "frames": frames,
            "host": entry["host"],
            "port": entry["port"],
            "reused": True,
        }
    except Exception:
        _close_pool_entry(entry)
        logger.info(f"probe pool device={device_id} 复用失败（连接异常），转重建")
        return None


def _heartbeat_precheck(device_id: str):
    """业务命令发送前的心跳预检：探针不在线/卡死时快速失败，不等响应超时。

    返回 None 表示探针在线（可继续执行业务命令）；
    返回 dict 表示探针无响应，调用方直接把该结果返回给前端。
    心跳走与业务帧相同的停等确认：池内复用失败（ACK 超时/连接异常）即判不在线；
    池中无连接时走一次完整冷启动心跳（get-state/广播/forward/建连，约 0.5~4s），
    成功后连接已入池，后续业务命令直接复用。
    """
    request = {}
    payload = b"{}"
    with _pool_lock:
        has_pooled = device_id in _conn_pool
    if has_pooled:
        if _probe_via_pool(device_id, CMD_HEARTBEAT, request, payload) is not None:
            return None
        return {"connected": False, "reason": "探针不在线（心跳无响应，连接已失效）"}
    cold = _run_probe(device_id, False, CMD_HEARTBEAT, request, payload)
    if cold.get("connected"):
        return None
    return {"connected": False, "reason": f"探针不在线（{cold.get('reason', '心跳失败')}）"}


def run_probe(device_id: str, install: bool, cmd: int = CMD_HEARTBEAT, data: dict = None,
              response_timeout: float = RESPONSE_TIMEOUT) -> dict:
    """对指定设备发一帧业务帧并取回响应。

    adb 只负责安装、启动广播与 USB 端口映射；业务数据全部走设备 21104 上的帧协议。
    同一设备的请求串行化，避免帧序与 forward 端口互相干扰；等待锁最多 5 秒，
    超时返回「设备忙」（前一个请求可能卡在长耗时命令上）。
    install=True 时强制重建连接（APK 被覆盖安装，旧进程与连接全部失效）。
    业务命令（非心跳、非安装）发送前先做心跳预检，探针不在线时快速失败。
    """
    lock = _device_lock(device_id)
    if not lock.acquire(timeout=5.0):
        result = {"connected": False, "reason": "设备忙（上一个探针请求尚未结束）"}
        if cmd == CMD_HEARTBEAT:
            # 心跳被长任务挡住不算探针离线：带 busy 标记，前端跳过状态刷新
            result["busy"] = True
        return result
    try:
        request = data if data is not None else {}
        try:
            payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            return {"connected": False, "reason": f"数据区序列化失败: {e}"}
        if len(payload) > 65535:
            return {"connected": False, "reason": f"数据区超过 65535 字节: {len(payload)}"}
        # 心跳本身就是在线检测、安装是探针死亡后的恢复手段，两者豁免预检
        if not install and cmd != CMD_HEARTBEAT:
            pre = _heartbeat_precheck(device_id)
            if pre is not None:
                return pre
        if not install:
            pooled = _probe_via_pool(device_id, cmd, request, payload, response_timeout)
            if pooled is not None:
                return pooled
        return _run_probe(device_id, install, cmd, request, payload, response_timeout)
    finally:
        lock.release()


def _run_probe(device_id: str, install: bool, cmd: int, request: dict, payload: bytes,
               response_timeout: float = RESPONSE_TIMEOUT) -> dict:
    conn = None
    forward_port = None
    try:
        # install 重启 APK 会杀掉旧进程与连接，先丢弃池中旧连接
        if install:
            _drop_pooled(device_id)
        rc, out, err = _adb(device_id, "get-state")
        if rc != 0 or out != "device":
            _drop_pooled(device_id)
            return {"connected": False, "reason": f"设备不在线: {err or out}"}

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
        response, rx_raw = recv_response(reader, seq, cmd, pending, response_timeout)
        frames["rx"] = rx_raw.hex(" ").upper()

        # 请求成功，连接放入复用池（后续同设备请求直接在此连接上发帧）
        with _pool_lock:
            _conn_pool[device_id] = {
                "device_id": device_id,
                "conn": conn,
                "reader": reader,
                "host": host,
                "port": port,
                "forward_port": forward_port,
            }
        conn = None
        forward_port = None

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
            "reused": False,
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


def _result(device_id: str, install: bool, cmd: int = CMD_HEARTBEAT, data: dict = None,
            response_timeout: float = RESPONSE_TIMEOUT) -> dict:
    device_id = (device_id or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": {"connected": False}}
    d = run_probe(device_id, install, cmd, data, response_timeout)
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
    0x0E 脚本 zip 解压 / 0x0F 脚本列表 / 0x10 脚本执行，
    其余命令字由设备端回 0xFF CMD_ERROR。
    """
    return _result(req.deviceId, False, req.cmd, req.data)






@router.post("/probe/send")
def probe_send(req: ProbeSendRequest):
    """通用透传接口：把任意探针命令（cmd + data JSON）原样发给设备，
    返回解析后的响应（不限制命令字，前端调试/新命令联调用）。"""
    device_id = (req.deviceId or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": None}
    raw = req.cmd
    try:
        cmd = int(raw, 0) if isinstance(raw, str) else int(raw)
    except (TypeError, ValueError):
        return {"code": 400, "message": f"cmd 不合法: {raw}（示例 0x11 或 17）", "data": None}
    if not 0 <= cmd <= 0xFF:
        return {"code": 400, "message": f"cmd 超出范围 0x00~0xFF: {cmd}", "data": None}
    result = _result(device_id, False, cmd, req.data or {}, response_timeout=req.timeout)
    if isinstance(result.get("data"), dict):
        result["data"]["cmd"] = f"0x{cmd:02x}"
        result["data"]["cmdSent"] = {"cmd": cmd, "data": req.data or {}}
    return result



class ProbeScriptImportRequest(BaseModel):
    deviceId: str
    zipPath: str


class ProbeScriptRunRequest(BaseModel):
    deviceId: str
    script: str
    repeat: int = Field(1, ge=0, le=1000)


class ProbeScriptRunJsRequest(BaseModel):
    deviceId: str
    script: str
    repeat: int = Field(1, ge=1, le=1000)


@router.post("/probe/script-import")
def probe_script_import(req: ProbeScriptImportRequest):
    """把本地脚本 zip 推送到设备并解压。

    adb push 到设备固定地址 DEVICE_SCRIPT_DIR（files/script_in/），随后发 0x0E
    让设备端解压到 files/script_store/{脚本名}/；成功后删除设备端 zip 避免残留。
    zipPath 接受本地 zip 绝对路径（如 D:\\log\\script_exports\\xxx.zip）。
    """
    device_id = (req.deviceId or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": None}
    import os
    zip_path = (req.zipPath or "").strip()
    if not zip_path or not os.path.isfile(zip_path):
        return {"code": 400, "message": f"zip 文件不存在: {zip_path}", "data": None}
    zip_name = os.path.basename(zip_path)
    if not zip_name.lower().endswith(".zip"):
        return {"code": 400, "message": "仅支持 .zip 文件", "data": None}
    # 先确保目录存在（旧 APK 上可能还没有 script_in/）
    rc, out, err = _adb(device_id, "shell", "mkdir", "-p", DEVICE_SCRIPT_DIR)
    if rc != 0:
        return {"code": 500, "message": f"创建设备目录失败: {err or out}", "data": None}
    # adb push 直写 script_in 在部分设备/adb 版本上会触发 post-write fchown
    # 失败（remote fchown failed: Operation not permitted）并把文件回滚删除；
    # 先推到 /sdcard 根（不受限），再用 shell cp 挪进 script_in。
    tmp_push = f"/sdcard/{zip_name}.tmp"
    rc, out, err = _adb(device_id, "push", zip_path, tmp_push, timeout=600)
    if rc != 0:
        # 兜底：直接推目标目录（老 adb 无 fchown 问题）
        rc2, out2, err2 = _adb(device_id, "push", zip_path, f"{DEVICE_SCRIPT_DIR}/{zip_name}", timeout=600)
        if rc2 != 0:
            return {"code": 500, "message": f"adb push 失败: {err or err2 or out or out2}", "data": None}
    else:
        rc, out, err = _adb(device_id, "shell", "cp", tmp_push, f"{DEVICE_SCRIPT_DIR}/{zip_name}", timeout=30)
        _adb(device_id, "shell", "rm", "-f", tmp_push, timeout=15)
        if rc != 0:
            return {"code": 500, "message": f"设备内挪移 zip 失败: {err or out}", "data": None}
    # adb 为 root 时 push 出的文件属 root，应用读不到，这里修正属主
    #（命令整体作单参数传给 adb shell，避免多参数被 adb 拼接剥引号）
    files_dir = "/sdcard/Android/data/com.dfm.probe/files"
    _adb(device_id, "shell",
         f"O=$(stat -c %U {files_dir} 2>/dev/null); "
         f"G=$(stat -c %G {files_dir} 2>/dev/null); "
         f"[ -n \"$O\" ] && chown $O:$G {DEVICE_SCRIPT_DIR}/{zip_name} && "
         f"chmod 2770 {DEVICE_SCRIPT_DIR} && chmod 664 '{DEVICE_SCRIPT_DIR}/{zip_name}'",
         timeout=30)
    result = _result(device_id, False, CMD_SCRIPT_UNZIP, {"zip": zip_name})
    resp = (result.get("data") or {}).get("response") or {}
    if resp.get("code") == 200:
        # 解压成功后删除设备端 zip，避免 script_in 残留堆积
        try:
            _adb(device_id, "shell", "rm", "-f", f"{DEVICE_SCRIPT_DIR}/{zip_name}")
        except Exception as e:
            logger.warning(f"probe 删除设备端 zip 失败 device={device_id}: {e}")
    return result


@router.post("/probe/script-list")
def probe_script_list(req: ProbeRequest):
    """列出设备端已导入的脚本（0x0F），返回 script_store 下含 script.json 的目录。"""
    return _result(req.deviceId, False, CMD_SCRIPT_LIST, {})


@router.post("/probe/script-run")
def probe_script_run(req: ProbeScriptRunRequest):
    """执行设备端已导入的脚本（0x10），返回逐步骤成败日志。

    注意：脚本执行时长受帧协议 RESPONSE_TIMEOUT=120s 限制，超长脚本
    （如大 repeat、长 sleep）会因响应超时而失败；repeat=0 表示按脚本自身配置执行。
    """
    return _result(req.deviceId, False, CMD_SCRIPT_RUN,
                   {"script": req.script, "repeat": req.repeat})


@router.post("/probe/script-run-js")
def probe_script_run_js(req: ProbeScriptRunJsRequest):
    """执行设备端已导入的 JS 脚本（0x13，方案3 P0），返回 dfm.log 收集的日志。

    JS 引擎（Rhino）与 JSON 引擎并存：0x13 执行 script_store/{script}/script.js，
    0x10 仍执行 script.json。脚本同步执行，结束后一次性返回全部日志；
    执行时长受帧协议 RESPONSE_TIMEOUT=120s 限制，超长脚本用 /probe/script-stop
    停止（0x14 独立连接，不受当前执行阻塞）。
    """
    return _result(req.deviceId, False, CMD_SCRIPT_RUN_JS,
                   {"script": req.script, "repeat": req.repeat})


@router.post("/probe/script-stop")
def probe_script_stop(req: ProbeRequest):
    """停止正在执行的 JS 脚本（0x14），幂等。返回 {stopping, wasRunning, forceAbandoned}。

    forceAbandoned=true 表示脚本线程在停止后 3s 内未退出（如原型链卡死），
    已被放弃并放行后续脚本执行。

    停止请求必须绕过设备锁与连接池：执行中的 0x13 会持有设备锁（同步等待脚本结束），
    而停止恰恰要在这个窗口发出去——设备端 0x14 走独立连接、可与 0x13 并发处理。
    """
    device_id = (req.deviceId or "").strip()
    if not device_id:
        return {"code": 400, "message": "deviceId 不能为空", "data": None}
    try:
        payload = json.dumps({}, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        return {"code": 500, "message": f"数据区序列化失败: {e}", "data": None}
    # 不抢设备锁：设备端 0x14 与 0x13 并发安全（独立 TCP 连接 + 停止标志）
    d = _run_probe(device_id, False, CMD_SCRIPT_STOP, {}, payload,
                   response_timeout=30.0)
    connected = bool(d.get("connected"))
    logger.info(f"probe device={device_id} cmd=0x14 connected={connected} {d.get('reason', '')}")
    return {
        "code": 200,
        "message": "通讯正常" if connected else d.get("reason", "通讯失败"),
        "data": d,
    }
