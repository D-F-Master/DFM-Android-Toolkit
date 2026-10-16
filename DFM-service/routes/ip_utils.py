import ipaddress
import concurrent.futures
import subprocess
from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(
    prefix="/api",
    tags=["IP工具"]
)


class IpScanRequest(BaseModel):
    ip: str
    subnetMask: str


class IpCheckRequest(BaseModel):
    ip: str


class BatchConnectRequest(BaseModel):
    ips: List[str]


def check_ip_online(ip: str, timeout: float = 0.3) -> dict:
    """
    检查单个 IP 是否在线
    
    使用 ping 命令，发 2 个包，收到字节回复才判在线。
    Windows: ping -n 2 -w <timeout_ms>
    """
    try:
        timeout_ms = int(timeout * 1000)
        # Windows 默认 ping -n 4，用 -n 2 只发 2 个包提速
        result = subprocess.run(
            ["ping", "-n", "2", "-w", str(timeout_ms), ip],
            capture_output=True, text=True, encoding='gbk', errors='replace',
            timeout=5
        )
        output = result.stdout + result.stderr

        # 只有收到字节回复才算在线（"字节=32" / "bytes=32"）
        if "字节=" in output or "bytes=" in output:
            return {"ip": ip, "online": True}
        return {"ip": ip, "online": False}
    except Exception:
        return {"ip": ip, "online": False}


def get_network_range(ip: str, subnet_mask: str) -> List[str]:
    """根据 IP 和子网掩码计算网段内的所有 IP"""
    try:
        network = ipaddress.IPv4Network(f"{ip}/{subnet_mask}", strict=False)
        hosts = [str(host) for host in network.hosts()]
        return hosts
    except Exception as e:
        raise ValueError(f"无效的网络地址或子网掩码: {str(e)}")


@router.post("/ip/scan")
def scan_network(request: IpScanRequest):
    """扫描指定网段内在线的 IP"""
    try:
        # 获取网段内所有 IP
        ip_list = get_network_range(request.ip, request.subnetMask)
        
        online_ips = []
        total = len(ip_list)
        
        # 使用线程池并发扫描（优化：从 10 提升到 200，充分利用网络 IO 等待）
        max_workers = min(200, total)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ip = {
                executor.submit(check_ip_online, ip): ip
                for ip in ip_list
            }
            
            for future in concurrent.futures.as_completed(future_to_ip):
                try:
                    result = future.result()
                    if result["online"]:
                        online_ips.append(result["ip"])
                except Exception:
                    pass
        
        # 按 IP 排序
        online_ips.sort(key=lambda x: tuple(map(int, x.split('.'))))
        
        return {
            "code": 200,
            "message": "扫描完成",
            "data": {
                "total": total,
                "onlineCount": len(online_ips),
                "onlineIps": online_ips
            }
        }
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    except Exception as e:
        return {"code": 500, "message": f"扫描失败: {str(e)}", "data": None}


@router.post("/ip/check")
def check_ip(request: IpCheckRequest):
    """检查单个 IP 是否在线"""
    try:
        result = check_ip_online(request.ip)
        return {
            "code": 200,
            "message": "success",
            "data": result
        }
    except Exception as e:
        return {"code": 500, "message": f"检查失败: {str(e)}", "data": None}


@router.post("/ip/batch-connect")
def batch_connect(request: BatchConnectRequest):
    """批量执行 adb connect 命令"""
    try:
        if not request.ips or len(request.ips) == 0:
            return {"code": 400, "message": "IP 列表不能为空", "data": None}
        
        results = []
        
        # 使用线程池并发执行 adb connect
        def execute_connect(ip: str) -> dict:
            try:
                cmd = f'adb connect {ip}'
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=10
                )
                
                success = result.returncode == 0 and 'connected' in result.stdout.lower()
                return {
                    "ip": ip,
                    "success": success,
                    "message": result.stdout.strip() if result.stdout else result.stderr.strip()
                }
            except Exception as e:
                return {
                    "ip": ip,
                    "success": False,
                    "message": str(e)
                }
        
        max_workers = min(20, len(request.ips))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ip = {
                executor.submit(execute_connect, ip): ip
                for ip in request.ips
            }
            
            for future in concurrent.futures.as_completed(future_to_ip):
                try:
                    result = future.result()
                    results.append(result)
                except Exception:
                    pass
        
        # 按 IP 排序（处理带端口号的情况）
        def sort_key(item):
            ip_part = item["ip"].split(':')[0]  # 去掉端口号
            return tuple(map(int, ip_part.split('.')))
        
        results.sort(key=sort_key)
        
        success_count = sum(1 for r in results if r["success"])
        failed_count = len(results) - success_count
        
        return {
            "code": 200,
            "message": "批量连接完成",
            "data": {
                "total": len(results),
                "successCount": success_count,
                "failedCount": failed_count,
                "results": results
            }
        }
    except Exception as e:
        return {"code": 500, "message": f"批量连接失败: {str(e)}", "data": None}
