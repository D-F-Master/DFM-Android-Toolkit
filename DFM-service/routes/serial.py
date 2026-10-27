"""
模拟串口 - 串口枚举与写入（方案 A：UART 内部回环注入）

依赖设备端的 arm64 工具 serial-writer（源码见 D:\\pycode\\apk模拟串口）：
  serial-writer [选项]
    -d, --device PATH      串口设备
    -b, --baud N           波特率；不给则完全继承当前串口设置
    -t, --text STR         文本载荷
    -x, --hex STR          HEX 载荷
    -e, --end KIND         结束符 none|cr|lf|crlf
    -n, --times N          发送次数
    -i, --interval MS      发送间隔
        --loop             开启 UART 内部回环，让读取该串口的程序收到本机写入
        --unloop           只清除回环位后退出
        --dry-run          只解析载荷并打印
    -q, --quiet            只打印 TX/RX 结果行

载荷传递：adb shell 走的是 PTY，stdin 会被换行转码（实测 0x0D 变 0x0A、0x00 被截断），
二进制载荷无法保真。因此采用「base64 编码 → 设备端 base64 -d → od -tx1 → 逗号分隔 hex
→ 命令替换 $(...) 展开为单个 -x 参数」的链路，实测字节完全一致。

提权：设备 adb 身份为 root 时直接执行；否则命令前加 su 0。

────────────────────────────────────────────────────────────────────────
方案 B：键盘模拟（pty 替换 + 按键注入）

用于外接实体键盘的串口（TX/RX 有硬件收发器隔离，内部回环无效），
把设备节点 bind mount 成 pty，App 重开串口时拿到的就是伪终端。

  pty-inject --dev PATH [--inject HEX | --inject-list F1,F2,...]
             [--interval MS] [--count N]
  keyinject --dev PATH <按键>... | --keys a,b,c | --map FILE | --seq 1-10
  keyinject --list

按键协议（com.yqsh.consume）：55 | LEN(03) | 05 | KEY | CRC，CRC=(0x05+KEY)&0xFF

注意：pty 替换对已打开的 fd 无效，App 必须重启才会拿到 pty。
"""
import base64
import re
import subprocess
import time
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from config import logger
from utils.tool_manager import get_adb_path, get_lib_path

router = APIRouter(
    prefix="/api",
    tags=["模拟串口"]
)

# 设备端工具存放路径
DEVICE_BIN_DIR = "/data/local/tmp"
DEVICE_WRITER = f"{DEVICE_BIN_DIR}/serial-writer"
DEVICE_PTY_INJECT = f"{DEVICE_BIN_DIR}/pty-inject"
DEVICE_KEYINJECT = f"{DEVICE_BIN_DIR}/keyinject"
DEVICE_KEYMAP = f"{DEVICE_BIN_DIR}/keymap.txt"
# 本地 arm64 工具目录（lib/serial/）
LOCAL_TOOL_DIRNAME = "serial"
LOCAL_WRITER_NAME = "serial-writer"
LOCAL_PTY_INJECT_NAME = "pty-inject"
LOCAL_KEYINJECT_NAME = "keyinject"
LOCAL_KEYMAP_NAME = "keymap-4x4.txt"
# 目标 App（pty 替换后需重启它才会重新打开串口）
DEFAULT_TARGET_APP = "com.yqsh.consume"
DEFAULT_APP_ACTIVITY = "com.yqsh.consume/.activity.SplashActivity"
# adb 命令执行超时（秒）
CMD_TIMEOUT = 20


def _stream_log_path(device: str) -> str:
    """按串口名生成独立日志路径（与 serial-stream.sh 约定一致）"""
    name = device.strip("/").split("/")[-1] or "default"
    return f"{DEVICE_BIN_DIR}/serial-stream-{name}.log"


class SerialPortsRequest(BaseModel):
    deviceId: str


class SerialSendRequest(BaseModel):
    deviceId: str
    device: str = "/dev/ttyS3"
    mode: str = "text"          # text | hex
    payload: str = ""
    end: str = "none"           # none | cr | lf | crlf
    baud: Optional[int] = None  # 不传则继承设备端已有设置
    loop: bool = True           # 开启 UART 内部回环
    times: int = 1
    interval: int = 0


class SerialStreamRequest(BaseModel):
    deviceId: str
    device: str = "/dev/ttyS3"
    baud: int = 115200
    loop: bool = True
    action: str = "status"      # start | stop | status


class SerialPtyRequest(BaseModel):
    """方案 B：pty 替换的启停与状态"""
    deviceId: str
    device: str = "/dev/ttyS7"
    action: str = "status"      # start | stop | status
    restartApp: bool = True     # 启动后是否重启目标 App（pty 替换必须重启才生效）
    packageName: str = DEFAULT_TARGET_APP
    activity: str = DEFAULT_APP_ACTIVITY


