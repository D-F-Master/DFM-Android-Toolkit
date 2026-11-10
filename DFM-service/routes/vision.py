"""视觉匹配 - 画面模板搜索与等待

用于「等待组件出现」类步骤：设备端截一张图作模板，
运行时反复截屏搜索该模板，命中则继续往下执行。

为什么用 raw screencap：设备端 PNG 编码要 3.3 秒左右（实测 ~4s/次），
raw 格式只要 ~640ms，PIL/cv2 解析仅几毫秒，轮询等待才有实际意义。

匹配用 cv2.matchTemplate + TM_CCOEFF_NORMED：
  - 与亮度/整体色偏无关（归一化相关系数），实测亮度 +25 仍 0.9999
  - 实测存在 1.0000 / 不存在 0.47 / 缩放 0.95 倍 0.9124，
    所以默认阈值 0.85 能区分且容忍小幅渲染差异。
"""
import os
import struct
import subprocess
import threading
import time

import numpy as np
import cv2

from config import logger

# 截图与匹配的超时（秒）
CAPTURE_TIMEOUT = 60

# 投屏视频帧缓存：device_id -> (写入时间戳, BGR 帧)
# 由 routes/remote_ws.py 的帧泵在转发 H.264 帧时解码写入；没有预览会话时
# 为空，capture_screen 自动回退 adb raw screencap。
FRAME_MAX_AGE = 0.5
_latest_frames: dict = {}
_frames_lock = threading.Lock()


def _put_frame(device_id: str, frame):
    with _frames_lock:
        _latest_frames[device_id] = (time.time(), frame)


def get_video_frame(device_id: str):
    """取最新解码帧；超过 FRAME_MAX_AGE 视为过期（返回 None 走 adb 兜底）"""
    with _frames_lock:
        item = _latest_frames.get(device_id)
    if item is None:
        return None
    ts, frame = item
    if time.time() - ts > FRAME_MAX_AGE:
        return None
    return frame


def video_frame_size(device_id: str):
    """最新投屏视频帧尺寸；无新鲜帧返回 None（走 wm size / adb 兜底）"""
    frame = get_video_frame(device_id)
    if frame is None:
        return None
    return frame.shape[1], frame.shape[0]


def _fit_screen(frame, target):
    """把画面统一到等待开始时的尺寸，避免视频帧/adb 兜底切换后模板尺寸失配"""
    if target and (frame.shape[1], frame.shape[0]) != target:
        return cv2.resize(frame, target, interpolation=cv2.INTER_AREA)
    return frame


class H264Decoder:
    """把投屏 H.264 Annex B 帧流解成最新 BGR 帧（每个帧泵一个实例，单线程使用）

    delta 帧必须连续喂：解码器实例要跟着帧泵活，不能每帧新建。config 帧
    （SPS/PPS）也要喂，否则解码器不认识后续 slice。av 缺失或解码失败时
    只记日志，capture_screen 会自动回退 adb，不影响主流程。
    """

    def __init__(self, device_id: str):
        self.device_id = device_id
        self._av = None
        self._ctx = None
        self._disabled = False

    def feed(self, data: bytes):
        if self._disabled:
            return
        if self._av is None:
            try:
                import av
            except ImportError:
                self._disabled = True
                logger.warning(f"[{self.device_id}] av 未安装，视频帧快路径禁用（回退 adb 截屏）")
                return
            self._av = av
        if self._ctx is None:
            try:
                self._ctx = self._av.CodecContext.create("h264", "r")
            except Exception as e:
                self._disabled = True
                logger.warning(f"[{self.device_id}] H.264 解码器创建失败（回退 adb 截屏）: {e}")
                return
        try:
            for packet in self._ctx.parse(data):
                for img in self._ctx.decode(packet):
                    _put_frame(self.device_id, img.to_ndarray(format="bgr24"))
        except Exception as e:
            logger.warning(f"[{self.device_id}] 视频帧解码失败（回退 adb 截屏）: {e}")
            self._ctx = None  # 重建，等下一个带 SPS/PPS 的关键帧


def _adb_path() -> str:
    """取内置 adb 完整路径（打包后同样可用）"""
    from utils.tool_manager import get_adb_path
    return get_adb_path()


