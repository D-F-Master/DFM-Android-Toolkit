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
import time

import numpy as np
import cv2

from config import logger

# 截图与匹配的超时（秒）
CAPTURE_TIMEOUT = 60


def _adb_path() -> str:
    """取内置 adb 完整路径（打包后同样可用）"""
    from utils.tool_manager import get_adb_path
    return get_adb_path()


def capture_screen(device_id: str, timeout: int = CAPTURE_TIMEOUT):
    """
    截取设备当前画面，返回 BGR 格式的 numpy 数组（cv2 原生顺序）

    raw screencap 头部 16 字节：width, height, format, colorspace（小端 uint32），
    之后是 width*height*4 字节的 RGBA 像素。
    """
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
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)


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
                      timeout: float = 15.0, interval: float = 0.8):
    """
    反复截屏直到模板出现或超时

    返回 (命中?, 耗时秒, 最高分, 尝试次数)。超时不抛异常，
    由调用方决定是判失败还是继续（YAGNI：只用当前已知的两条路径）。
    """
    start = time.time()
    best_score = 0.0
    attempts = 0
    while True:
        attempts += 1
        try:
            screen = capture_screen(device_id)
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

        time.sleep(interval)