class SerialKeyRequest(BaseModel):
    """方案 B：按键注入（重启 pty-inject 循环注入指定帧）"""
    deviceId: str
    device: str = "/dev/ttyS7"
    keys: str = ""              # 逗号分隔的按键名/键值，如 1,menu,enter
    mapFile: str = ""           # 布局文件（设备端路径），与 keys 二选一
    seq: str = ""               # 键值范围，如 1-10
    baud: int = 38400
    delay: int = 300
    repeat: int = 1
    interval: int = 1000        # 帧注入间隔（毫秒）


def _get_local_writer_path() -> str:
    """获取本地 serial-writer（arm64）的完整路径"""
    lib_path = get_lib_path()
    candidates = [
        lib_path / LOCAL_TOOL_DIRNAME / LOCAL_WRITER_NAME,
        lib_path / LOCAL_TOOL_DIRNAME / (LOCAL_WRITER_NAME + ".exe"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError(
        f"未找到 {LOCAL_WRITER_NAME}！\n"
        f"请确认 lib/{LOCAL_TOOL_DIRNAME}/{LOCAL_WRITER_NAME} 存在"
    )


def _run_adb(device_id: str, args: List[str], timeout: int = CMD_TIMEOUT) -> dict:
    """
    执行 adb 命令（不经 shell，避免二次转义）

    Args:
        device_id: 设备序列号
        args: adb 参数列表，如 ['shell', 'ls', '-l', '/dev/ttyS3']
        timeout: 超时秒数
    """
    adb_path = get_adb_path()
    cmd = [adb_path, "-s", device_id] + args
    logger.info(f"ADB exec: {cmd}")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return {
            "success": result.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        logger.error(f"ADB timeout: {cmd}")
        return {"success": False, "stdout": "", "stderr": "命令执行超时", "exitCode": -1}
    except Exception as e:
        logger.error(f"ADB error: {e}")
        return {"success": False, "stdout": "", "stderr": str(e), "exitCode": -1}


def _device_is_root(device_id: str) -> bool:
    """判断设备上 adb 是否已是 root 身份"""
    result = _run_adb(device_id, ["shell", "id", "-u"], timeout=10)
    return result["success"] and result["stdout"].strip() == "0"


def _root_prefix(device_id: str) -> str:
    """返回提权前缀：root 设备直接返回空串，否则用 su 0"""
    return "" if _device_is_root(device_id) else "su 0 "


def _list_serial_ports(device_id: str) -> List[dict]:
    """
    枚举设备上的串口节点（/dev/ttyS*、/dev/ttyUSB*、/dev/ttyACM*）

    兼容两种 ls 输出：
      字符设备  crw-rw-rw- 1 root root 4, 67 /dev/ttyS3
      软链接    lrwxrwxrwx 1 root root 11 ... /dev/ttyS1 -> /dev/ttyDC1
    """
    result = _run_adb(device_id, [
        "shell",
        "ls", "-l", "/dev/ttyS*", "/dev/ttyUSB*", "/dev/ttyACM*",
    ], timeout=15)
    if not result["stdout"].strip():
        return []

    ports = []
    seen = set()

    def add(path: str, major_minor: str):
        if not path.startswith("/dev/") or path in seen:
            return
        seen.add(path)
        ports.append({"device": path, "majorMinor": major_minor})

    for raw_line in result["stdout"].splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if "->" in line:
            # 软链接：箭头左侧是路径，右侧是目标
            left = line.split("->", 1)[0].strip().split()
            target = line.split("->", 1)[1].strip()
            if left:
                add(left[-1], "")
            if target:
                add(target, "")
            continue

        parts = line.split()
        # crw-rw-rw- 1 owner group 4, 65 2026-10-27 13:49 /dev/ttyS3
        # major/minor 可能被空格分开（"4," + "65"），需要拼接
        if len(parts) >= 5 and parts[0].startswith("c"):
            major_minor = ""
            for idx, part in enumerate(parts):
                if part.endswith(","):
                    tail = parts[idx + 1] if idx + 1 < len(parts) else ""
                    if tail.isdigit():
                        major_minor = f"{part}{tail}"
                    break
            add(parts[-1], major_minor)

    return ports


def _tool_exists(device_id: str, path: str = DEVICE_WRITER) -> bool:
    """检查设备端是否已部署指定工具"""
    result = _run_adb(device_id, ["shell", "ls", "-l", path], timeout=10)
    return result["success"] and result["stdout"].strip() != ""


def _deploy_tool(device_id: str) -> dict:
    """把本地 serial-writer 推送到设备并赋予可执行权限"""
    try:
        local_path = _get_local_writer_path()
    except FileNotFoundError as e:
        return {"success": False, "message": str(e)}

    result = _run_adb(
        device_id,
        ["push", local_path, DEVICE_WRITER],
        timeout=60,
    )
    if not result["success"]:
        return {"success": False, "message": f"推送失败: {result['stderr'] or result['stdout']}"}

    chmod = _run_adb(device_id, ["shell", "chmod", "755", DEVICE_WRITER], timeout=10)
    if not chmod["success"]:
        return {"success": False, "message": f"设置权限失败: {chmod['stderr']}"}

    logger.info(f"serial-writer deployed to {device_id}")
    return {"success": True, "message": "success"}


def _build_payload_bytes(request: SerialSendRequest) -> bytes:
    """
    把请求里的载荷转成实际字节

    text 模式：按 serial-writer 的文本转义规则处理（\\r \\n \\t \\\\ \\0 \\xHH）
    hex 模式：解析 "01 03 00 00" / "0x01,0x03" 形式
    """
    raw = request.payload or ""
    if request.mode == "hex":
        cleaned = re.sub(r"0[xX]", "", raw)
        cleaned = re.sub(r"[^0-9a-fA-F]", "", cleaned)
        if len(cleaned) % 2 != 0:
            raise ValueError("HEX 载荷必须是偶数个十六进制位")
        return bytes.fromhex(cleaned)

    # 文本模式：与 serial-writer parse_text 保持一致
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch != "\\":
            out.append(ord(ch) & 0xFF)
            i += 1
            continue
        i += 1
        if i >= len(raw):
            raise ValueError("文本载荷转义不完整")
        esc = raw[i]
        mapping = {"r": 0x0D, "n": 0x0A, "t": 0x09, "\\": 0x5C, "0": 0x00}
        if esc in mapping:
            out.append(mapping[esc])
            i += 1
        elif esc == "x":
            hex_part = raw[i + 1:i + 3]
            if len(hex_part) != 2 or not re.fullmatch(r"[0-9a-fA-F]{2}", hex_part):
                raise ValueError(f"非法转义 \\x{hex_part}")
            out.append(int(hex_part, 16))
            i += 3
        else:
            raise ValueError(f"不支持的转义 \\{esc}")
    return bytes(out)


def _end_bytes(end: str) -> bytes:
    """结束符对应的字节"""
    return {
        "none": b"",
        "cr": b"\r",
        "lf": b"\n",
        "crlf": b"\r\n",
    }.get(end, b"")


def _build_hex_expr(payload: bytes) -> str:
    """
    生成设备端把载荷还原成 hex 列表的命令替换表达式

    adb shell 是 PTY，stdin 会被换行转码，二进制载荷不能直接走 stdin。
    这里用 base64 编码后在设备端解码成 hex 列表，实测字节完全保真。
    """
    b64 = base64.b64encode(payload).decode("ascii")
    # od -An -tx1 输出空格分隔的十六进制，paste 拼成逗号分隔列表
    return (
        f'"$(echo {b64} | base64 -d '
        f'| od -An -tx1 | tr -s \' \' \'\\n\' '
        f'| grep -v \'^$\' | paste -sd, -)"'
    )


# ========== 接口 ==========

@router.post("/device/serial/ports")
def list_serial_ports(request: SerialPortsRequest):
    """列出设备上的串口节点，并报告工具是否已部署"""
    try:
        ports = _list_serial_ports(request.deviceId)
        deployed = _tool_exists(request.deviceId)
        return {
            "code": 200,
            "message": "success",
            "data": {
                "ports": ports,
                "toolDeployed": deployed,
                "toolPath": DEVICE_WRITER,
            },
        }
    except Exception as e:
        logger.error(f"获取串口列表失败: {e}")
        return {"code": 500, "message": f"获取串口列表失败: {str(e)}", "data": None}


@router.post("/device/serial/deploy")
def deploy_serial_tool(request: SerialPortsRequest):
    """把 serial-writer 推送到设备"""
    try:
        result = _deploy_tool(request.deviceId)
        if not result["success"]:
            return {"code": 500, "message": result["message"], "data": None}
        return {"code": 200, "message": "success", "data": {"toolPath": DEVICE_WRITER}}
    except Exception as e:
        logger.error(f"部署串口工具失败: {e}")
        return {"code": 500, "message": f"部署串口工具失败: {str(e)}", "data": None}


@router.post("/device/serial/send")
def send_serial_data(request: SerialSendRequest):
    """向指定串口写入一帧数据（默认开启 UART 内部回环）"""
    try:
        if not request.deviceId:
            return {"code": 400, "message": "deviceId 不能为空", "data": None}

        payload = _build_payload_bytes(request)
        payload += _end_bytes(request.end)
        if not payload:
            return {"code": 400, "message": "载荷不能为空", "data": None}

        # 载荷经 base64 链路传给设备，避免 PTY 转码破坏二进制内容
        writer_cmd = [
            DEVICE_WRITER,
            "-d", request.device,
            "-x", _build_hex_expr(payload),
            "-e", "none",
            "-n", str(max(1, request.times)),
            "-i", str(max(0, request.interval)),
        ]
        if request.baud:
            writer_cmd += ["-b", str(request.baud)]
        if request.loop:
            writer_cmd.append("--loop")

        prefix = _root_prefix(request.deviceId)
        shell_cmd = " ".join(writer_cmd)
        if prefix:
            shell_cmd = f"{prefix}{shell_cmd}"

        result = _run_adb(
            request.deviceId,
            ["shell", shell_cmd],
            timeout=max(CMD_TIMEOUT, 10 + request.times * (request.interval + 1)),
        )

        output = (result["stdout"] or "") + (result["stderr"] or "")
        return {
            "code": 200 if result["success"] else 500,
            "message": "success" if result["success"] else "串口写入失败",
            "data": {
                "output": output.strip(),
                "exitCode": result["exitCode"],
                "sentBytes": len(payload),
            },
        }
    except ValueError as e:
        return {"code": 400, "message": f"载荷格式错误: {str(e)}", "data": None}
    except Exception as e:
        logger.error(f"串口写入失败: {e}")
        return {"code": 500, "message": f"串口写入失败: {str(e)}", "data": None}


@router.post("/device/serial/stream")
def control_serial_stream(request: SerialStreamRequest):
    """持续发送的启停与状态查询"""
    try:
        if not request.deviceId:
            return {"code": 400, "message": "deviceId 不能为空", "data": None}

        prefix = _root_prefix(request.deviceId)
        action = request.action

        if action == "start":
            if not _tool_exists(request.deviceId):
                return {"code": 400, "message": "设备端未部署 serial-writer，请先部署", "data": None}

            # 若该端口已有实例在跑，先停掉，避免两个进程抢同一个串口
            _stop_stream(request.deviceId, request.device, prefix)
            time.sleep(0.3)

            cmd = [
                DEVICE_WRITER,
                "-d", request.device,
                "-b", str(request.baud),
                "-t", "SERIAL-WRITER-TEST",
                "-e", "crlf",
                "-n", "0",
                "-i", "300",
                "-q",
            ]
            if request.loop:
                cmd.append("--loop")
            shell_cmd = " ".join(cmd)
            if prefix:
                shell_cmd = f"{prefix}{shell_cmd}"

            # 后台常驻：重定向到按端口区分的日志，adb shell 退出后仍继续
            log_path = _stream_log_path(request.device)
            full_cmd = f"{shell_cmd} > {log_path} 2>&1 < /dev/null &"
            _run_adb(request.deviceId, ["shell", full_cmd], timeout=15)
            time.sleep(1.0)

            status = _stream_status(request.deviceId, request.device, prefix)
            if not status["running"]:
                return {
                    "code": 500,
                    "message": "持续发送启动失败",
                    "data": status,
                }
            return {
                "code": 200,
                "message": "持续发送已启动",
                "data": status,
            }

        if action == "stop":
            _stop_stream(request.deviceId, request.device, prefix)
            time.sleep(0.5)
            status = _stream_status(request.deviceId, request.device, prefix)
            return {
                "code": 200,
                "message": "持续发送已停止",
                "data": status,
            }

        # status
        status = _stream_status(request.deviceId, request.device, prefix)
        return {"code": 200, "message": "success", "data": status}

    except Exception as e:
        logger.error(f"持续发送控制失败: {e}")
        return {"code": 500, "message": f"持续发送控制失败: {str(e)}", "data": None}


def _find_stream_pids(device_id: str, device: str, prefix: str) -> List[str]:
    """
    查找指定串口上运行中的全部 serial-writer 进程 pid

    用 ps -A -o PID,ARGS 列出全部进程后在本地精确匹配端口，
    避免 pgrep -f 命中 adb shell 自身（其命令行里也含端口串）。
    """
    result = _run_adb(
        device_id,
        ["shell", f"{prefix}ps -A -o PID,ARGS"],
        timeout=10,
    )
    if not result["stdout"].strip():
        return []

    needle = f"-d {device}"
    pids = []
    for line in result["stdout"].splitlines():
        parts = line.strip().split()
        # ps -A -o PID,ARGS 输出形如 " 7617 serial-writer -d /dev/ttyS3 ..."
        if len(parts) < 2 or parts[1] != "serial-writer":
            continue
        if needle in line:
            pids.append(parts[0])
    return pids


def _find_stream_pid(device_id: str, device: str, prefix: str) -> str:
    """查找指定串口上运行中的 serial-writer 进程 pid（取第一个）"""
    pids = _find_stream_pids(device_id, device, prefix)
    return pids[0] if pids else ""


def _stop_stream(device_id: str, device: str, prefix: str) -> bool:
    """
    停止指定串口上的持续发送进程（SIGTERM，让工具自行恢复 termios）

    可能残留多个实例（历史异常退出），需全部终止。
    """
    pids = _find_stream_pids(device_id, device, prefix)
    if not pids:
        return False

    for pid in pids:
        _run_adb(device_id, ["shell", f"{prefix}kill -TERM {pid}"], timeout=10)

    # 等待进程真正退出
    for _ in range(20):
        time.sleep(0.2)
        if not _find_stream_pids(device_id, device, prefix):
            return True
    # 兜底：强制结束（termios 可能残留）
    for pid in _find_stream_pids(device_id, device, prefix):
        _run_adb(device_id, ["shell", f"{prefix}kill -9 {pid}"], timeout=10)
    return True


def _stream_status(device_id: str, device: str, prefix: str) -> dict:
    """查询持续发送进程状态与最近日志"""
    pid = _find_stream_pid(device_id, device, prefix)

    log_text = ""
    log_result = _run_adb(
        device_id,
        ["shell", f"tail -n 5 {_stream_log_path(device)}"],
        timeout=10,
    )
    if log_result["stdout"].strip():
        log_text = log_result["stdout"].strip()

    return {
        "running": bool(pid),
        "pid": pid,
        "device": device,
        "log": log_text,
    }


# ═══════════════════════════════════════════════════════════════════════
# 方案 B：pty 替换 + 按键注入
# ═══════════════════════════════════════════════════════════════════════

# 设备端 pty-inject 日志
PTY_LOG = f"{DEVICE_BIN_DIR}/pty-inject.log"
# 循环注入的帧间隔（毫秒）
PINJECT_INTERVAL_MS = 1500
# 默认注入帧（数字 1，键值 2）
DEFAULT_INJECT_FRAME = "5503050207"

# 可注入的按键名 -> App 内部键值（与 keyinject 的 KEYS 表一致）
KEY_VALUES = {
    "0": 1, "1": 2, "2": 3, "3": 4, "4": 5,
    "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "dot": 11, "add": 16, "enter": 17, "menu": 35, "sub": 49,
    "f1": 240, "f2": 241, "up": 242, "down": 243, "esc": 244,
}


def _get_local_tool(name: str) -> str:
    """获取 lib/serial/ 下指定工具的完整路径"""
    lib_path = get_lib_path()
    path = lib_path / LOCAL_TOOL_DIRNAME / name
    if path.exists():
        return str(path)
    raise FileNotFoundError(f"未找到 {name}！请确认 lib/{LOCAL_TOOL_DIRNAME}/{name} 存在")


def _push_file(device_id: str, local_path: str, remote_path: str, mode: str = "755") -> dict:
    """把本地文件推送到设备并设置权限"""
    result = _run_adb(device_id, ["push", local_path, remote_path], timeout=60)
    if not result["success"]:
        return {"success": False, "message": f"推送 {remote_path} 失败: {result['stderr'] or result['stdout']}"}
    chmod = _run_adb(device_id, ["shell", "chmod", mode, remote_path], timeout=10)
    if not chmod["success"]:
        return {"success": False, "message": f"设置 {remote_path} 权限失败: {chmod['stderr']}"}
    return {"success": True, "message": "success"}


def _deploy_pty_tools(device_id: str) -> dict:
    """推送 pty-inject / keyinject / keymap 到设备"""
    try:
        pty_local = _get_local_tool(LOCAL_PTY_INJECT_NAME)
        key_local = _get_local_tool(LOCAL_KEYINJECT_NAME)
    except FileNotFoundError as e:
        return {"success": False, "message": str(e)}

    for local_path, remote_path in (
        (pty_local, DEVICE_PTY_INJECT),
        (key_local, DEVICE_KEYINJECT),
    ):
        result = _push_file(device_id, local_path, remote_path)
        if not result["success"]:
            return result

    # 布局文件存在才推（可选）
    try:
        map_local = _get_local_tool(LOCAL_KEYMAP_NAME)
        _push_file(device_id, map_local, DEVICE_KEYMAP, mode="666")
    except FileNotFoundError:
        pass

    logger.info(f"pty tools deployed to {device_id}")
    return {"success": True, "message": "success"}


def _find_pty_pids(device_id: str, prefix: str) -> List[str]:
    """查找设备上运行中的 pty-inject 进程 pid"""
    result = _run_adb(device_id, ["shell", f"{prefix}ps -A -o PID,ARGS"], timeout=10)
    if not result["stdout"].strip():
        return []
    pids = []
    for line in result["stdout"].splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "pty-inject":
            pids.append(parts[0])
    return pids


def _pty_status(device_id: str, device: str, prefix: str) -> dict:
    """查询 pty 替换状态：进程、节点类型、App 是否持有"""
    pids = _find_pty_pids(device_id, prefix)

    # 节点类型：真实串口是 4,x；pty 是 136,x
    node_type = "unknown"
    major_minor = ""
    ls_result = _run_adb(device_id, ["shell", f"ls -l {device}"], timeout=10)
    ls_text = ls_result["stdout"].strip()
    # ls -l 输出形如 "crw-rw-rw- 1 root root 136,   0 2026-10-27 14:00 /dev/ttyS7"
    parts = ls_text.split()
    for idx, part in enumerate(parts):
        if part.endswith(",") and idx + 1 < len(parts) and parts[idx + 1].isdigit():
            major_minor = f"{part[:-1]},{parts[idx + 1]}"
            # pty 的主设备号是 136（Unix98 伪终端），真实串口通常是 4
            node_type = "pty" if part[:-1] == "136" else "serial"
            break

    # App 是否持有该串口
    app_holds = False
    lsof_result = _run_adb(
        device_id,
        ["shell", f"{prefix}lsof 2>/dev/null | grep {device}"],
        timeout=15,
    )
    if lsof_result["stdout"].strip():
        app_holds = True

    # 残留挂载（异常退出会留下 .ptybak_*）
    leftover = ""
    bak_result = _run_adb(
        device_id,
        ["shell", "ls /dev/.ptybak_* 2>&1"],
        timeout=10,
    )
    bak_text = bak_result["stdout"].strip()
    if bak_text and "No such file" not in bak_text:
        leftover = bak_text.splitlines()[0].strip()

    log_text = ""
    log_result = _run_adb(device_id, ["shell", f"tail -n 8 {PTY_LOG}"], timeout=10)
    if log_result["stdout"].strip():
        log_text = log_result["stdout"].strip()

    return {
        "running": bool(pids),
        "pid": pids[0] if pids else "",
        "device": device,
        "nodeType": node_type,
        "majorMinor": major_minor,
        "appHolds": app_holds,
        "leftoverMount": leftover,
        "log": log_text,
    }


def _find_sleep_pids(device_id: str, prefix: str) -> List[str]:
    """查找为撑住 pty-inject stdin 而启动的 sleep 进程 pid"""
    result = _run_adb(device_id, ["shell", f"{prefix}ps -A -o PID,ARGS"], timeout=10)
    if not result["stdout"].strip():
        return []
    pids = []
    for line in result["stdout"].splitlines():
        parts = line.strip().split()
        # 形如 " 29245 sleep 86400"
        if len(parts) >= 2 and parts[1] == "sleep" and parts[2] == "86400":
            pids.append(parts[0])
    return pids


def _start_pty_inject(device_id: str, device: str, prefix: str, frames: List[str]) -> dict:
    """
    启动 pty-inject 并循环注入指定帧

    pty 替换与注入必须在同一进程内完成：pty-inject 退出时会把设备节点
    恢复成真实串口，App 握着的 fd 就失效了。
    """
    _stop_pty(device_id, device, prefix)
    time.sleep(0.3)

    cmd = [
        DEVICE_PTY_INJECT,
        "--dev", device,
        "--inject-list", ",".join(frames),
        "--interval", str(PINJECT_INTERVAL_MS),
    ]
    shell_cmd = " ".join(cmd)
    if prefix:
        shell_cmd = f"{prefix}{shell_cmd}"
    full_cmd = f"{shell_cmd} > {PTY_LOG} 2>&1 < /dev/null &"
    _run_adb(device_id, ["shell", full_cmd], timeout=15)
    time.sleep(1.5)

    return _pty_status(device_id, device, prefix)


def _stop_pty(device_id: str, device: str, prefix: str) -> dict:
    """
    停止 pty-inject 并清理可能的残留挂载

    pty-inject 由 `sleep 86400 | pty-inject` 管道启动，停止时要一并干掉
    撑住 stdin 的 sleep 进程，否则会留下孤儿进程。
    """
    pids = _find_pty_pids(device_id, prefix)
    for pid in pids:
        _run_adb(device_id, ["shell", f"{prefix}kill -TERM {pid}"], timeout=10)

    # 等待退出
    for _ in range(15):
        time.sleep(0.2)
        if not _find_pty_pids(device_id, prefix):
            break

    # 清理撑住 stdin 的 sleep 进程（pty-inject 退出后管道会断，但 sleep 仍在）
    for pid in _find_sleep_pids(device_id, prefix):
        _run_adb(device_id, ["shell", f"{prefix}kill -TERM {pid}"], timeout=10)

    # 兜底清理残留挂载（异常退出时 pty-inject 来不及 umount）
    name = device.rsplit("/", 1)[-1]
    _run_adb(
        device_id,
        ["shell", f"{prefix}/system/bin/umount /dev/{name} 2>/dev/null; "
                  f"{prefix}/system/bin/umount -l /dev/{name} 2>/dev/null; "
                  f"{prefix}rm -f /dev/.ptybak_{name}"],
        timeout=10,
    )
    return {"stopped": bool(pids)}


def _restart_app(device_id: str, package_name: str, activity: str) -> dict:
    """重启目标 App（pty 替换后必须重启，它才会重开串口拿到 pty）"""
    if not package_name:
        return {"restarted": False, "message": "未指定包名，跳过重启"}

    stop = _run_adb(device_id, ["shell", "am", "force-stop", package_name], timeout=20)
    time.sleep(1)
    start = _run_adb(
        device_id,
        ["shell", "am", "start", "-n", activity],
        timeout=20,
    )
    return {
        "restarted": start["success"],
        "message": (start["stdout"] or start["stderr"]).strip(),
    }


@router.post("/device/serial/pty/deploy")
def deploy_pty_tools(request: SerialPortsRequest):
    """把 pty-inject / keyinject 推送到设备"""
    try:
        result = _deploy_pty_tools(request.deviceId)
        if not result["success"]:
            return {"code": 500, "message": result["message"], "data": None}
        return {
            "code": 200,
            "message": "success",
            "data": {
                "ptyInject": DEVICE_PTY_INJECT,
                "keyInject": DEVICE_KEYINJECT,
                "keymap": DEVICE_KEYMAP,
            },
        }
    except Exception as e:
        logger.error(f"部署 pty 工具失败: {e}")
        return {"code": 500, "message": f"部署 pty 工具失败: {str(e)}", "data": None}


@router.post("/device/serial/pty/status")
def pty_status(request: SerialPtyRequest):
    """查询 pty 替换状态"""
    try:
        prefix = _root_prefix(request.deviceId)
        return {
            "code": 200,
            "message": "success",
            "data": _pty_status(request.deviceId, request.device, prefix),
        }
    except Exception as e:
        logger.error(f"查询 pty 状态失败: {e}")
        return {"code": 500, "message": f"查询 pty 状态失败: {str(e)}", "data": None}


@router.post("/device/serial/pty/control")
def pty_control(request: SerialPtyRequest):
    """
    启动/停止 pty 替换

    启动流程（顺序不能反）：
      1. 先起 pty-inject 把设备节点换成 pty
      2. 再重启 App，它重开串口时拿到的就是 pty
      3. 之后注入的按键帧 App 才能收到
    """
    try:
        if not request.deviceId:
            return {"code": 400, "message": "deviceId 不能为空", "data": None}

        prefix = _root_prefix(request.deviceId)

        if request.action == "stop":
            _stop_pty(request.deviceId, request.device, prefix)
            time.sleep(0.5)
            return {
                "code": 200,
                "message": "pty 替换已停止，设备节点已恢复",
                "data": _pty_status(request.deviceId, request.device, prefix),
            }

        if request.action == "start":
            # 工具未部署则先部署
            if not _tool_exists(request.deviceId, DEVICE_PTY_INJECT):
                deploy_result = _deploy_pty_tools(request.deviceId)
                if not deploy_result["success"]:
                    return {"code": 500, "message": deploy_result["message"], "data": None}

            # 启动注入进程（pty 替换与注入必须同一进程：
            # pty-inject 一旦退出，设备节点就恢复成真实串口）
            status = _start_pty_inject(
                request.deviceId, request.device, prefix, [DEFAULT_INJECT_FRAME])
            if not status["running"]:
                return {
                    "code": 500,
                    "message": "pty-inject 启动失败",
                    "data": status,
                }
            if status["nodeType"] != "pty":
                return {
                    "code": 500,
                    "message": "pty 替换未生效，设备节点仍是真实串口",
                    "data": status,
                }

            # 重启 App，使其重开串口拿到 pty
            restart_info = {}
            if request.restartApp:
                restart_info = _restart_app(
                    request.deviceId, request.packageName, request.activity)
                time.sleep(4)
                status = _pty_status(request.deviceId, request.device, prefix)

            return {
                "code": 200,
                "message": "pty 替换已启动",
                "data": {**status, "appRestart": restart_info},
            }

        # status
        return {
            "code": 200,
            "message": "success",
            "data": _pty_status(request.deviceId, request.device, prefix),
        }
    except Exception as e:
        logger.error(f"pty 控制失败: {e}")
        return {"code": 500, "message": f"pty 控制失败: {str(e)}", "data": None}


def _build_key_frame(key_value: int) -> str:
    """
    构造键盘协议帧：55 | LEN(03) | 05 | KEY | CRC

    CRC = (0x05 + KEY) & 0xFF（接收侧不校验，但长度判断依赖它）
    """
    key = key_value & 0xFF
    return "".join([
        "55", "03", "05",
        f"{key:02X}",
        f"{(0x05 + key) & 0xFF:02X}",
    ])


def _lookup_key(raw: str) -> Optional[int]:
    """按键名或键值 -> 内部键值，无法识别返回 None"""
    name = raw.strip()
    if not name:
        return None
    if name.lower() in KEY_VALUES:
        return KEY_VALUES[name.lower()]
    # 纯数字：1..244 直接当键值；0..9 按键名处理
    if name.isdigit():
        value = int(name)
        if 0 <= value <= 9:
            return KEY_VALUES[name]
        if 1 <= value <= 244:
            return value
    if name.lower().startswith("0x"):
        try:
            value = int(name, 16)
            if 0 <= value <= 255:
                return value
        except ValueError:
            return None
    return None


def _key_frames_from_request(request: SerialKeyRequest) -> List[str]:
    """把按键参数转成协议帧列表"""
    if request.keys:
        frames = []
        for token in request.keys.split(","):
            value = _lookup_key(token)
            if value is None:
                raise ValueError(f"未知按键: {token.strip()}")
            frames.append(_build_key_frame(value))
        return frames

    if request.seq:
        try:
            if "-" in request.seq:
                lo_str, hi_str = request.seq.split("-", 1)
                lo, hi = int(lo_str), int(hi_str)
            else:
                lo = hi = int(request.seq)
        except ValueError:
            raise ValueError(f"键值范围格式错误: {request.seq}")
        return [_build_key_frame(v) for v in range(lo, hi + 1) if 0 <= v <= 255]

    if request.mapFile:
        # 布局文件由设备端 keyinject 解析，这里先读取内容自行构造帧
        result = _run_adb(
            request.deviceId,
            ["shell", f"cat {request.mapFile}"],
            timeout=15,
        )
        if not result["success"] or not result["stdout"].strip():
            raise ValueError(f"无法读取布局文件: {request.mapFile}")
        frames = []
        for line in result["stdout"].splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for token in line.replace(",", " ").split():
                value = _lookup_key(token)
                if value is None:
                    raise ValueError(f"布局文件中的按键无效: {token}")
                frames.append(_build_key_frame(value))
        return frames

    raise ValueError("未指定按键（keys / mapFile / seq）")


@router.post("/device/serial/keys")
def inject_keys(request: SerialKeyRequest):
    """
    注入按键帧

    实现方式：重启 pty-inject 的循环注入（--inject-list），由它写 pty master 端。
    注意不能直接用 keyinject 写 pty slave —— 那是同一端的两端，
    App 读的是 slave，写 slave 的数据到不了 App（实测 keyBack 无输出）。
    """
    try:
        if not request.deviceId:
            return {"code": 400, "message": "deviceId 不能为空", "data": None}

        prefix = _root_prefix(request.deviceId)

        if not _tool_exists(request.deviceId, DEVICE_PTY_INJECT):
            deploy_result = _deploy_pty_tools(request.deviceId)
            if not deploy_result["success"]:
                return {"code": 500, "message": deploy_result["message"], "data": None}

        try:
            frames = _key_frames_from_request(request)
        except ValueError as e:
            return {"code": 400, "message": str(e), "data": None}
        if not frames:
            return {"code": 400, "message": "未指定按键", "data": None}

        # 重启注入进程（pty 重建后 App 的 fd 会失效，必须一并重启 App）
        status = _start_pty_inject(
            request.deviceId, request.device, prefix, frames)
        if not status["running"]:
            return {
                "code": 500,
                "message": "注入进程启动失败",
                "data": {**status, "frames": frames},
            }
        if status["nodeType"] != "pty":
            return {
                "code": 500,
                "message": "pty 替换未生效，设备节点仍是真实串口",
                "data": {**status, "frames": frames},
            }

        restart_info = _restart_app(
            request.deviceId, DEFAULT_TARGET_APP, DEFAULT_APP_ACTIVITY)
        time.sleep(4)
        status = _pty_status(request.deviceId, request.device, prefix)

        return {
            "code": 200,
            "message": f"已注入 {len(frames)} 个按键帧",
            "data": {
                "frames": frames,
                "running": True,
                "pid": status["pid"],
                "nodeType": status["nodeType"],
                "appHolds": status["appHolds"],
                "appRestart": restart_info,
            },
        }
    except Exception as e:
        logger.error(f"按键注入失败: {e}")
        return {"code": 500, "message": f"按键注入失败: {str(e)}", "data": None}


@router.post("/device/serial/keys/list")
def list_keys(request: SerialPortsRequest):
    """列出可用的按键名与对应键值"""
    try:
        return {
            "code": 200,
            "message": "success",
            "data": {
                "keys": [{"name": name, "value": value} for name, value in KEY_VALUES.items()],
            },
        }
    except Exception as e:
        logger.error(f"获取按键列表失败: {e}")
        return {"code": 500, "message": f"获取按键列表失败: {str(e)}", "data": None}
