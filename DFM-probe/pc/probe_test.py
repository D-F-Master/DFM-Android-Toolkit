"""DFM Probe 帧协议联调脚本（PC 端）。

流程：
  1. 确认设备在线
  2. adb install -r DFMProbe.apk（--skip-install 可跳过）
  3. am broadcast 启动 APK 服务（adb 不再传地址）
  4. USB 设备：adb forward 随机本地端口 -> 设备 21104；网络设备：直连 <IP>:21104
  5. PC 主动 connect，按二进制帧协议测试 心跳 / 设备详细信息 / TTS / 参数错误 / 未知命令 / CRC 错帧

帧格式：AA 55 | 帧序(1B) | 长度(2B，低字节在前) | 命令字(1B) | 数据区 | CRC16(低字节在前)
详见 probe与service通讯协议.md

用法：
  python pc/probe_test.py --device 202600298120
  python pc/probe_test.py --device 192.168.0.59:5555 --tts-text 你好世界
  python pc/probe_test.py --device 202600298120 --skip-install
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

PACKAGE = "com.dfm.probe"
RECEIVER = PACKAGE + "/.StartReceiver"
ACTION = "com.dfm.probe.START"
DEVICE_PORT = 21104
FRAME_HEADER = b"\xaa\x55"

CMD_HEARTBEAT = 0x00
CMD_TTS = 0x01
CMD_DEVICE_INFO = 0x02
CMD_ACK = 0x06
CMD_ERROR = 0xFF

ACK_TIMEOUT = 0.2
MAX_RETRY = 3


def adb(device, *args, timeout=60):
    cmd = ["adb", "-s", device] + [str(a) for a in args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def crc16_modbus(data):
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


def pack_frame(seq, cmd, data):
    """组一帧；data 为字节串，长度 2 字节低字节在前，数据区最长 65535 字节。"""
    if len(data) > 65535:
        raise ValueError("数据区超过 65535 字节: {}".format(len(data)))
    body = bytes([seq & 0xFF, len(data) & 0xFF, (len(data) >> 8) & 0xFF, cmd & 0xFF]) + data
    crc = crc16_modbus(body)
    return FRAME_HEADER + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


class FrameReader:
    """增量解帧：处理粘包/半包，CRC 错帧丢弃后重新找帧头。"""

    def __init__(self, conn):
        self.conn = conn
        self.buf = bytearray()

    def read_frame(self, timeout):
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

    def _try_parse(self):
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
                # CRC 错：丢掉第一个字节，从后续字节重新找帧头
                del self.buf[:1]
                continue
            del self.buf[:frame_len]
            return seq, cmd, frame[6:6 + length]


def call(reader, conn, seq, cmd, obj, resp_timeout):
    """发一帧业务帧，停等 ACK（T=200ms，最多重发 3 次），再取同帧序响应帧。"""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    frame = pack_frame(seq, cmd, data)
    pending = None
    got_ack = False
    for _ in range(MAX_RETRY + 1):
        conn.sendall(frame)
        deadline = time.monotonic() + ACK_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                r_seq, r_cmd, r_data = reader.read_frame(remaining)
            except TimeoutError:
                break
            if r_seq != seq:
                continue
            if r_cmd == CMD_ACK:
                got_ack = True
                break
            if r_cmd in (cmd | 0x80, CMD_ERROR):
                pending = (r_cmd, r_data)
        if got_ack or pending is not None:
            break
    if not got_ack and pending is None:
        raise RuntimeError("ACK 等待超时（共发 {} 次）".format(MAX_RETRY + 1))
    if pending is None:
        deadline = time.monotonic() + resp_timeout
        while True:
            r_seq, r_cmd, r_data = reader.read_frame(deadline - time.monotonic())
            if r_seq == seq and r_cmd in (cmd | 0x80, CMD_ERROR):
                pending = (r_cmd, r_data)
                break
    return json.loads(pending[1].decode("utf-8")), frame


def expect(msg, key, value):
    if msg.get(key) != value:
        raise RuntimeError("期望 {} = {!r}，实际收到 {!r}".format(key, value, msg))


def connect_with_retry(host, port, attempts=10, interval=0.3):
    """START 广播是异步的，服务可能还没 bind 好，需要短暂重试。"""
    last = None
    for _ in range(attempts):
        try:
            return socket.create_connection((host, port), timeout=5)
        except OSError as e:
            last = e
            time.sleep(interval)
    raise RuntimeError("连接 {}:{} 失败: {}".format(host, port, last))


def main():
    parser = argparse.ArgumentParser(description="DFM Probe 帧协议通讯测试")
    parser.add_argument("--device", default="192.168.0.59:5555")
    parser.add_argument(
        "--apk",
        default=str(Path(__file__).resolve().parents[1] / "DFMProbe.apk"),
    )
    parser.add_argument(
        "--timeout", type=float, default=130.0,
        help="响应帧等待超时（秒），需覆盖 TTS 引擎初始化 30s + 朗读 60s",
    )
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--tts-text", default="设备语音测试", help="TTS 朗读文本")
    args = parser.parse_args()

    conn = None
    forward_port = None
    ok = False
    seq = 0
    try:
        print("[1/7] 检查设备 {} ...".format(args.device))
        rc, out, err = adb(args.device, "get-state")
        if rc != 0 or out != "device":
            raise RuntimeError("设备不在线: rc={} out={!r} err={!r}".format(rc, out, err))
        print("      OK: device")

        if not args.skip_install:
            apk = Path(args.apk)
            if not apk.is_file():
                raise RuntimeError("找不到 APK: {}".format(apk))
            print("[2/7] 安装 {} ...".format(apk))
            rc, out, err = adb(args.device, "install", "-r", str(apk), timeout=180)
            print("      {}".format((out + " " + err).strip()))
            if rc != 0 or "Success" not in out:
                raise RuntimeError("adb install 失败: rc={}".format(rc))
        else:
            print("[2/7] 跳过安装")

        print("[3/7] 启动服务并建立连接 ...")
        rc, out, err = adb(
            args.device, "shell", "am", "broadcast",
            "--include-stopped-packages", "-n", RECEIVER, "-a", ACTION,
        )
        print("      {}".format((out + " " + err).strip()))
        if rc != 0:
            raise RuntimeError("am broadcast 失败: rc={}".format(rc))

        if ":" in args.device:
            # 网络设备：APK 启动后直接连它的 21104
            host = args.device.split(":")[0]
            port = DEVICE_PORT
        else:
            # USB 设备：用系统分配的随机本地端口做 forward，避免多设备抢端口
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            finally:
                probe.close()
            rc, out, err = adb(
                args.device, "forward", "tcp:{}".format(port), "tcp:{}".format(DEVICE_PORT)
            )
            if rc != 0:
                raise RuntimeError("adb forward 失败: {} {}".format(out, err))
            forward_port = port
            host = "127.0.0.1"
        conn = connect_with_retry(host, port)
        print("      OK: 已连入 {}:{}".format(host, port))
        reader = FrameReader(conn)

        # 组帧边界：数据区超过 65535 字节必须被拒绝，65535 字节可正常组帧
        try:
            pack_frame(seq, CMD_HEARTBEAT, b"x" * 65536)
            raise RuntimeError("组帧应拒绝超过 65535 字节的数据区")
        except ValueError:
            print("      超 65535 字节数据区被拒绝 OK")
        if len(pack_frame(seq, CMD_HEARTBEAT, b"x" * 65535)) != 8 + 65535:
            raise RuntimeError("65535 字节数据区组帧异常")

        print("[4/7] 心跳 CMD_HEARTBEAT (0x00) ...")
        hb, _ = call(reader, conn, seq, CMD_HEARTBEAT, {}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(hb, "code", 200)
        expect(hb, "message", "success")
        version = hb.get("data", {}).get("version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("心跳应答缺少 version: {}".format(hb))
        print("      heartbeat OK (version={})".format(version))

        print("[5/7] 设备详细信息 CMD_DEVICE_INFO (0x02) ...")
        detail, _ = call(reader, conn, seq, CMD_DEVICE_INFO, {}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(detail, "code", 200)
        expect(detail, "message", "success")
        for key in ("androidVersion", "sdk", "model", "manufacturer", "device", "displayVersion",
                    "sn", "buildDate", "kernelVersion", "serialPorts", "cameras", "peripherals"):
            if key not in detail.get("data", {}):
                raise RuntimeError("设备详细信息缺少 {}: {}".format(key, detail))
        print("      device info OK (serialPorts={}, cameras={}, peripherals={})".format(
            len(detail["data"]["serialPorts"]), len(detail["data"]["cameras"]),
            len(detail["data"]["peripherals"])))

        print("[6/7] TTS CMD_TTS (0x01) / 参数错误 / 未知命令 / CRC 错帧 ...")
        tts, _ = call(reader, conn, seq, CMD_TTS, {"text": args.tts_text}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(tts, "code", 200)
        expect(tts.get("data", {}), "status", "done")
        print("      tts OK ({} -> done)".format(args.tts_text))

        missing, _ = call(reader, conn, seq, CMD_TTS, {}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(missing, "code", 400)
        expect(missing, "data", None)
        expect(missing, "message", "text must be a string")
        print("      缺少 text -> 400 OK")

        wrong_type, _ = call(reader, conn, seq, CMD_TTS, {"text": 123}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(wrong_type, "code", 400)
        expect(wrong_type, "data", None)
        print("      text 非字符串 -> 400 OK")

        empty, _ = call(reader, conn, seq, CMD_TTS, {"text": "   "}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(empty, "code", 400)
        expect(empty, "data", None)
        expect(empty, "message", "empty text")
        print("      空文本 -> 400 OK (empty text)")

        bogus, _ = call(reader, conn, seq, 0x7E, {}, args.timeout)
        seq = (seq + 1) & 0xFF
        expect(bogus, "code", 400)
        expect(bogus, "data", None)
        if "unknown cmd" not in bogus.get("message", ""):
            raise RuntimeError("未知命令应答异常: {}".format(bogus))
        print("      未知命令 -> 0xFF/400 OK ({})".format(bogus.get("message")))

        # CRC 错帧：协议要求直接丢弃、不回 ACK、不回响应
        bad = bytearray(pack_frame(seq, CMD_HEARTBEAT, b"{}"))
        bad[-1] ^= 0xFF
        conn.sendall(bytes(bad))
        try:
            reader.read_frame(0.6)
            raise RuntimeError("CRC 错帧不应得到任何应答")
        except TimeoutError:
            print("      CRC 错帧被丢弃（无 ACK/响应）OK")
        seq = (seq + 1) & 0xFF

        print("[7/7] PASS：帧协议链路建立且心跳 / 设备详细信息 / TTS 收发正常")
        ok = True
    except Exception as e:
        print("FAIL: {}".format(e))
    finally:
        if conn is not None:
            conn.close()
        if forward_port is not None:
            adb(args.device, "forward", "--remove", "tcp:{}".format(forward_port))
        # 服务常驻监听，不再 force-stop
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
