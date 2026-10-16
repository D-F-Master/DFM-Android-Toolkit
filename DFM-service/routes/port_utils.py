import socket
import concurrent.futures
from fastapi import APIRouter
import subprocess
import re
import logging
from pydantic import BaseModel

logger = logging.getLogger("ToolServer")

router = APIRouter(
    prefix="/api",
    tags=["端口管理"]
)


class PortCheckRequest(BaseModel):
    port: int


class PortKillRequest(BaseModel):
    pid: int


class PortScanRequest(BaseModel):
    target: str
    startPort: int = None
    endPort: int = None
    ports: list = None


class TelnetRequest(BaseModel):
    target: str
    port: int


@router.post("/port/check")
def check_port(request: PortCheckRequest):
    """检查端口占用情况"""
    logger.info(f"Checking port: {request.port}")
    if not request.port:
        return {"code": 400, "message": "Port is required", "data": None}
    
    try:
        # 使用 netstat 命令检查端口占用
        command = f"netstat -ano | findstr :{request.port}"
        result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding='gbk')
        
        if result.returncode != 0 and not result.stdout.strip():
            logger.info(f"Port {request.port} is not in use")
            return {"code": 200, "message": "success", "data": []}
        
        processes = []
        lines = result.stdout.strip().split('\n')
        
        for line in lines:
            trimmed_line = line.strip()
            if trimmed_line:
                parts = trimmed_line.split()
                if len(parts) >= 5:
                    processes.append({
                        "protocol": parts[0],
                        "localAddress": parts[1],
                        "foreignAddress": parts[2],
                        "state": parts[3] if len(parts) > 3 else "",
                        "pid": parts[-1]
                    })
        
        # 获取进程名称和路径
        def get_process_info(pid):
            try:
                # 获取进程名称
                process_command = f'tasklist /fi "PID eq {pid}" /fo csv /nh'
                process_result = subprocess.run(process_command, shell=True, capture_output=True, text=True, encoding='gbk')
                process_name = '未知进程'
                if process_result.stdout:
                    process_info = process_result.stdout.strip().split(',')
                    if len(process_info) >= 2:
                        process_name = process_info[0].replace('"', '')
                
                # 获取进程完整路径
                process_path = '未知路径'
                try:
                    wmic_command = f'wmic process where ProcessId={pid} get ExecutablePath /format:list'
                    wmic_result = subprocess.run(wmic_command, shell=True, capture_output=True, text=True, encoding='gbk')
                    if wmic_result.stdout:
                        for line in wmic_result.stdout.strip().split('\n'):
                            if line.startswith('ExecutablePath='):
                                path = line.split('=', 1)[1].strip()
                                if path:
                                    process_path = path
                                    break
                except Exception as e:
                    logger.warning(f'Failed to get process path: {e}')
                
                return process_name, process_path
            except Exception as e:
                logger.warning(f'Failed to get process info: {e}')
                return '未知进程', '未知路径'
        
        # 为每个进程添加进程名称和路径
        # 优化：一次性调用 get_process_info，避免两次执行 tasklist + wmic
        processes_with_info = []
        for process in processes:
            name, path = get_process_info(process["pid"])
            processes_with_info.append({
                **process,
                "process": name,
                "processPath": path
            })
        
        logger.info(f"Found {len(processes_with_info)} processes on port {request.port}")
        return {"code": 200, "message": "success", "data": processes_with_info}
    
    except Exception as e:
        logger.error(f"Port check error: {str(e)}")
        return {"code": 500, "message": str(e), "data": None}


@router.post("/port/kill")
def kill_process(request: PortKillRequest):
    """终止指定PID的进程"""
    logger.info(f"Killing process: {request.pid}")
    if not request.pid:
        return {"code": 400, "message": "PID is required", "data": None}
    
    try:
        # 使用 taskkill 命令终止进程
        command = f"taskkill /F /PID {request.pid}"
        result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding='gbk')
        
        if result.returncode != 0:
            logger.error(f"Failed to kill process {request.pid}: {result.stderr}")
            return {"code": 500, "message": result.stderr.strip() if result.stderr else "Failed to kill process", "data": None}
        
        logger.info(f"Process {request.pid} killed successfully")
        return {"code": 200, "message": "success", "data": None}
    
    except Exception as e:
        logger.error(f"Kill process error: {str(e)}")
        return {"code": 500, "message": str(e), "data": None}


