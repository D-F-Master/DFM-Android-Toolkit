"""
批量远控 - scrcpy 设备会话模块

每台设备一个 DeviceSession：
1. adb push scrcpy-server 到设备并以 tunnel_forward=true 启动
2. Python 通过 adb forward 的 TCP 连接接收 H.264 流
3. 视频与控制共用一个 socket（v3.x 协议），控制消息从客户端方向写入
4. 帧数据通过队列交给 WebSocket 发送层

协议依据 scrcpy v3.3.3 源码（doc/develop.md + app/src/control_msg.c）：
- 帧/包头：12 字节 = 8B 大端（bit63=config标志, bit62=keyframe标志, 其余 PTS）
          + 4B 大端包长
- 控制消息 INJECT_KEYCODE(0)：type(1)+action(1)+keycode(4)+repeat(4)+metaState(4) = 14B
- 控制消息 INJECT_TEXT(1)：type(1)+len(4)+utf8（单条最长 300 字节，超长自动分段）
- 控制消息 INJECT_TOUCH_EVENT(2)：type(1)+action(1)+pointerId(8)
          +x(4)+y(4)+w(2)+h(2)+pressure(2)+actionButton(4)+buttons(4) = 32B
          坐标归一化：round(v * 0xFFFF / (max-1)) - 0x8000（sc_int16fp）
"""
import socket
import struct
import subprocess
import threading
import queue
import time

from config import logger
from utils.tool_manager import get_adb_path, get_lib_path

SERVER_FILENAME = "scrcpy-server"

# 控制消息类型（与 scrcpy ControlMessageReader 一致）
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_INJECT_SCROLL_EVENT = 3
TYPE_BACK_OR_SCREEN_ON = 4

# Android MotionEvent action
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2

# Android KeyEvent action
KEY_ACTION_DOWN = 0
KEY_ACTION_UP = 1

# 常用键码
KEYCODE_BACK = 4
KEYCODE_HOME = 3
KEYCODE_MENU = 82
KEYCODE_POWER = 26

# scrcpy 特殊 pointer id（uint64 下的 -2 即 GENERIC_FINGER）
POINTER_ID_GENERIC_FINGER = 0xFFFFFFFFFFFFFFFE


def get_server_path() -> str:
    """获取 scrcpy-server 文件完整路径"""
    lib_path = get_lib_path()
    for item in lib_path.iterdir():
        if item.is_dir() and item.name.startswith("scrcpy"):
            server = item / SERVER_FILENAME
            if server.exists():
                return str(server)
    raise FileNotFoundError("未找到 lib/scrcpy-*/scrcpy-server")


