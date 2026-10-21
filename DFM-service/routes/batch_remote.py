"""
批量远控 - scrcpy 设备会话模块

每台设备一个 DeviceSession：
1. adb push scrcpy-server 到 /data/local/tmp/scrcpy-server.jar 并启动
2. adb forward tcp:PORT localabstract:scrcpy_XXXXXXXX
3. 建立两条 TCP 连接（forward 端口相同）：视频 socket + 控制 socket
4. 视频 socket 收 H.264 流，控制 socket 发注入事件

协议依据 scrcpy v3.3.3 官方源码：
- app/src/server.c：push 路径 /data/local/tmp/scrcpy-server.jar、
  socket 名 "scrcpy_%08x"、tunnel_forward 模式客户端依次连视频/控制两个 socket
- server/.../device/DesktopConnection.java：forward 模式 server 依次 accept
  视频、控制连接，然后才在首个 socket 上发 64B 设备名；scid 为 8 位零填充 hex
- server/.../Streamer.java：视频 socket 先收 codec meta 12B（codecId u32 +
  width u32 + height u32），随后每帧 12B 头（bit63=config, bit62=keyframe,
  其余 PTS + u32 包长）
- app/src/control_msg.c：控制消息字节布局（INJECT_KEYCODE=0/TEXT=1/TOUCH=2/
  SCROLL=3），坐标为 i16 定点数 round(v*0xFFFF/(max-1))-0x8000
"""
import random
import socket
import struct
import subprocess
import threading
import queue
import time

from config import logger
from utils.tool_manager import get_adb_path, get_lib_path

SERVER_FILENAME = "scrcpy-server"
SERVER_PUSH_PATH = "/data/local/tmp/scrcpy-server.jar"

