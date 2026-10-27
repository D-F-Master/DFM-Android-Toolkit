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
# 本地 arm64 工具目录（lib/serial/）
LOCAL_TOOL_DIRNAME = "serial"
LOCAL_WRITER_NAME = "serial-writer"
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


def _tool_exists(device_id: str) -> bool:
    """检查设备端是否已部署 serial-writer"""
    result = _run_adb(device_id, ["shell", "ls", "-l", DEVICE_WRITER], timeout=10)
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