def _run_adb(args: str, timeout: int = 15):
    """执行 adb 命令，返回 (returncode, stdout, stderr)"""
    adb = get_adb_path()
    result = subprocess.run(
        f'"{adb}" {args}', shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


class DeviceSession:
    """单台设备的 scrcpy 会话：视频流接收 + 控制注入"""

    def __init__(self, device_id: str, local_port: int):
        self.device_id = device_id
        self.local_port = local_port
        # localabstract 名称需设备级唯一（多设备并行）
        tag = device_id.replace(":", "").replace(".", "")
        self.socket_name = f"dsh_batch_{tag}"
        self.sock: socket.socket = None
        self._server_proc: subprocess.Popen = None
        self.frame_queue: queue.Queue = queue.Queue(maxsize=600)
        self.running = False
        self.device_name = device_id
        self.video_width = 0
        self.video_height = 0

    # ---------- 会话建立 ----------

    def start(self) -> bool:
        """推送 server、启动设备端进程、建立连接"""
        try:
            server_path = get_server_path()

            rc, _, err = _run_adb(f'-s {self.device_id} push "{server_path}" /data/local/tmp')
            if rc != 0:
                logger.error(f"[{self.device_id}] push server failed: {err}")
                return False

            # adb forward：本机端口 → 设备端 localabstract socket
            rc, _, err = _run_adb(
                f"-s {self.device_id} forward tcp:{self.local_port} localabstract:{self.socket_name}"
            )
            if rc != 0:
                logger.error(f"[{self.device_id}] adb forward failed: {err}")
                return False

            # 启动 server。tunnel_forward=true：设备端监听，本机主动连接
            shell_cmd = (
                f'"{get_adb_path()}" -s {self.device_id} shell '
                f'CLASSPATH=/data/local/tmp/{SERVER_FILENAME} '
                f"app_process / com.genymobile.scrcpy.Server 3.3.3 "
                f"log_level=info scid={self._scid()} "
                f"video=true audio=false control=true cleanup=false "
                f"send_dummy_byte=false send_device_meta=true send_codec_meta=true "
                f"send_frame_meta=true raw_stream=false "
                f"video_codec=h264 video_encoder=default max_size=1280 "
                f"video_bit_rate=8000000 max_fps=60 stay_awake=true "
                f"tunnel_forward=true socket_name={self.socket_name}"
            )
            self._server_proc = subprocess.Popen(
                shell_cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            self.sock = self._connect_with_retry(("127.0.0.1", self.local_port), retries=24, delay=0.25)
            if not self.sock:
                logger.error(f"[{self.device_id}] connect to forwarded port failed")
                return False

            # 单 socket 模式（v3.x）：首个（唯一）socket 上先读 device meta
            if not self._read_device_meta():
                logger.error(f"[{self.device_id}] read device meta failed")
                return False

            self.running = True
            threading.Thread(target=self._read_loop, daemon=True).start()
            logger.info(
                f"[{self.device_id}] session started "
                f"({self.video_width}x{self.video_height}, name={self.device_name})"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.device_id}] session start error: {e}")
            return False

    @staticmethod
    def _scid() -> int:
        """随机 31 位 scid，同设备多客户端不冲突"""
        import random
        return random.randint(0, 0x7FFFFFFF)

    def _connect_with_retry(self, addr, retries: int, delay: float):
        for _ in range(retries):
            if self._server_proc.poll() is not None:
                return None
            try:
                s = socket.create_connection(addr, timeout=2)
                s.settimeout(None)
                return s
            except OSError:
                time.sleep(delay)
        return None

    def _read_device_meta(self) -> bool:
        """
        读取首 socket 的 device meta + codec meta

        v3.3.3（send_device_meta=true）：
        - device meta: 64B 设备名（CLIPBOARD 固定 64）
        - codec meta（send_codec_meta=true, video）: codecId(4B) + width(4B) + height(4B)
        """
        try:
            name_raw = self._recv_exact(64)
            if not name_raw:
                return False
            self.device_name = name_raw.split(b"\x00")[0].decode("utf-8", errors="replace")

            codec_meta = self._recv_exact(12)
            if not codec_meta:
                return False
            codec_id = codec_meta[:4]
            self.video_width, self.video_height = struct.unpack(">II", codec_meta[4:12])
            if codec_id != b"h264":
                logger.error(f"[{self.device_id}] unexpected codec {codec_id!r}")
                return False
            return True
        except OSError as e:
            logger.error(f"[{self.device_id}] meta read error: {e}")
            return False

    # ---------- 视频流读取 ----------

    def _read_loop(self):
        """按 12 字节包头切帧放入队列"""
        try:
            while self.running:
                header = self._recv_exact(12)
                if not header:
                    break
                # 头格式：8B (bit63=config, bit62=keyframe, 其余 PTS) + 4B len
                (packed,) = struct.unpack(">Q", header[:8])
                (payload_len,) = struct.unpack(">I", header[8:12])
                is_config = bool(packed >> 63 & 1)
                is_keyframe = bool(packed >> 62 & 1)

                payload = self._recv_exact(payload_len)
                if payload is None:
                    break

                if is_config:
                    self.frame_queue.put({"kind": "config", "data": payload})
                else:
                    self.frame_queue.put({
                        "kind": "frame", "data": payload, "keyframe": is_keyframe,
                    })
        except OSError as e:
            logger.warning(f"[{self.device_id}] read loop ended: {e}")
        finally:
            self.running = False
            try:
                self.frame_queue.put_nowait({"kind": "closed"})
            except queue.Full:
                pass

    def _recv_exact(self, n: int):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    # ---------- 控制注入（字节布局严格对齐 control_msg.c） ----------

    def _send_control(self, data: bytes):
        with self._io_lock():
            if self.sock and self.running:
                try:
                    self.sock.sendall(data)
                except OSError as e:
                    logger.warning(f"[{self.device_id}] control send failed: {e}")

    def _io_lock(self):
        if not hasattr(self, "_lock"):
            self._lock = threading.Lock()
        return self._lock

    @staticmethod
    def _norm_coord(v: float, max_v: int) -> int:
        """坐标归一化为 scrcpy 的 i16 定点数（sc_int16fp）"""
        if max_v <= 1:
            return 0
        return max(-0x8000, min(0x7FFF, round(v * 0xFFFF / (max_v - 1)) - 0x8000))

    def inject_touch(self, action: int, x: float, y: float, w: int, h: int,
                     pointer_id: int = POINTER_ID_GENERIC_FINGER,
                     buttons: int = 1):
        """
        注入触摸事件（32 字节定长）

        type(1) action(1) pointerId(8) x(4) y(4) w(2) h(2)
        pressure(2) actionButton(4) buttons(4)
        """
        nx, ny = self._norm_coord(x, w), self._norm_coord(y, h)
        pressure = 0xFFFF if action != ACTION_UP else 0
        msg = struct.pack(
            ">B B Q i i H H H I I",
            TYPE_INJECT_TOUCH_EVENT, action, pointer_id,
            nx, ny, w, h,
            pressure, buttons, buttons,
        )
        self._send_control(msg)

    def inject_keycode(self, keycode: int, action: int):
        """注入按键（14 字节定长）"""
        msg = struct.pack(">B B I I I", TYPE_INJECT_KEYCODE, action, keycode, 0, 0)
        self._send_control(msg)

    def tap_key(self, keycode: int):
        """按下并抬起一个键"""
        self.inject_keycode(keycode, KEY_ACTION_DOWN)
        self.inject_keycode(keycode, KEY_ACTION_UP)

    def inject_text(self, text: str):
        """
        注入文本：type(1)+len(4)+utf8，单条最长 300 字节（UTF-8 安全截断）
        """
        encoded = text.encode("utf-8")
        for i in range(0, len(encoded), 300):
            chunk = encoded[i:i + 300]
            # 防止把多字节字符截断
            while chunk and (chunk[-1] & 0xC0) == 0x80:
                chunk = chunk[:-1]
            msg = struct.pack(">BI", TYPE_INJECT_TEXT, len(chunk)) + chunk
            self._send_control(msg)

    def inject_scroll(self, x: float, y: float, hscroll: float, vscroll: float,
                      w: int, h: int, buttons: int = 0):
        """
        注入滚轮（21 字节定长）

        type(1) x(4) y(4) w(2) h(2) hscroll(2) vscroll(2) buttons(4)
        滚动值范围 [-16,16]，先归一化到 [-1,1] 再转 i16 定点
        """
        msg = struct.pack(
            ">B i i H H h h I",
            TYPE_INJECT_SCROLL_EVENT,
            self._norm_coord(x, w), self._norm_coord(y, h), w, h,
            max(-1, min(1, hscroll / 16)) * 0x7FFF,
            max(-1, min(1, vscroll / 16)) * 0x7FFF,
            buttons,
        )
        self._send_control(msg)

    # ---------- 清理 ----------

    def stop(self):
        self.running = False
        try:
            if self._server_proc and self._server_proc.poll() is None:
                self._server_proc.kill()
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        try:
            _run_adb(f"-s {self.device_id} forward --remove tcp:{self.local_port}", timeout=5)
        except Exception:
            pass
        # cleanup=false 时需手动清理设备端进程
        try:
            _run_adb(
                f'-s {self.device_id} shell "ps -A | grep app_process | grep {self.socket_name} '
                f'| grep -v grep | awk \'{{print $2}}\' | xargs -r kill"',
                timeout=8,
            )
        except Exception:
            pass
        logger.info(f"[{self.device_id}] session stopped")