# 控制消息类型（与 scrcpy ControlMessageReader 一致）
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_INJECT_SCROLL_EVENT = 3

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
    """获取本地 scrcpy-server 文件完整路径"""
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
        # socket 名由 scid 生成（scrcpy_%08x），与 server 端 getSocketName 一致
        # scid：8 位零填充 hex 且首字符必须是数字（1-7 开头）——
        # adb shell 传参时字母开头的值会被远端 shell 当作选项吞掉，
        # 导致 server 端 NumberFormatException 退出
        self.scid_hex = f"{random.randint(0x10000000, 0x7FFFFFFF):08x}"
        self.socket_name = f"scrcpy_{self.scid_hex}"
        self.video_sock: socket.socket = None
        self.control_sock: socket.socket = None
        self._server_proc: subprocess.Popen = None
        self.frame_queue: queue.Queue = queue.Queue(maxsize=600)
        self.running = False
        self.device_name = device_id
        self.video_width = 0
        self.video_height = 0
        self._lock = threading.Lock()

    # ---------- 会话建立 ----------

    def start(self) -> bool:
        """推送 server、启动设备端进程、建立双 socket 连接"""
        try:
            server_path = get_server_path()

            # 先清理设备端可能残留的旧 server（上一次会话异常退出时
            # 只杀了本机 cmd 包装进程，adb shell 会话可能仍在设备上存活）
            _run_adb(f'-s {self.device_id} shell "pkill -f scrcpy_server_marker"')

            rc, _, err = _run_adb(
                f'-s {self.device_id} push "{server_path}" {SERVER_PUSH_PATH}'
            )
            if rc != 0:
                logger.error(f"[{self.device_id}] push server failed: {err}")
                return False

            rc, _, err = _run_adb(
                f"-s {self.device_id} forward tcp:{self.local_port} "
                f"localabstract:{self.socket_name}"
            )
            if rc != 0:
                logger.error(f"[{self.device_id}] adb forward failed: {err}")
                return False

            # tunnel_forward=true：server 监听 localabstract socket，
            # 客户端（本机）主动向 forward 端口发起连接。
            # 注意：命令不能用外层双引号包裹（Windows adb 会提前剥引号导致
            # 远端参数解析异常，server 发完设备名即退出），实测必须裸传
            shell_cmd = (
                f'"{get_adb_path()}" -s {self.device_id} shell '
                f"CLASSPATH={SERVER_PUSH_PATH} "
                f"app_process / com.genymobile.scrcpy.Server 3.3.3 "
                f"log_level=info scid={self.scid_hex} "
                f"video=true audio=false control=true cleanup=false "
                f"send_dummy_byte=false send_device_meta=true send_codec_meta=true "
                f"send_frame_meta=true "
                f"video_codec=h264 max_size=1280 "
                f"video_bit_rate=8000000 max_fps=60 stay_awake=true "
                f"tunnel_forward=true"
            )
            self._server_proc = subprocess.Popen(
                shell_cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # 依次建立视频、控制两条连接（server 依次 accept，顺序不能反）
            if not self._connect_sockets():
                logger.error(f"[{self.device_id}] connect sockets failed")
                return False

            # 首个（视频）socket 上读设备名 + codec meta（带超时防卡死）
            if not self._read_device_meta():
                logger.error(f"[{self.device_id}] read device meta failed")
                return False

            self.video_sock.settimeout(None)
            self.running = True
            threading.Thread(target=self._read_loop, daemon=True).start()
            logger.info(
                f"[{self.device_id}] session started "
                f"({self.video_width}x{self.video_height}, name={self.device_name})"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.device_id}] session start error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _connect_sockets(self) -> bool:
        """
        建立视频 + 控制两条 TCP 连接并等待设备名

        forward 模式下 TCP connect 总能成功（adbd 先 accept），即使 server
        还没监听。server 端 accept 之后就会向这条连接写数据，因此一旦建立
        连接就不能断开重连（重连的新 socket 不会再有数据），只能在同一
        socket 上等待——WiFi 设备 server 启动较慢，等待要留足时间。
        只有读到 EOF（server 已死）或 server 进程退出时才重建连接。
        """
        deadline = time.time() + 25
        while time.time() < deadline:
            if self._server_proc.poll() is not None:
                return False  # server 已退出（参数错误等）
            try:
                self.video_sock = socket.create_connection(
                    ("127.0.0.1", self.local_port), timeout=3)
                self.control_sock = socket.create_connection(
                    ("127.0.0.1", self.local_port), timeout=3)
                self.control_sock.settimeout(None)
                self.video_sock.settimeout(5.0)

                # 在同一条 socket 上等待 64B 设备名（超时继续等，EOF 才重连）
                name = self._recv_meta_wait(64, deadline)
                if name is not None:
                    self.device_name = name.split(b"\x00")[0].decode(
                        "utf-8", errors="replace")
                    return True
                # EOF：server 可能已退出，关 socket 走下一轮重连
                self._close_sockets()
            except OSError:
                self._close_sockets()
            if time.time() >= deadline:
                break
        return False

    def _recv_meta_wait(self, n: int, deadline: float):
        """
        在 video_sock 上等待读取 n 字节（用于会话建立阶段）

        超时（暂无数据）继续等到总 deadline；EOF 返回 None；
        成功返回完整字节串。部分数据不会丢弃。
        """
        buf = b""
        while len(buf) < n:
            if time.time() >= deadline:
                return None
            try:
                chunk = self.video_sock.recv(n - len(buf))
            except socket.timeout:
                continue  # 暂无数据，继续等
            except OSError:
                return None
            if not chunk:
                return None  # EOF
            buf += chunk
        return buf

    def _close_sockets(self):
        for attr in ("video_sock", "control_sock"):
            s = getattr(self, attr, None)
            if s:
                try:
                    s.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    def _read_device_meta(self) -> bool:
        """
        读取视频 socket 的 codec meta

        （设备名已在 _connect_sockets 中读完）
        codec meta（send_codec_meta=true）: codecId(4B) + width(4B) + height(4B)
        编码器初始化需要时间（首次可能数秒），用长超时等待
        """
        try:
            self.video_sock.settimeout(10.0)
            codec_meta = self._recv_exact(12)
            if not codec_meta:
                logger.error(f"[{self.device_id}] codec meta EOF")
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
            chunk = self.video_sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    # ---------- 控制注入（字节布局严格对齐 control_msg.c） ----------

    def _send_control(self, data: bytes):
        with self._lock:
            if self.control_sock and self.running:
                try:
                    self.control_sock.sendall(data)
                except OSError as e:
                    logger.warning(f"[{self.device_id}] control send failed: {e}")

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
        self._close_sockets()
        try:
            _run_adb(f"-s {self.device_id} forward --remove tcp:{self.local_port}", timeout=5)
        except Exception:
            pass
        # cleanup=false 时需手动清理设备端进程
        try:
            _run_adb(
                f'-s {self.device_id} shell "ps -A | grep app_process | grep -v grep '
                f'| awk \'{{print $2}}\' | xargs -r kill"',
                timeout=8,
            )
        except Exception:
            pass
        logger.info(f"[{self.device_id}] session stopped")