def check_port_status(target: str, port: int, timeout: float = 0.3) -> dict:
    """检查单个端口状态 - 优化版"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((target, port))
        sock.close()
        
        if result == 0:
            return {
                "port": port,
                "status": "open",
                "service": identify_service(port)
            }
        else:
            return {
                "port": port,
                "status": "closed"
            }
    except Exception:
        return {
            "port": port,
            "status": "closed"
        }


def identify_service(port: int) -> str:
    """根据端口号识别常见服务"""
    common_services = {
        21: "FTP",
        22: "SSH",
        23: "Telnet",
        25: "SMTP",
        53: "DNS",
        80: "HTTP",
        110: "POP3",
        143: "IMAP",
        443: "HTTPS",
        993: "IMAPS",
        995: "POP3S",
        3306: "MySQL",
        3389: "RDP",
        5432: "PostgreSQL",
        6379: "Redis",
        8080: "HTTP-Proxy",
        8443: "HTTPS-Alt",
        27017: "MongoDB"
    }
    return common_services.get(port, "Unknown")


def validate_ip_address(ip: str) -> bool:
    """验证IP地址格式"""
    pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
    match = re.match(pattern, ip)
    if not match:
        return False
    
    for i in range(1, 5):
        octet = int(match.group(i))
        if octet < 0 or octet > 255:
            return False
    return True


@router.post("/port/scan")
def scan_ports(request: PortScanRequest):
    """扫描指定范围的端口或指定端口列表 - 优化版"""
    logger.info(f"Starting port scan on {request.target}")
    
    # 验证IP地址
    if not validate_ip_address(request.target):
        logger.warning(f"Invalid IP address format: {request.target}")
        return {"code": 400, "message": "无效的 IP 地址格式", "data": None}
    
    # 确定要扫描的端口列表
    port_list = []
    
    # 如果传入了端口数组，使用数组
    if request.ports is not None:
        if not isinstance(request.ports, list) or len(request.ports) == 0:
            logger.warning(f"Invalid ports array: {request.ports}")
            return {"code": 400, "message": "端口数组不能为空", "data": None}
        
        # 验证端口数组中的每个端口
        for port in request.ports:
            if not isinstance(port, int) or port < 1 or port > 65535:
                logger.warning(f"Invalid port in array: {port}")
                return {"code": 400, "message": f"端口号 {port} 无效，必须在 1-65535 范围内", "data": None}
        
        port_list = sorted(request.ports)
        logger.info(f"Scanning {len(port_list)} specified ports on {request.target}")
    
    # 否则使用起始和结束端口
    elif request.startPort is not None and request.endPort is not None:
        # 验证端口范围
        if request.startPort < 1 or request.startPort > 65535:
            logger.warning(f"Invalid start port: {request.startPort}")
            return {"code": 400, "message": "起始端口号必须在 1-65535 范围内", "data": None}
        
        if request.endPort < 1 or request.endPort > 65535:
            logger.warning(f"Invalid end port: {request.endPort}")
            return {"code": 400, "message": "结束端口号必须在 1-65535 范围内", "data": None}
        
        if request.startPort > request.endPort:
            logger.warning(f"Start port {request.startPort} > end port {request.endPort}")
            return {"code": 400, "message": "起始端口号不能大于结束端口号", "data": None}
        
        port_list = list(range(request.startPort, request.endPort + 1))
        logger.info(f"Scanning ports {request.startPort}-{request.endPort} on {request.target}")
    
    else:
        logger.warning("Missing port range or ports array")
        return {"code": 400, "message": "请提供端口范围(startPort/endPort)或端口数组(ports)", "data": None}
    
    try:
        results = []
        # 计算端口总数
        port_count = len(port_list)
        
        # 根据端口数量动态调整并发数和超时时间
        # 优化：小范围用 100 并发，中范围用 200，大范围用 300
        if port_count <= 100:
            max_workers = min(100, port_count)
            timeout = 0.3
        elif port_count <= 1000:
            max_workers = 200
            timeout = 0.2
        else:
            max_workers = 300
            timeout = 0.15
        
        logger.info(f"Scan configuration: {port_count} ports, {max_workers} workers, {timeout}s timeout")
        
        open_count = 0
        closed_count = 0
        error_count = 0
        
        # 使用线程池并发扫描端口，提高扫描速度
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有端口扫描任务
            future_to_port = {
                executor.submit(check_port_status, request.target, port, timeout): port
                for port in port_list
            }
            
            # 收集结果
            for future in concurrent.futures.as_completed(future_to_port):
                try:
                    result = future.result()
                    results.append(result)
                    if result["status"] == "open":
                        open_count += 1
                    else:
                        closed_count += 1
                except Exception as e:
                    port = future_to_port[future]
                    logger.error(f"Error scanning port {request.target}:{port}: {str(e)}")
                    results.append({
                        "port": port,
                        "status": "closed"
                    })
                    error_count += 1
        
        # 按端口号排序结果
        results.sort(key=lambda x: x["port"])
        
        logger.info(f"Port scan completed on {request.target}: Total={port_count}, Open={open_count}, Closed={closed_count}, Errors={error_count}")
        
        return {
            "code": 200,
            "message": "扫描成功",
            "data": results
        }
    
    except Exception as e:
        logger.error(f"Port scan error on {request.target}: {str(e)}", exc_info=True)
        return {"code": 500, "message": str(e), "data": None}


@router.post("/port/telnet")
def telnet_port(request: TelnetRequest):
    """打开命令行窗口并执行telnet命令访问指定IP和端口"""
    logger.info(f"Opening telnet to {request.target}:{request.port}")
    
    # 验证参数
    if not request.target or not request.port:
        return {"code": 400, "message": "目标IP和端口号都是必填项", "data": None}
    
    # 验证端口范围
    if request.port < 1 or request.port > 65535:
        return {"code": 400, "message": "端口号必须在 1-65535 范围内", "data": None}
    
    try:
        # 构建telnet命令
        telnet_command = f"telnet {request.target} {request.port}"
        
        # 在Windows下打开新的CMD窗口执行telnet命令
        subprocess.Popen(
            f'start cmd.exe /k "{telnet_command}"',
            shell=True
        )
        
        logger.info(f"Telnet window opened for {request.target}:{request.port}")
        return {
            "code": 200,
            "message": "success",
            "data": {
                "command": telnet_command
            }
        }
    
    except FileNotFoundError:
        logger.error("Telnet command not found")
        return {
            "code": 500,
            "message": "执行失败: telnet命令不存在或未安装",
            "data": None
        }
    except Exception as e:
        logger.error(f"Telnet execution error: {str(e)}")
        return {"code": 500, "message": f"执行失败: {str(e)}", "data": None}


# ── 端口转发 (netsh interface portproxy) ──────────────────────────────

class AddPortProxyRequest(BaseModel):
    listenPort: int
    listenAddress: str = "0.0.0.0"
    connectPort: int
    connectAddress: str
    protocol: str = "v4tov4"


class DeletePortProxyRequest(BaseModel):
    listenPort: int
    listenAddress: str = "0.0.0.0"
    protocol: str = "v4tov4"


def _is_ip(s: str) -> bool:
    """判断字符串是否为 IPv4 地址"""
    parts = s.split('.')
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _parse_portproxy_output(output: str) -> list:
    """
    解析 netsh interface portproxy show all 输出

    兼容中/英文系统两种列顺序：
      - 中文：地址 端口 地址 端口
      - 英文：Port Address Connect_to Port Address
    """
    rules = []
    lines = output.split('\n')

    capture = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 分隔行特征：连续等号(英文) 或 连续短横线(中文)
        sep_chars = set(stripped)
        if sep_chars <= {'=', ' ', '-'} and ('=' in sep_chars or '-' in sep_chars):
            capture = True
            continue
        if not capture:
            continue

        parts = stripped.split()
        if len(parts) < 4:
            continue

        # 用第一个字段判断格式：英文格式首字段是数字(端口)，中文格式首字段是 IP(地址)
        if _is_ip(parts[0]):
            # 中文顺序：listenAddress listenPort connectAddress connectPort [connectBindAddress]
            rule = {
                "listenAddress": parts[0],
                "listenPort": int(parts[1]) if parts[1].isdigit() else parts[1],
                "connectAddress": parts[2],
                "connectPort": int(parts[3]) if parts[3].isdigit() else parts[3],
            }
            if len(parts) >= 5:
                rule["connectBindAddress"] = parts[4]
        else:
            # 英文顺序：listenPort listenAddress connectAddress connectPort [connectBindAddress]
            rule = {
                "listenPort": int(parts[0]) if parts[0].isdigit() else parts[0],
                "listenAddress": parts[1],
                "connectAddress": parts[2],
                "connectPort": int(parts[3]) if parts[3].isdigit() else parts[3],
            }
            if len(parts) >= 5:
                rule["connectBindAddress"] = parts[4]

        rules.append(rule)

    return rules


@router.post("/portproxy/list")
def list_port_proxy():
    """
    查看系统级端口转发规则

    执行 netsh interface portproxy show all 返回所有规则。
    """
    logger.info("Listing all port proxy rules")
    try:
        result = subprocess.run(
            "netsh interface portproxy show all",
            shell=True,
            capture_output=True,
            text=True,
            encoding='gbk',
            errors='replace'
        )

        output = (result.stdout or "") + (result.stderr or "")

        if result.returncode != 0:
            # 需要管理员权限时的典型错误
            error_msg = output.strip() or "执行失败，可能需要管理员权限"
            logger.warning(f"Port proxy list failed: {error_msg}")
            return {"code": 500, "message": error_msg, "data": None}

        rules = _parse_portproxy_output(output)
        logger.info(f"Found {len(rules)} port proxy rule(s)")
        return {"code": 200, "message": "success", "data": rules}

    except FileNotFoundError:
        logger.error("netsh command not found")
        return {"code": 500, "message": "netsh 命令不存在", "data": None}
    except Exception as e:
        logger.error(f"Port proxy list error: {str(e)}")
        return {"code": 500, "message": str(e), "data": None}


@router.post("/portproxy/add")
def add_port_proxy(request: AddPortProxyRequest):
    """
    添加端口转发规则

    需要以管理员权限运行此服务。
    支持的 protocol: v4tov4, v4tov6, v6tov4, v6tov6（默认 v4tov4）
    """
    logger.info(
        f"Adding port proxy: {request.protocol} "
        f"listenport={request.listenPort} listenaddress={request.listenAddress} "
        f"connectport={request.connectPort} connectaddress={request.connectAddress}"
    )

    # 参数校验
    if request.listenPort < 1 or request.listenPort > 65535:
        return {"code": 400, "message": "监听端口号必须在 1-65535 范围内", "data": None}
    if request.connectPort < 1 or request.connectPort > 65535:
        return {"code": 400, "message": "目标端口号必须在 1-65535 范围内", "data": None}
    if not request.connectAddress:
        return {"code": 400, "message": "目标地址不能为空", "data": None}

    valid_protocols = {"v4tov4", "v4tov6", "v6tov4", "v6tov6"}
    if request.protocol not in valid_protocols:
        return {
            "code": 400,
            "message": f"不支持的协议类型: {request.protocol}，支持: {', '.join(sorted(valid_protocols))}",
            "data": None
        }

    try:
        command = (
            f'netsh interface portproxy add {request.protocol} '
            f'listenport={request.listenPort} '
            f'listenaddress={request.listenAddress} '
            f'connectport={request.connectPort} '
            f'connectaddress={request.connectAddress}'
        )

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding='gbk',
            errors='replace'
        )

        output = (result.stdout or "") + (result.stderr or "")
        output_stripped = output.strip()

        if result.returncode != 0:
            logger.warning(f"Add port proxy failed: {output_stripped}")
            return {"code": 500, "message": output_stripped or "添加失败，可能需要管理员权限", "data": None}

        logger.info("Port proxy added successfully")
        return {"code": 200, "message": "success", "data": None}

    except FileNotFoundError:
        logger.error("netsh command not found")
        return {"code": 500, "message": "netsh 命令不存在", "data": None}
    except Exception as e:
        logger.error(f"Add port proxy error: {str(e)}")
        return {"code": 500, "message": str(e), "data": None}


@router.post("/portproxy/delete")
def delete_port_proxy(request: DeletePortProxyRequest):
    """
    删除端口转发规则

    需要以管理员权限运行此服务。
    通过 listenPort + listenAddress + protocol 唯一定位一条规则。
    """
    logger.info(
        f"Deleting port proxy: {request.protocol} "
        f"listenport={request.listenPort} listenaddress={request.listenAddress}"
    )

    if request.listenPort < 1 or request.listenPort > 65535:
        return {"code": 400, "message": "监听端口号必须在 1-65535 范围内", "data": None}

    valid_protocols = {"v4tov4", "v4tov6", "v6tov4", "v6tov6"}
    if request.protocol not in valid_protocols:
        return {
            "code": 400,
            "message": f"不支持的协议类型: {request.protocol}，支持: {', '.join(sorted(valid_protocols))}",
            "data": None
        }

    try:
        command = (
            f'netsh interface portproxy delete {request.protocol} '
            f'listenport={request.listenPort} '
            f'listenaddress={request.listenAddress}'
        )

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding='gbk',
            errors='replace'
        )

        output = (result.stdout or "") + (result.stderr or "")
        output_stripped = output.strip()

        if result.returncode != 0:
            logger.warning(f"Delete port proxy failed: {output_stripped}")
            return {"code": 500, "message": output_stripped or "删除失败，可能规则不存在或需要管理员权限", "data": None}

        logger.info("Port proxy deleted successfully")
        return {"code": 200, "message": "success", "data": None}

    except FileNotFoundError:
        logger.error("netsh command not found")
        return {"code": 500, "message": "netsh 命令不存在", "data": None}
    except Exception as e:
        logger.error(f"Delete port proxy error: {str(e)}")
        return {"code": 500, "message": str(e), "data": None}