def capture_screen(device_id: str, timeout: int = CAPTURE_TIMEOUT, target=None):
    """
    截取设备当前画面，返回 BGR 格式的 numpy 数组（cv2 原生顺序）

    优先用投屏视频流刚解码的最新帧（有预览会话时几乎零耗时），否则回退
    adb raw screencap。target 为等待开始时的画面尺寸，两种数据源都统一到它，
    保证模板换算一次后始终对得上。

    raw screencap 头部 16 字节：width, height, format, colorspace（小端 uint32），
    之后是 width*height*4 字节的 RGBA 像素。
    """
    frame = get_video_frame(device_id)
    if frame is not None:
        return _fit_screen(frame, target)

    cmd = [_adb_path(), "-s", device_id, "exec-out", "screencap"]
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"screencap 失败: {(result.stderr or b'').decode('utf-8', 'replace')[:200]}")

    data = result.stdout
    if len(data) < 16:
        raise RuntimeError("screencap 返回数据过短")

    width, height, _fmt, _cs = struct.unpack("<IIII", data[:16])
    expect = width * height * 4
    body = data[16:]
    if len(body) != expect:
        raise RuntimeError(f"截图像素尺寸不符: 得到 {len(body)} 字节，期望 {expect}（{width}x{height}）")

    arr = np.frombuffer(body, dtype=np.uint8).reshape(height, width, 4)
    return _fit_screen(cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR), target)


def load_template(path: str):
    """读取模板图（中文路径安全：cv2.imread 对非 ASCII 路径会失败，用 imdecode）"""
    if not os.path.exists(path):
        raise RuntimeError(f"模板图不存在: {path}")
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"模板图无法解码: {path}")
    return img


def find_template(screen, template, threshold: float = 0.85):
    """
    在 screen 中搜索 template，返回 (命中?, 最高分, 位置)

    调用方自行处理尺寸/缩放差异：模板与画面尺寸不一致时由调用方
    按录制分辨率换算，这里只做纯匹配。
    """
    if template is None or screen is None:
        return False, 0.0, None
    th, tw = template.shape[:2]
    sh, sw = screen.shape[:2]
    if th > sh or tw > sw:
        # 模板比画面还大 → 不可能命中（通常是基准分辨率不匹配）
        return False, 0.0, None

    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _minv, maxv, _minl, maxl = cv2.minMaxLoc(result)
    return maxv >= threshold, float(maxv), maxl


def wait_for_template(device_id: str, template, threshold: float = 0.85,
                      timeout: float = 15.0, interval: float = 0.3,
                      stop_event=None, target_size=None):
    """
    反复截屏直到模板出现或超时

    interval：截屏匹配之间的等待秒数。每轮实际耗时 ≈ 截屏(~0.65s) + interval，
    0.3s 时一轮约 1 秒，短时闪现的组件（>1s）基本都能抓到。

    target_size：等待开始时的画面尺寸（可选），视频帧与 adb 兜底都统一到它。
    stop_event（可选）：外部停止标志，置位后立刻返回未命中。
    返回 (命中?, 耗时秒, 最高分, 尝试次数)。超时不抛异常，
    由调用方决定是判失败还是继续（YAGNI：只用当前已知的两条路径）。
    """
    target = target_size if target_size and target_size[0] and target_size[1] else None
    start = time.time()
    best_score = 0.0
    attempts = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            return False, time.time() - start, best_score, attempts
        attempts += 1
        try:
            screen = capture_screen(device_id, target=target)
            found, score, loc = find_template(screen, template, threshold)
            if score > best_score:
                best_score = score
            if found:
                elapsed = time.time() - start
                logger.info(
                    f"[{device_id}] 模板命中: score={score:.4f} 位置={loc} "
                    f"第 {attempts} 次尝试，耗时 {elapsed:.2f}s"
                )
                return True, elapsed, score, attempts
        except Exception as e:
            logger.warning(f"[{device_id}] 截屏/匹配异常（继续重试）: {e}")

        if time.time() - start >= timeout:
            elapsed = time.time() - start
            logger.warning(
                f"[{device_id}] 模板未出现，超时 {elapsed:.2f}s，"
                f"最高分 {best_score:.4f}（阈值 {threshold}），共 {attempts} 次"
            )
            return False, elapsed, best_score, attempts

        # 等待轮询间隔，同样可被停止请求唤醒
        if stop_event is not None:
            if stop_event.wait(interval):
                return False, time.time() - start, best_score, attempts
        else:
            time.sleep(interval)
