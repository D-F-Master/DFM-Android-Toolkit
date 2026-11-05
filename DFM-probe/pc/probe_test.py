"""DFM Probe 通讯联调脚本（PC 端）。

流程：
  1. 确认设备在线
  2. adb install -r DFMProbe.apk
  3. 本机开 TCP 监听
  4. 通过 adb broadcast 把「本机地址」发给设备上的 APK
  5. 等 APK 主动回连，校验 hello / ping-pong / echo / tts / bye

用法：
  python pc/probe_test.py
  python pc/probe_test.py --device 192.168.0.59:5555 --host 192.168.2.93 --port 19876
  python pc/probe_test.py --reverse          # 走 adb reverse，绕开 Windows 防火墙
  python pc/probe_test.py --tts-text 你好世界  # 自定义朗读文本
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

PACKAGE = "com.dfm.probe"
RECEIVER = PACKAGE + "/.ConnectReceiver"
ACTION = "com.dfm.probe.CONNECT"


def adb(device, *args, timeout=60):
    cmd = ["adb", "-s", device] + [str(a) for a in args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def local_ip_for(device):
    """取本机到设备所用网卡的 IP（UDP connect 不真正发包）。"""
    host = device.split(":")[0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 5555))
        return s.getsockname()[0]
    finally:
        s.close()


def read_line(conn, timeout):
    conn.settimeout(timeout)
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(4096)
        if not chunk:
            raise RuntimeError("设备端连接已关闭（未收到完整一行）")
        buf += chunk
    return json.loads(buf.decode("utf-8").strip())


def send_json(conn, obj):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def expect(msg, key, value):
    if msg.get(key) != value:
        raise RuntimeError("期望 {} = {!r}，实际收到 {!r}".format(key, value, msg))


def main():
    parser = argparse.ArgumentParser(description="DFM Probe 通讯测试")
    parser.add_argument("--device", default="192.168.0.59:5555")
    parser.add_argument(
        "--apk",
        default=str(Path(__file__).resolve().parents[1] / "DFMProbe.apk"),
    )
    parser.add_argument("--host", default=None, help="发给设备的本机地址，默认自动探测")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0=系统分配")
    parser.add_argument("--timeout", type=float, default=20.0, help="等待回连超时（秒）")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--reverse", action="store_true", help="用 adb reverse 代替 LAN 直连")
    parser.add_argument("--tts-text", default="设备语音测试", help="TTS 朗读文本")
    args = parser.parse_args()

    listener = None
    conn = None
    reverse_port = None
    ok = False
    try:
        print("[1/6] 检查设备 {} ...".format(args.device))
        rc, out, err = adb(args.device, "get-state")
        if rc != 0 or out != "device":
            raise RuntimeError("设备不在线: rc={} out={!r} err={!r}".format(rc, out, err))
        print("      OK: device")

        if not args.skip_install:
            apk = Path(args.apk)
            if not apk.is_file():
                raise RuntimeError("找不到 APK: {}".format(apk))
            print("[2/6] 安装 {} ...".format(apk))
            rc, out, err = adb(args.device, "install", "-r", str(apk), timeout=180)
            print("      {}".format((out + " " + err).strip()))
            if rc != 0 or "Success" not in out:
                raise RuntimeError("adb install 失败: rc={}".format(rc))
        else:
            print("[2/6] 跳过安装")

        # 清掉上一次的会话，保证本次测试从零开始
        adb(args.device, "shell", "am", "force-stop", PACKAGE)

        bind_host = "127.0.0.1" if args.reverse else "0.0.0.0"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind_host, args.port))
        listener.listen(1)
        port = listener.getsockname()[1]
        print("[3/6] 本机监听 {}:{}".format(bind_host, port))

        if args.reverse:
            rc, out, err = adb(
                args.device, "reverse", "tcp:{}".format(port), "tcp:{}".format(port)
            )
            if rc != 0:
                raise RuntimeError("adb reverse 失败: {} {}".format(out, err))
            reverse_port = port
            host = "127.0.0.1"   # 设备侧 loopback，由 adbd 反向转发到 PC
        else:
            host = args.host or local_ip_for(args.device)

        print("[4/6] 发送本机地址 {}:{} 给设备".format(host, port))
        rc, out, err = adb(
            args.device, "shell", "am", "broadcast",
            "--include-stopped-packages", "-n", RECEIVER, "-a", ACTION,
            "--es", "host", host, "--ei", "port", str(port),
        )
        print("      {}".format((out + " " + err).strip()))
        if rc != 0:
            raise RuntimeError("am broadcast 失败: rc={}".format(rc))

        print("[5/6] 等待设备回连（{} 秒）...".format(args.timeout))
        listener.settimeout(args.timeout)
        conn, addr = listener.accept()
        print("      OK: 设备已连入 {}".format(addr))

        hello = read_line(conn, args.timeout)
        expect(hello, "type", "hello")
        print("      hello: {}".format(json.dumps(hello, ensure_ascii=False)))

        ping_payload = "ping-{}".format(int(time.time()))
        send_json(conn, {"type": "ping", "payload": ping_payload})
        pong = read_line(conn, args.timeout)
        expect(pong, "type", "pong")
        expect(pong, "payload", ping_payload)
        print("      ping -> pong OK (payload={})".format(ping_payload))

        echo_payload = "你好 DFM-Probe"
        send_json(conn, {"type": "echo", "payload": echo_payload})
        echo = read_line(conn, args.timeout)
        expect(echo, "type", "echo")
        expect(echo, "payload", echo_payload)
        print("      echo OK (payload={})".format(echo_payload))

        tts_payload = args.tts_text
        send_json(conn, {"type": "tts", "text": tts_payload})
        tts = read_line(conn, args.timeout + 60)   # TTS 引擎首次初始化可能较慢
        expect(tts, "type", "tts")
        if tts.get("status") != "done":
            raise RuntimeError("tts 失败: {}".format(tts))
        print("      tts OK ({} -> {})".format(tts_payload, tts.get("status")))

        send_json(conn, {"type": "quit"})
        bye = read_line(conn, args.timeout)
        expect(bye, "type", "bye")
        print("      quit -> bye OK")

        print("[6/6] PASS：通讯链路建立且双向收发正常（ping/echo/tts）")
        ok = True
    except Exception as e:
        print("FAIL: {}".format(e))
    finally:
        if conn is not None:
            conn.close()
        if listener is not None:
            listener.close()
        if reverse_port is not None:
            adb(args.device, "reverse", "--remove", "tcp:{}".format(reverse_port))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
