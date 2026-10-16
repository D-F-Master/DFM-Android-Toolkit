import asyncio
import concurrent.futures
import subprocess
import os
import re
import tempfile
import time
import uuid
from config import logger
from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel

from config.queue_config import task_queue
from utils.tool_manager import get_adb_path, get_scrcpy_path  # 导入工具管理器

router = APIRouter(
    prefix="/api",
    tags=["ADB工具"]
)


def execute_adb_command(command: str, timeout: int = 5) -> dict:
    """
    执行 ADB 命令

    同步执行ADB命令，请求会等待数据返回
    自动将命令中的 'adb' 替换为内置工具的完整路径，
    确保在打包后也能正常工作。
    
    Args:
        command: ADB 命令字符串
        timeout: 命令超时时间（秒），默认5秒，安装APK等耗时操作需传更长超时
        
    Returns:
        dict: 执行结果
            - success: bool, 是否成功
            - stdout: str, 标准输出
            - stderr: str, 错误输出
            - error: str, 错误信息（失败时）
    """
    logger.info(f"Executing ADB command: {command}")
    try:
        # 获取 adb 工具的完整路径
        adb_path = get_adb_path()
        # 替换命令中的 adb 为完整路径（处理带引号和不带引号的情况）
        command = command.replace('adb ', f'"{adb_path}" ')
        
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        if result.returncode != 0:
            logger.warning(f"ADB command failed: {result.stderr or result.stdout}")
            return {
                "success": False,
                "error": result.stderr or result.stdout,
                "stderr": result.stderr
            }
        logger.info(f"ADB command executed successfully")
        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except subprocess.TimeoutExpired:
        logger.error(f"ADB command timeout: {command}")
        return {"success": False, "error": "Command timeout", "stderr": ""}
    except Exception as e:
        logger.error(f"ADB command error: {str(e)}")
        return {"success": False, "error": str(e), "stderr": ""}




def _extract_mac(text: str) -> str:
    """从文本中提取第一个 MAC 地址（xx:xx:xx:xx:xx:xx 格式），未找到返回空串"""
    match = re.search(r'([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}', text or "")
    return match.group(0) if match else ""


def _is_valid_mac(mac: str) -> bool:
    """排除 Android 10+ 随机 MAC 和全零 MAC 等无效值"""
    if not mac:
        return False
    return mac not in ("02:00:00:00:00:00", "00:00:00:00:00:00")


def _parse_getprop_output(output: str) -> dict:
    """解析 adb shell getprop 全量输出为 {属性名: 值} 字典"""
    props = {}
    for line in output.splitlines():
        match = re.match(r'\[(.*?)\]: \[(.*?)\]', line)
        if match:
            props[match.group(1)] = match.group(2)
    return props


def _get_mac_via_wifi_toggle(device_id: str) -> str:
    """
    临时开启 WiFi 读取真实 MAC（读取后恢复 WiFi 原状态）

    部分设备（如无 WiFi 开启状态的机顶盒）WiFi 关闭时 wlan0 接口不存在，
    需临时开启 WiFi 等接口出现后读取 MAC，再恢复原状态。
    """
    # 记录 WiFi 原状态
    status_result = execute_adb_command(f"adb -s {device_id} shell dumpsys wifi")
    wifi_was_enabled = "Wi-Fi is enabled" in status_result.get("stdout", "")

    try:
        # 开启 WiFi
        execute_adb_command(f"adb -s {device_id} shell svc wifi enable")

        # 轮询等待 wlan0 接口出现并读取 MAC（最多 20 秒）
        mac = ""
        for _ in range(20):
            time.sleep(1)
            result = execute_adb_command(f"adb -s {device_id} shell ip addr show wlan0")
            if result["success"]:
                mac = _extract_mac(result["stdout"])
                if _is_valid_mac(mac):
                    break
        return mac
    finally:
        # 恢复 WiFi 原状态
        if wifi_was_enabled:
            execute_adb_command(f"adb -s {device_id} shell svc wifi enable")
        else:
            execute_adb_command(f"adb -s {device_id} shell svc wifi disable")


def _get_mac(device_id: str, iface: str) -> str:
    """
    获取指定网卡类型（wlan/eth）的 MAC 地址，按顺序尝试多种方法，
    任一方法成功即返回。

    设备不在线时直接返回 "Unknown"，避免对离线设备反复发送命令。
    wlan 最后会尝试临时开启 WiFi 读取（读后恢复原状态）。
    """
    # 兜底：设备不在线时直接返回，避免反复发送请求
    state_result = execute_adb_command(f"adb -s {device_id} get-state")
    if not state_result.get("success") or state_result.get("stdout", "").strip() != "device":
        logger.warning(f"Device {device_id} not online, skip {iface} MAC fetch")
        return "Unknown"

    if iface == "wlan":
        candidates = [
            f"adb -s {device_id} shell getprop ro.boot.wifimacaddr",
            f"adb -s {device_id} shell cat /sys/class/net/wlan0/address",
            f"adb -s {device_id} shell ip addr show wlan0",
            f"adb -s {device_id} shell dumpsys wifi",
        ]
    else:
        candidates = [
            f"adb -s {device_id} shell getprop ro.boot.ethmacaddr",
            f"adb -s {device_id} shell cat /sys/class/net/eth0/address",
            f"adb -s {device_id} shell ip addr show eth0",
            f"adb -s {device_id} shell ifconfig eth0",
        ]

    for cmd in candidates:
        result = execute_adb_command(cmd)
        if not result["success"]:
            continue
        mac = _extract_mac(result["stdout"])
        if _is_valid_mac(mac):
            logger.info(f"Got {iface} MAC via: {cmd.split('shell ')[-1]}")
            return mac

    # wlan 最后兜底：临时开启 WiFi 读取
    if iface == "wlan":
        mac = _get_mac_via_wifi_toggle(device_id)
        if _is_valid_mac(mac):
            logger.info("Got wlan MAC via: wifi toggle (temporarily enabled wifi)")
            return mac
    return "Unknown"


# 请求模型
class CommandRequest(BaseModel):
    command: str


class DeviceIdRequest(BaseModel):
    deviceId: str


class DeviceIdsRequest(BaseModel):
    deviceIds: List[str]


class AppInfoRequest(BaseModel):
    deviceId: str
    packageName: str


class PackageRequest(BaseModel):
    deviceId: str
    packageName: str
    activity: Optional[str] = None


class ExtractRequest(BaseModel):
    deviceId: str
    packageName: str
    localPath: str


class PathRequest(BaseModel):
    deviceId: str
    path: str


class KeycodeRequest(BaseModel):
    deviceId: str
    keycode: int


class FileDownloadRequest(BaseModel):
    deviceId: str
    remotePath: str
    localPath: str


class FileUploadRequest(BaseModel):
    deviceId: str
    remotePath: Optional[str] = "/sdcard/"


class FileReadRequest(BaseModel):
    deviceId: str
    remotePath: str


class BasicKeyRequest(BaseModel):
    deviceId: str
    key: str


class VolumeControlRequest(BaseModel):
    deviceId: str
    action: str  # up, down, mute, unmute
    stream: Optional[str] = "media"  # media, ring, alarm, notification


class VolumeStatusRequest(BaseModel):
    deviceId: str
    stream: Optional[str] = "media"  # media, ring, alarm, notification


class DeviceCommandsRequest(BaseModel):
    deviceIds: List[str]
    command: str


class MonitorRequest(BaseModel):
    deviceId: str


class MonitorRateRequest(BaseModel):
    deviceId: str
    duration: Optional[int] = 1  # 采样时长（秒），限幅 1-5


# 获取设备列表
@router.post("/devices")
def get_devices():
    result = execute_adb_command("adb devices -l")
    if result["success"]:
        devices = []
        lines = result["stdout"].split('\n')
        
        for i in range(1, len(lines)):
            line = lines[i].strip()
            if line:
                parts = line.split()
                device_id = parts[0]
                device_info = {}
                
                # 解析设备信息
                for j in range(1, len(parts)):
                    if ':' in parts[j]:
                        key, value = parts[j].split(':', 1)
                        device_info[key] = value
                
                devices.append({"deviceId": device_id, **device_info})
        
        return {"code": 200, "message": "success", "data": devices}
    else:
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}


# 获取设备信息
@router.post("/device/info")
def get_device_info(request: DeviceIdRequest):
    device_id = request.deviceId
    
    # 检查设备连接状态
    devices_result = execute_adb_command("adb devices")
    device_status = "离线"
    if devices_result["success"]:
        lines = devices_result["stdout"].split('\n')
        for line in lines[1:]:
            if line.strip().startswith(device_id):
                parts = line.strip().split()
                if len(parts) >= 2:
                    status = parts[1]
                    if status == "device":
                        device_status = "在线"
                    elif status == "unauthorized":
                        device_status = "未授权"
                    else:
                        device_status = "离线"
                break

    # 一次 getprop 获取所有属性（原为 13 次独立命令，合并后大幅降低耗时）
    props = {}
    props_result = execute_adb_command(f"adb -s {device_id} shell getprop")
    if props_result["success"]:
        props = _parse_getprop_output(props_result["stdout"])

    # 获取内核版本
    kernel_result = execute_adb_command(f"adb -s {device_id} shell cat /proc/version")

    # MAC 地址：先从 getprop 结果读取，缺失/无效再走多方法 fallback 链
    wifi_mac = props.get('ro.boot.wifimacaddr', '')
    if not _is_valid_mac(wifi_mac):
        wifi_mac = _get_mac(device_id, "wlan")
    eth_mac = props.get('ro.boot.ethmacaddr', '')
    if not _is_valid_mac(eth_mac):
        eth_mac = _get_mac(device_id, "eth")

    # 处理内核版本，只提取关键信息
    kernel_version = "Unknown"
    if kernel_result["success"] and kernel_result["stdout"].strip():
        # 提取 Linux version 4.19.191 和日期 #4 SMP PREEMPT Fri Mar 6 15:26:02 CST 2026
        match = re.search(r'Linux version (\S+).*?(#\d+\s+SMP\s+PREEMPT\s+\w+\s+\w+\s+\d+\s+[\d:]+\s+\w+\s+\d+)', kernel_result["stdout"])
        if match:
            kernel_version = f"{match.group(1)} {match.group(2)}"
        else:
            kernel_version = kernel_result["stdout"].strip()[:100]

    # 处理解锁状态，转换为中文
    unlock_status_map = {
        "green": "已解锁",
        "orange": "已解锁（警告）",
        "yellow": "未锁定",
        "red": "已锁定",
        "unknown": "未知"
    }
    unlock_raw = props.get('ro.boot.verifiedbootstate', 'Unknown')
    unlock_status = unlock_status_map.get(unlock_raw, unlock_raw)

    info = {
        "status": device_status,
        "model": props.get('ro.product.model', 'Unknown'),
        "version": props.get('ro.build.version.release', 'Unknown'),
        "manufacturer": props.get('ro.product.manufacturer', 'Unknown'),
        "deviceName": props.get('ro.product.name', 'Unknown'),
        "displayVersion": props.get('ro.build.display.id', 'Unknown'),
        "androidVersion": props.get('ro.build.version.release', 'Unknown'),
        "kernelVersion": kernel_version,
        "buildDate": props.get('ro.build.date', 'Unknown'),
        "codename": props.get('ro.product.device', 'Unknown'),
        "unlockStatus": unlock_status,
        "sn": props.get('ro.serialno', 'Unknown'),
        "wifiMac": wifi_mac,
        "ethMac": eth_mac
    }
    
    return {"code": 200, "message": "success", "data": info}


# 获取设备 SN 号
@router.post("/device/sn")
def get_device_sn(request: DeviceIdRequest):
    """
    通过 deviceId 获取设备的 SN 号
    
    执行 adb shell getprop ro.serialno 获取设备序列号。
    """
    device_id = request.deviceId
    result = execute_adb_command(f"adb -s {device_id} shell getprop ro.serialno")
    if result["success"]:
        sn = result["stdout"].strip()
        if sn:
            return {"code": 200, "message": "success", "data": {"sn": sn}}
        return {"code": 500, "message": "未获取到 SN 号", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}


# 断开设备连接
@router.post("/device/disconnect")
def disconnect_device(request: DeviceIdRequest):
    """
    断开指定 ADB 设备的连接
    
    执行 adb disconnect 命令断开与设备的连接。
    
    Args:
        deviceId: 设备 ID（IP:端口 或 序列号）
        
    Returns:
        成功返回 code 200，失败返回错误信息
    """
    device_id = request.deviceId
    logger.info(f"Disconnecting device: {device_id}")

    result = _disconnect_one(device_id)
    if result["success"]:
        logger.info(f"Device disconnected successfully: {device_id}")
        return {"code": 200, "message": "success", "data": None}

    error_msg = result.get("message", "断开失败")
    logger.error(f"Failed to disconnect device {device_id}: {error_msg}")
    return {"code": 500, "message": error_msg, "data": None}


def _disconnect_one(device_id: str) -> dict:
    """
    断开单个设备，返回 {"success": bool, "message": str}

    设备离线（如已断电/重启）时 adb server 可能已将其移出设备列表，
    此时 disconnect 报 no such device，但目标状态（设备不在连接列表中）已达成，视为成功
    """
    result = execute_adb_command(f"adb disconnect {device_id}")
    if result["success"]:
        return {"success": True, "message": (result.get("stdout") or "").strip()}

    check = execute_adb_command("adb devices")
    if check["success"]:
        listed = any(
            line.strip().startswith(device_id)
            for line in check["stdout"].splitlines()
            if line.strip()
        )
        if not listed:
            logger.info(f"Device {device_id} already absent from adb list, treat as disconnected")
            return {"success": True, "message": "already disconnected"}

    return {"success": False, "message": (result.get("error") or "断开失败").strip()}


# 断开全部网络 ADB 连接
@router.post("/device/disconnect/all")
def disconnect_all_devices():
    """
    断开所有通过网络连接的 ADB 设备（deviceId 形如 ip:端口）

    USB 设备为物理连接，无法通过 adb 断开，自动跳过。
    逐台并发执行 adb disconnect，返回每台设备的结果。
    """
    logger.info("Disconnecting all network adb devices")

    devices_result = execute_adb_command("adb devices")
    if not devices_result["success"]:
        error_msg = devices_result.get("error", "获取设备列表失败")
        logger.error(f"Failed to list devices for disconnect-all: {error_msg}")
        return {"code": 500, "message": error_msg, "data": None}

    # 只筛选网络设备（deviceId 含冒号，即 ip:端口），USB 序列号设备跳过
    network_devices = []
    for line in devices_result["stdout"].splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        device_id = line.split()[0]
        if ":" in device_id:
            network_devices.append(device_id)

    if not network_devices:
        return {
            "code": 200,
            "message": "success",
            "data": {"total": 0, "successCount": 0, "failedCount": 0, "results": []},
        }

    def exec_disconnect(device_id: str) -> dict:
        result = _disconnect_one(device_id)
        return {
            "deviceId": device_id,
            "success": result["success"],
            "message": result["message"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, len(network_devices))) as executor:
        futures = {executor.submit(exec_disconnect, did): did for did in network_devices}
        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                results.append({"deviceId": futures[future], "success": False, "message": str(e)})

    # 按 IP 数值排序
    def sort_key(item):
        ip_part = item["deviceId"].split(":")[0]
        try:
            return tuple(map(int, ip_part.split(".")))
        except ValueError:
            return (999, 999, 999, 999)

    results.sort(key=sort_key)
    success_count = sum(1 for r in results if r["success"])

    logger.info(f"Disconnect-all done: {success_count}/{len(results)} succeeded")
    return {
        "code": 200,
        "message": "success",
        "data": {
            "total": len(results),
            "successCount": success_count,
            "failedCount": len(results) - success_count,
            "results": results,
        },
    }


# 执行自定义 ADB 命令
@router.post("/command")
def execute_command(request: CommandRequest):
    if not request.command:
        return {"code": 400, "message": "Command is required", "data": None}
    
    result = execute_adb_command(request.command)
    if result["success"]:
        return {"code": 200, "message": "success", "data": {"stdout": result["stdout"], "stderr": result["stderr"]}}
    else:
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}


# 获取应用列表（仅第三方应用）
@router.post("/device/apps")
def get_apps(request: DeviceIdRequest):
    """
    获取设备中已安装的第三方应用列表
    
    返回包名列表，如需详细信息请使用 /device/app/info 接口
    """
    device_id = request.deviceId
    result = execute_adb_command(f"adb -s {device_id} shell pm list packages -3")
    if result["success"]:
        packages = []
        lines = result["stdout"].split('\n')
        
        for line in lines:
            if line and line.startswith('package:'):
                package_name = line[8:].strip()
                if package_name:
                    packages.append(package_name)
        
        return {"code": 200, "message": "success", "data": packages}
    else:
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}


# 批量获取应用信息（优化版）
@router.post("/device/apps/batch-info")
def get_apps_batch_info(request: DeviceIdRequest):
    """
    批量获取应用基本信息（快速版本）
    
    使用 dumpsys 快速获取所有应用的基本信息，避免逐个拉取 APK。
    适合在应用列表页面快速展示。
    
    Args:
        deviceId: 设备 ID
        
    Returns:
        应用信息列表，包含 packageName, appName, versionName 等
    """
    from utils.app_label_utils import load_label_cache, extract_app_name_from_dumpsys
    
    device_id = request.deviceId
    cache = load_label_cache()
    
    # 获取所有第三方应用包名
    packages_result = execute_adb_command(f"adb -s {device_id} shell pm list packages -3")
    if not packages_result["success"]:
        return {"code": 500, "message": "获取应用列表失败", "data": None}
    
    packages = []
    for line in packages_result["stdout"].split('\n'):
        if line and line.startswith('package:'):
            pkg = line[8:].strip()
            if pkg:
                packages.append(pkg)
    
    # 批量获取应用信息
    apps_info = []
    for package_name in packages:
        try:
            # 从缓存获取 appName
            app_name = cache.get(package_name, package_name)
            
            # 快速获取版本信息（只提取关键行）
            version_result = execute_adb_command(
                f"adb -s {device_id} shell dumpsys package {package_name} | grep -E 'versionName=|versionCode='"
            )
            
            version_name = ""
            version_code = ""
            
            if version_result["success"] and version_result["stdout"]:
                for line in version_result["stdout"].split('\n'):
                    if 'versionName=' in line:
                        version_name = line.split('=', 1)[1].strip() if '=' in line else ""
                    elif 'versionCode=' in line:
                        parts = line.split('=', 1)
                        if len(parts) > 1:
                            version_code = parts[1].split()[0] if parts[1].strip() else ""
            
            apps_info.append({
                "packageName": package_name,
                "appName": app_name,
                "versionName": version_name,
                "versionCode": version_code
            })
            
        except Exception as e:
            # 单个应用失败不影响其他应用
            apps_info.append({
                "packageName": package_name,
                "appName": package_name,
                "versionName": "",
                "versionCode": "",
                "error": str(e)
            })
    
    return {
        "code": 200,
        "message": "success",
        "data": {
            "total": len(apps_info),
            "apps": apps_info
        }
    }


# 获取应用详细信息（名称、版本号等）
@router.post("/device/app/info")
def get_app_info(request: AppInfoRequest):
    """
    获取单个应用的详细信息
    
    优化策略：
    1. 优先从缓存获取 appName
    2. 如果缓存中没有，后台异步获取（先尝试快速方法，再尝试慢速方法）
    3. 立即返回基础信息，不阻塞用户
    
    Args:
        deviceId: 设备 ID
        packageName: 应用包名
        
    Returns:
        应用详细信息，包括 packageName, appName, versionName, versionCode
    """
    import time
    from utils.app_label_utils import (
        load_label_cache,
        background_fetch_app_name,
        extract_app_name_via_shell
    )
    
    start_time = time.time()
    logger.info(f"\n{'='*60}")
    logger.info(f"📱 [API] 获取应用信息")
    logger.info(f"📱 [API] 设备: {request.deviceId}")
    logger.info(f"📦 [API] 包名: {request.packageName}")
    logger.info(f"{'='*60}")
    
    # 1. 尝试从缓存获取 appName
    cache = load_label_cache()
    app_name = cache.get(request.packageName, None)
    
    if app_name:
        logger.info(f"✅ [API] 缓存命中: {request.packageName} -> {app_name}")
    else:
        logger.info(f"❌ [API] 缓存未命中: {request.packageName}")

    # 2. 解析基础信息（version 等）
    logger.info(f"\n🔍 [API] 执行 dumpsys package...")
    cmd_start = time.time()
    result = execute_adb_command(f"adb -s {request.deviceId} shell dumpsys package {request.packageName}")
    cmd_duration = time.time() - cmd_start
    
    if not result["success"]:
        logger.error(f"❌ [API] dumpsys 失败: {result.get('error', 'Unknown error')}")
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}
    
    logger.info(f"✅ [API] dumpsys 成功 (耗时: {cmd_duration:.2f}s)")

    info = {
        "packageName": request.packageName,
        "versionName": "",
        "versionCode": "",
        "appName": app_name if app_name else request.packageName  # 缓存中有就用，否则用包名
    }

    # 3. 从 dumpsys 提取 version 和 appName
    dumpsys_output = result["stdout"]
    for line in dumpsys_output.split('\n'):
        trimmed = line.strip()
        if trimmed.startswith('versionName='):
            info["versionName"] = trimmed.split('=', 1)[1] if '=' in trimmed else ""
        elif trimmed.startswith('versionCode='):
            parts = trimmed.split('=', 1)
            if len(parts) > 1:
                info["versionCode"] = parts[1].split()[0] if parts[1].strip() else ""
    
    logger.info(f"📊 [API] 版本信息: {info['versionName']} ({info['versionCode']})")
    
    # 4. 如果缓存中没有 appName，尝试从 dumpsys 输出中快速提取
    if not app_name:
        logger.info(f"\n🔍 [API] 尝试从 dumpsys 输出中提取应用名...")
        from utils.app_label_utils import extract_app_name_from_dumpsys
        quick_name = extract_app_name_from_dumpsys(dumpsys_output)
        
        if quick_name:
            info["appName"] = quick_name
            # 更新缓存
            cache[request.packageName] = quick_name
            from utils.app_label_utils import save_label_cache
            save_label_cache(cache)
            logger.info(f"✅ [API] 快速提取成功并缓存: {request.packageName} -> {quick_name}")
        else:
            # 快速提取失败，启动后台线程去获取（多层次策略）
            logger.info(f"⚠️ [API] 快速提取失败，启动后台任务...")
            import threading
            thread = threading.Thread(
                target=background_fetch_app_name,
                args=(execute_adb_command, request.deviceId, request.packageName),
                daemon=True
            )
            thread.start()
            logger.info(f"🔄 [API] 后台任务已启动（不会阻塞当前响应）")
    else:
        logger.info(f"✅ [API] 使用缓存中的应用名")

    total_duration = time.time() - start_time
    logger.info(f"\n✅ [API] 响应完成 (总耗时: {total_duration:.2f}s)")
    logger.info(f"📝 [API] 返回数据: packageName={info['packageName']}, appName={info['appName']}")
    logger.info(f"{'='*60}\n")

    return {"code": 200, "message": "success", "data": info}


# 安装应用（支持文件上传）
@router.post("/device/app/install")
async def install_app(
    deviceId: str = Form(..., description="设备ID"),
    apk: UploadFile = File(..., description="APK文件")
):
    import tempfile
    
    # 创建临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".apk") as tmp_file:
        content = await apk.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name
    
    try:
        result = await asyncio.to_thread(execute_adb_command, f'adb -s {deviceId} install -r "{tmp_path}"', timeout=600)
        if result["success"]:
            return {"code": 200, "message": "success", "data": None}
        else:
            return {"code": 500, "message": result.get("error", "Install failed"), "data": None}
    finally:
        # 删除临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# 批量安装应用（多台设备安装同一个 APK，入队异步执行）
@router.post("/device/app/install/batch")
async def install_app_batch(
    deviceIds: str = Form(..., description="设备ID，多个用逗号分隔"),
    apk: UploadFile = File(..., description="APK文件"),
):
    # 校验设备列表
    device_id_list = [did.strip() for did in deviceIds.split(",") if did.strip()]
    if not device_id_list:
        return {"code": 400, "message": "deviceId 列表不能为空", "data": None}

    # 保存 APK 到临时文件（worker 执行时读取，由系统定期清理）
    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".apk",
        prefix=f"batch_install_{uuid.uuid4().hex}_",
    ) as tmp_file:
        content = await apk.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name

    # 逐台设备入队，改用 task_queue.submit_task
    for did in device_id_list:
        task_queue.submit_task(
            execute_adb_command,
            f'adb -s {did} install -r "{tmp_path}"',
            timeout=600,
        )

    return {"code": 200, "message": "批量安装命令已发送", "data": None}

# 卸载应用
@router.post("/device/app/uninstall")
def uninstall_app(request: PackageRequest):
    if not request.packageName:
        return {"code": 400, "message": "Package name is required", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} uninstall {request.packageName}")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Uninstall failed"), "data": None}


# 启动应用
@router.post("/device/app/start")
def start_app(request: PackageRequest):
    if not request.packageName:
        return {"code": 400, "message": "Package name is required", "data": None}
    
    if request.activity:
        cmd = f"adb -s {request.deviceId} shell am start -n {request.packageName}/{request.activity}"
    else:
        cmd = f"adb -s {request.deviceId} shell monkey -p {request.packageName} -c android.intent.category.LAUNCHER 1"
    
    result = execute_adb_command(cmd)
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Start failed"), "data": None}


# 停止应用
@router.post("/device/app/stop")
def stop_app(request: PackageRequest):
    if not request.packageName:
        return {"code": 400, "message": "Package name is required", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} shell am force-stop {request.packageName}")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Stop failed"), "data": None}


# 提取APK
@router.post("/device/app/extract")
def extract_apk(request: ExtractRequest):
    if not request.packageName or not request.localPath:
        return {"code": 400, "message": "Package name and local path are required", "data": None}
    
    # 获取APK路径
    path_result = execute_adb_command(f"adb -s {request.deviceId} shell pm path {request.packageName}")
    if not path_result["success"]:
        return {"code": 500, "message": path_result.get("error", "Failed to get APK path"), "data": None}
    
    apk_path = path_result["stdout"].strip().replace('package:', '')
    if not apk_path:
        return {"code": 500, "message": "Failed to get APK path", "data": None}
    
    # 确保本地路径存在
    local_dir = os.path.dirname(request.localPath)
    if local_dir and not os.path.exists(local_dir):
        try:
            os.makedirs(local_dir, exist_ok=True)
        except Exception as e:
            return {"code": 500, "message": f"Failed to create directory: {str(e)}", "data": None}
    
    # 提取APK
    result = execute_adb_command(f'adb -s {request.deviceId} pull "{apk_path}" "{request.localPath}"')
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Extract failed"), "data": None}


# 获取应用详细信息
@router.post("/device/app/details")
def get_app_details(request: AppInfoRequest):
    result = execute_adb_command(f"adb -s {request.deviceId} shell dumpsys package {request.packageName}")
    if result["success"]:
        info = {}
        lines = result["stdout"].split('\n')
        
        for line in lines:
            if 'versionName=' in line:
                info["versionName"] = line.split('versionName=')[1].strip()
            elif 'versionCode=' in line:
                info["versionCode"] = line.split('versionCode=')[1].strip()
            elif 'applicationInfo=' in line:
                info["applicationInfo"] = line.split('applicationInfo=')[1].strip()
        
        return {"code": 200, "message": "success", "data": info}
    else:
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}


# 浏览文件系统
@router.post("/device/files/browse")
def browse_files(request: PathRequest):
    # 构建实际的路径，默认在/sdcard目录下
    actual_path = '/sdcard' if request.path == '/' else request.path
    
    # 添加末尾斜杠确保获取目录内容
    command_path = actual_path if actual_path.endswith('/') else actual_path + '/'
    
    result = execute_adb_command(f'adb -s {request.deviceId} shell ls -la "{command_path}"')
    if result and result.get("success"):
        files = []
        stdout = result.get("stdout", "") or ""
        lines = stdout.split('\n')
        
        # 处理每一行输出
        for i, line in enumerate(lines):
            line = line.strip()
            
            if line:
                # 跳过第一行（总文件数信息）
                if i == 0 and 'total' in line:
                    continue
                
                # 解析 ls -la 输出格式
                parts = line.split()
                
                if len(parts) >= 8:
                    permissions = parts[0]
                    is_directory = permissions.startswith('d')
                    size = '-' if is_directory else parts[4]
                    # 文件名可能从第7个部分开始（因为日期时间可能包含空格）
                    name = ' '.join(parts[7:])
                    
                    # 跳过 . 和 .. 两个目录自引用项：前端把返回的 path 直接作为下次浏览路径，
                    # 进入 . 会让路径不断累积成 /xxx/././.
                    if name in ('.', '..'):
                        continue
                    
                    # 构建完整路径
                    file_path = f"{actual_path}/{name}" if not actual_path.endswith('/') else f"{actual_path}{name}"
                    
                    files.append({
                        "name": name,
                        "path": file_path,
                        "isDirectory": is_directory,
                        "size": size,
                        "permissions": permissions
                    })
        
        return {"code": 200, "message": "success", "data": {"files": files, "currentPath": actual_path}}
    else:
        return {"code": 500, "message": result.get("error", "Unknown error"), "data": None}


# 读取设备上的文本文件
@router.post("/device/files/read")
def read_file(request: FileReadRequest):
    """
    读取设备上的文本文件全文

    经 adb shell cat 读取。设备无此接口能力的旧版前端按 code 404 判定回落，
    本接口正常实现；仅当文件不存在或读取失败时返回 code 500。
    """
    if not request.remotePath:
        return {"code": 400, "message": "remotePath is required", "data": None}

    result = execute_adb_command(f"adb -s {request.deviceId} shell cat '{request.remotePath}'")
    if not result["success"]:
        error_msg = (result.get("error") or "Read file failed").strip()
        return {"code": 500, "message": error_msg, "data": None}

    # 部分 adb 版本的 pty 会把 \n 转成 \r\n，统一回 \n；内容为空则可能是二进制文件
    content = (result.get("stdout") or "").replace("\r\n", "\n")
    return {
        "code": 200,
        "message": "success",
        "data": {"path": request.remotePath, "content": content},
    }


# 创建文件夹
@router.post("/device/files/mkdir")
def create_directory(request: PathRequest):
    if not request.path:
        return {"code": 400, "message": "Path is required", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} shell mkdir -p {request.path}")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Mkdir failed"), "data": None}


# 创建空文件
@router.post("/device/files/create")
def create_file(request: PathRequest):
    if not request.path:
        return {"code": 400, "message": "Path is required", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} shell touch '{request.path}'")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Create file failed"), "data": None}


# 删除文件或文件夹
@router.post("/device/files/delete")
def delete_file(request: PathRequest):
    if not request.path:
        return {"code": 400, "message": "Path is required", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} shell rm -rf {request.path}")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Delete failed"), "data": None}


# 上传文件
@router.post("/device/files/upload")
async def upload_file(
    deviceId: str = Form(..., description="设备ID"),
    file: UploadFile = File(..., description="要上传的文件"),
    remotePath: str = Form("/sdcard/", description="远程路径，默认为/sdcard/")
):
    import tempfile
    
    # 创建临时文件
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name
    
    try:
        # 构建目标路径
        dest_path = f"{remotePath}/{file.filename}" if not remotePath.endswith('/') else f"{remotePath}{file.filename}"
        
        result = execute_adb_command(f'adb -s {deviceId} push "{tmp_path}" "{dest_path}"')
        if result["success"]:
            return {"code": 200, "message": "success", "data": None}
        else:
            return {"code": 500, "message": result.get("error", "Upload failed"), "data": None}
    finally:
        # 删除临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# 下载文件
@router.post("/device/files/download")
def download_file(request: FileDownloadRequest):
    if not request.remotePath or not request.localPath:
        return {"code": 400, "message": "Remote path and local path are required", "data": None}
    
    # 确保本地路径存在
    if not os.path.exists(request.localPath):
        try:
            base_name = os.path.basename(request.localPath)
            has_extension = bool(re.search(r'\.[^./\\]+$', base_name))
            
            is_dir = request.localPath.endswith('/') or request.localPath.endswith('\\') or not has_extension
            
            if is_dir:
                os.makedirs(request.localPath, exist_ok=True)
            else:
                local_dir = os.path.dirname(request.localPath)
                if local_dir:
                    os.makedirs(local_dir, exist_ok=True)
        except Exception as e:
            return {"code": 500, "message": f"Failed to create directory: {str(e)}", "data": None}
    
    # 执行 adb pull 命令
    result = execute_adb_command(f'adb -s {request.deviceId} pull "{request.remotePath}" "{request.localPath}"')
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Download failed"), "data": None}


# 按键模拟
@router.post("/device/keyevent")
def key_event(request: KeycodeRequest):
    if not request.keycode:
        return {"code": 400, "message": "Keycode is required", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} shell input keyevent {request.keycode}")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Keyevent failed"), "data": None}


# 基本按键模拟
@router.post("/device/key")
def basic_key(request: BasicKeyRequest):
    # 按键映射
    key_map = {
        'power': 'KEYCODE_POWER',
        'volume_up': 'KEYCODE_VOLUME_UP',
        'volume_down': 'KEYCODE_VOLUME_DOWN',
        'back': 'KEYCODE_BACK',
        'home': 'KEYCODE_HOME',
        'menu': 'KEYCODE_MENU',
        'recent': 'KEYCODE_APP_SWITCH',
        'camera': 'KEYCODE_CAMERA',
        'search': 'KEYCODE_SEARCH'
    }
    
    keycode = key_map.get(request.key)
    if not keycode:
        return {"code": 400, "message": "Invalid key", "data": None}
    
    result = execute_adb_command(f"adb -s {request.deviceId} shell input keyevent {keycode}")
    if result["success"]:
        return {"code": 200, "message": "success", "data": None}
    else:
        return {"code": 500, "message": result.get("error", "Key press failed"), "data": None}


# 启动投屏
@router.post("/device/scrcpy")
def start_scrcpy(request: DeviceIdRequest):
    """
    启动 Scrcpy 投屏
    
    使用内置的 scrcpy 工具，自动获取其完整路径。
    """
    try:
        scrcpy_path = get_scrcpy_path()
        # 使用完整路径启动 scrcpy
        subprocess.Popen(f'"{scrcpy_path}" -s {request.deviceId}', shell=True)
        return {"code": 200, "message": "success", "data": None}
    except FileNotFoundError as e:
        return {"code": 500, "message": f"启动投屏失败: {str(e)}", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"启动投屏失败: {str(e)}", "data": None}


# 打开设备运行日志窗口
@router.post("/device/logs")
def open_logs(request: DeviceIdRequest):
    """
    获取当前正在运行的前台应用包名和 PID
    """
    try:
        device_id = request.deviceId
        
        # 1. 获取当前前台应用包名
        result = execute_adb_command(
            f"adb -s {device_id} shell dumpsys window"
        )
        package_name = None
        if result["success"] and result["stdout"]:
            logger.info(f"dumpsys window output length: {len(result['stdout'])}")
            # 在 Python 中过滤包含 mCurrentFocus 或 mFocusedApp 的行
            for line in result["stdout"].split('\n'):
                if 'mCurrentFocus' in line or 'mFocusedApp' in line:
                    logger.info(f"Found focus line: {line.strip()}")
                    # 提取包名部分（格式：u0 com.xxx.xxx/com.xxx.xxx.Activity）
                    match = re.search(r'u0\s+([^/\s]+)', line)
                    if match:
                        package_name = match.group(1).strip()
                        # 清理特殊字符
                        package_name = package_name.replace('{', '').replace('}', '').strip()
                        logger.info(f"Extracted package name: {package_name}")
                        break
        
        # 如果第一种方法失败，尝试备用方法
        if not package_name:
            logger.info("Trying alternative method: dumpsys activity top")
            alt_result = execute_adb_command(
                f"adb -s {device_id} shell dumpsys activity top"
            )
            if alt_result["success"] and alt_result["stdout"]:
                for line in alt_result["stdout"].split('\n'):
                    if 'Run #0' in line or 'ACTIVITY' in line:
                        # 匹配类似：Run #0: ActivityRecord{... u0 com.xxx.xxx/.Activity ...}
                        match = re.search(r'u0\s+([^/\s]+)', line)
                        if match:
                            package_name = match.group(1).strip()
                            package_name = package_name.replace('{', '').replace('}', '').strip()
                            logger.info(f"Alternative method extracted: {package_name}")
                            break
        
        logger.info(f"Current package name: {package_name}")
        
        # 2. 如果获取到包名，获取 PID
        pid = None
        if package_name:
            pid_result = execute_adb_command(
                f"adb -s {device_id} shell pidof {package_name}"
            )
            if pid_result["success"] and pid_result["stdout"].strip():
                # pidof 可能返回多个 PID，取第一个
                pids = pid_result["stdout"].strip().split()
                if pids:
                    pid = int(pids[0])
        logger.info(f"Current PID: {pid}")
        # 3. 打开 CMD 窗口执行 logcat
        if pid:
            logger.info(f"Opening logcat for {device_id}-{package_name} (PID: {pid})")
            cmd = f'start cmd.exe /k "title {device_id}-{package_name} Logs (PID: {pid}) & adb -s {device_id} logcat --pid={pid}"'
        else:
            logger.info(f"Opening logcat for {device_id}")
            cmd = f'start cmd.exe /k "title Device Logs & adb -s {device_id} logcat"'
        
        subprocess.Popen(cmd, shell=True)
        
        return {
            "code": 200,
            "message": "日志窗口已打开",
            "data": {
                "packageName": package_name,
                "pid": pid
            }
        }
    except Exception as e:
        return {"code": 500, "message": f"获取失败: {str(e)}", "data": None}


# 打开设备 shell 窗口
@router.post("/device/shell")
def open_shell(request: DeviceIdRequest):
    try:
        # Windows 下在新 cmd 窗口中运行 shell 命令
        subprocess.Popen(f'start cmd.exe /k "adb -s {request.deviceId} shell"', shell=True)
        return {"code": 200, "message": "success", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"启动 shell 窗口失败: {str(e)}", "data": None}


# 音量流类型映射
def get_stream_type(stream: str) -> str:
    """将音频流类型映射为 Android stream type"""
    stream_map = {
        'media': '3',      # STREAM_MUSIC
        'ring': '2',       # STREAM_RING
        'alarm': '4',      # STREAM_ALARM
        'notification': '5'  # STREAM_NOTIFICATION
    }
    return stream_map.get(stream, '3')


# 获取音量状态
@router.post("/device/volume/status")
async def get_volume_status(request: VolumeStatusRequest):
    """获取设备当前音量状态"""
    try:
        device_id = request.deviceId
        stream_type = get_stream_type(request.stream)
        
        # 获取最大音量
        max_vol_result = execute_adb_command(
            f"adb -s {device_id} shell settings get system volume_{request.stream}_max"
        )
        
        # 如果上面的命令失败，尝试另一种方式
        if not max_vol_result["success"] or not max_vol_result["stdout"].strip():
            max_vol_result = execute_adb_command(
                f"adb -s {device_id} shell dumpsys audio | findstr \"{request.stream}\" | findstr \"max\""
            )
        
        # 获取当前音量
        curr_vol_result = execute_adb_command(
            f"adb -s {device_id} shell settings get system volume_{request.stream}"
        )
        
        # 检查是否静音
        mute_result = execute_adb_command(
            f"adb -s {device_id} shell settings get system mute_streams"
        )
        
        # 解析最大音量
        max_volume = 15  # 默认值
        if max_vol_result["success"] and max_vol_result["stdout"].strip():
            try:
                max_volume = int(max_vol_result["stdout"].strip())
            except:
                pass
        
        # 解析当前音量
        current_volume = 0
        if curr_vol_result["success"] and curr_vol_result["stdout"].strip():
            try:
                current_volume = int(curr_vol_result["stdout"].strip())
            except:
                pass
        
        # 判断是否静音
        is_muted = False
        if mute_result["success"] and mute_result["stdout"].strip():
            try:
                mute_streams = int(mute_result["stdout"].strip())
                stream_bit = 1 << int(stream_type)
                is_muted = bool(mute_streams & stream_bit)
            except:
                pass
        
        return {
            "code": 200,
            "message": "获取成功",
            "data": {
                "currentVolume": current_volume,
                "maxVolume": max_volume,
                "isMuted": is_muted,
                "stream": request.stream
            }
        }
    except Exception as e:
        logger.error(f"获取音量状态失败: {str(e)}")
        return {"code": 500, "message": f"获取音量状态失败: {str(e)}", "data": None}


# 控制音量
@router.post("/device/volume")
async def control_volume(request: VolumeControlRequest):
    """控制设备音量（增加/减小/静音/取消静音）"""
    try:
        device_id = request.deviceId
        action = request.action.lower()
        stream_type = get_stream_type(request.stream)
        
        # 定义按键映射
        key_map = {
            'up': 'KEYCODE_VOLUME_UP',
            'down': 'KEYCODE_VOLUME_DOWN',
            'mute': None,  # 特殊处理
            'unmute': None  # 特殊处理
        }
        
        if action in ['up', 'down']:
            # 音量加减
            keycode = key_map[action]
            result = execute_adb_command(
                f"adb -s {device_id} shell input keyevent {keycode}"
            )
            
            if not result["success"]:
                return {"code": 500, "message": f"音量{action}操作失败", "data": None}
        
        elif action == 'mute':
            # 静音：多种方式尝试
            success = False
            
            # 方法1: 使用 service call 直接调用音频服务（需要root或系统权限）
            result = execute_adb_command(
                f"adb -s {device_id} shell service call audio {7 + int(stream_type)} i32 1"
            )
            if result["success"]:
                success = True
            
            # 方法2: 使用 settings 设置静音标志
            if not success:
                # 先获取当前的 mute_streams 值
                current_mute = execute_adb_command(
                    f"adb -s {device_id} shell settings get system mute_streams"
                )
                
                current_mute_value = 0
                if current_mute["success"] and current_mute["stdout"].strip():
                    try:
                        current_mute_value = int(current_mute["stdout"].strip())
                    except:
                        pass
                
                # 设置对应流的静音位
                new_mute_value = current_mute_value | (1 << int(stream_type))
                result = execute_adb_command(
                    f"adb -s {device_id} shell settings put system mute_streams {new_mute_value}"
                )
                if result["success"]:
                    success = True
            
            # 方法3: 将音量设置为0
            if not success:
                result = execute_adb_command(
                    f"adb -s {device_id} shell settings put system volume_{request.stream} 0"
                )
                if result["success"]:
                    success = True
            
            if not success:
                return {"code": 500, "message": "静音操作失败，请确保设备已授权", "data": None}
        
        elif action == 'unmute':
            # 取消静音：多种方式尝试
            success = False
            
            # 方法1: 使用 service call 直接调用音频服务
            result = execute_adb_command(
                f"adb -s {device_id} shell service call audio {7 + int(stream_type)} i32 0"
            )
            if result["success"]:
                success = True
            
            # 方法2: 清除 settings 中的静音标志
            if not success:
                # 先获取当前的 mute_streams 值
                current_mute = execute_adb_command(
                    f"adb -s {device_id} shell settings get system mute_streams"
                )
                
                current_mute_value = 0
                if current_mute["success"] and current_mute["stdout"].strip():
                    try:
                        current_mute_value = int(current_mute["stdout"].strip())
                    except:
                        pass
                
                # 清除对应流的静音位
                new_mute_value = current_mute_value & ~(1 << int(stream_type))
                result = execute_adb_command(
                    f"adb -s {device_id} shell settings put system mute_streams {new_mute_value}"
                )
                if result["success"]:
                    success = True
            
            # 方法3: 恢复音量到默认值（如果之前被设为0）
            if not success:
                # 获取当前音量，如果为0则设置为一个合理值
                curr_vol = execute_adb_command(
                    f"adb -s {device_id} shell settings get system volume_{request.stream}"
                )
                
                current_volume = 0
                if curr_vol["success"] and curr_vol["stdout"].strip():
                    try:
                        current_volume = int(curr_vol["stdout"].strip())
                    except:
                        pass
                
                if current_volume == 0:
                    # 设置为最大音量的一半
                    result = execute_adb_command(
                        f"adb -s {device_id} shell settings put system volume_{request.stream} 7"
                    )
                    if result["success"]:
                        success = True
            
            if not success:
                return {"code": 500, "message": "取消静音操作失败，请确保设备已授权", "data": None}
        
        else:
            return {"code": 400, "message": f"不支持的操作: {action}，支持的操作: up, down, mute, unmute", "data": None}
        
        # 获取操作后的音量状态
        status_result = await get_volume_status(VolumeStatusRequest(deviceId=device_id, stream=request.stream))
        
        if status_result["code"] == 200:
            return {
                "code": 200,
                "message": "操作成功",
                "data": status_result["data"]
            }
        else:
            return {
                "code": 200,
                "message": "操作成功",
                "data": None
            }
    
    except Exception as e:
        logger.error(f"音量控制失败: {str(e)}")
        return {"code": 500, "message": f"音量控制失败: {str(e)}", "data": None}

# 批量重启设备（异步队列模式，不等待结果）
@router.post("/device/reboot/batch")
async def reboot_devices_batch(request: DeviceIdsRequest):
    """批量发送重启命令，不等待结果"""
    if not request.deviceIds:
        return {"code": 400, "message": "deviceId 列表不能为空", "data": None}

    for did in request.deviceIds:
        task_queue.submit_task(execute_adb_command, f"adb -s {did} reboot")

    return {"code": 200, "message": "重启命令已发送", "data": None}


# 批量执行 Shell 命令（同步返回结果）
@router.post("/device/shell/batch")
async def batch_shell_command(request: DeviceCommandsRequest):
    """批量给设备执行 shell 命令，返回每台设备的 id 和响应"""
    if not request.deviceIds:
        return {"code": 400, "message": "deviceId 列表不能为空", "data": None}
    if not request.command or not request.command.strip():
        return {"code": 400, "message": "command 不能为空", "data": None}

    shell_cmd = f"adb -s {{}} shell {request.command}"

    def exec_on_device(device_id: str) -> dict:
        result = execute_adb_command(shell_cmd.format(device_id))
        output = result.get("stdout", "")
        error = result.get("error", "")
        return {
            "deviceId": device_id,
            "success": result["success"],
            "stdout": output.strip() if output else "",
            "stderr": error.strip() if error else "",
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(request.deviceIds)) as executor:
        futures = {executor.submit(exec_on_device, did): did for did in request.deviceIds}
        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                did = futures[future]
                logger.error(f"Batch shell failed for {did}: {e}")
                results.append({"deviceId": did, "success": False, "stdout": "", "stderr": str(e)})

    success_count = sum(1 for r in results if r["success"])
    return {
        "code": 200,
        "message": "批量执行完成",
        "data": {
            "total": len(results),
            "successCount": success_count,
            "failedCount": len(results) - success_count,
            "results": results,
        },
    }


# ── 设备性能监控 ──────────────────────────────────────────────────────

def _split_sections(output: str) -> dict:
    """
    按 @@@ 标记把合并输出切成 {标记: 文本} 字典

    一次 adb shell 内用 echo 输出多个分组标记，避免多次往返。
    """
    sections = {}
    current = None
    buffer = []

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith('@@@'):
            if current:
                sections[current] = '\n'.join(buffer)
            current = stripped
            buffer = []
        elif current:
            buffer.append(line)

    if current:
        sections[current] = '\n'.join(buffer)

    return sections


def _parse_meminfo(output: str) -> dict:
    """解析 /proc/meminfo，返回 {字段名: 数值(kB)} 字典"""
    info = {}
    for line in output.splitlines():
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        parts = value.split()
        if parts and parts[0].isdigit():
            info[key.strip()] = int(parts[0])
    return info


def _parse_cpu_stat(line: str) -> Optional[dict]:
    """
    解析 /proc/stat 的 cpu 汇总行

    字段顺序：user nice system idle iowait irq softirq steal guest guest_nice
    部分内核无 guest/guest_nice 字段，缺失时补 0。
    """
    parts = line.split()
    if len(parts) < 6 or parts[0] != 'cpu':
        return None

    try:
        values = [int(v) for v in parts[1:11]]
    except ValueError:
        return None

    values += [0] * (10 - len(values))
    keys = ["user", "nice", "system", "idle", "iowait",
            "irq", "softirq", "steal", "guest", "guestNice"]
    return dict(zip(keys, values))


def _cpu_times(stats: dict) -> tuple:
    """
    由 /proc/stat 累计值算出 (busy, total) 两个 jiffies

    guest/guestNice 已计入 user/nice，不能重复累加。
    busy 不含 idle 与 iowait。
    """
    idle_all = stats["idle"] + stats["iowait"]
    total = (stats["user"] + stats["nice"] + stats["system"] + stats["idle"]
             + stats["iowait"] + stats["irq"] + stats["softirq"] + stats["steal"])
    return total - idle_all, total


def _parse_net_dev(output: str) -> dict:
    """
    解析 /proc/net/dev，返回 {网卡名: {"rxBytes": int, "txBytes": int}}

    跳过回环 lo 与虚拟网卡 dummy0。
    """
    interfaces = {}
    for line in output.splitlines():
        if ':' not in line:
            continue

        name, _, rest = line.partition(':')
        name = name.strip()
        if not name or name in ('lo', 'dummy0'):
            continue

        parts = rest.split()
        if len(parts) < 9:
            continue

        try:
            interfaces[name] = {"rxBytes": int(parts[0]), "txBytes": int(parts[8])}
        except ValueError:
            continue

    return interfaces


def _parse_df_data(output: str) -> Optional[dict]:
    """
    从 df -k 输出中取出挂载点为 /data 的分区

    只读分区（/、/vendor、/system_ext 等）常显示 100% 占用，属于正常现象，
    不能作为磁盘使用指标，因此只认 /data。
    """
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[-1] != '/data':
            continue

        try:
            total = int(parts[1])
            used = int(parts[2])
            available = int(parts[3])
        except ValueError:
            continue

        return {
            "mountPoint": "/data",
            "totalBytes": total * 1024,
            "usedBytes": used * 1024,
            "availableBytes": available * 1024,
            "usagePercent": round(used * 100 / total, 2) if total else 0,
        }

    return None


def _parse_cpu_freq(output: str) -> list:
    """
    解析 grep -H 输出的每核频率

    输出形如：/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq:1416000
    注意：cpuinfo_cur_freq（硬件真实频率）需 root，无权限时不可读，
    此处使用 scaling_cur_freq（调频策略当前值），两者语义不同。
    """
    freqs = []
    for line in output.splitlines():
        if ':' not in line:
            continue

        path, _, value = line.rpartition(':')
        value = value.strip()
        if not value.isdigit():
            continue

        match = re.search(r'cpu(\d+)/cpufreq', path)
        core = int(match.group(1)) if match else len(freqs)
        freqs.append({"core": core, "currentKhz": int(value)})

    freqs.sort(key=lambda x: x["core"])
    return freqs


# 获取设备性能快照（内存、CPU 频率、磁盘使用）
@router.post("/device/monitor")
def get_device_monitor(request: MonitorRequest):
    """
    获取设备性能快照：内存占用、CPU 频率、磁盘使用

    单次 adb 往返取回全部指标（实测约 0.2 秒），无采样等待。
    CPU 占用率与网络速率需要两次采样求差值，见 /device/monitor/rate。
    """
    device_id = request.deviceId
    logger.info(f"Getting performance monitor for device: {device_id}")

    command = (
        "echo @@@MEM; head -5 /proc/meminfo; "
        "echo @@@FREQ; grep -H . /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; "
        "echo @@@MAXFREQ; cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq; "
        "echo @@@DISK; df -k"
    )

    result = execute_adb_command(f'adb -s {device_id} shell "{command}"')
    if not result["success"]:
        error_msg = (result.get("error") or "获取性能数据失败").strip()
        logger.error(f"Monitor failed for {device_id}: {error_msg}")
        return {"code": 500, "message": error_msg, "data": None}

    sections = _split_sections(result["stdout"])

    # 内存
    meminfo = _parse_meminfo(sections.get("@@@MEM", ""))
    mem_total = meminfo.get("MemTotal", 0)
    mem_available = meminfo.get("MemAvailable", 0)
    memory = {
        "totalBytes": mem_total * 1024,
        "usedBytes": (mem_total - mem_available) * 1024,
        "availableBytes": mem_available * 1024,
        "freeBytes": meminfo.get("MemFree", 0) * 1024,
        "cachedBytes": meminfo.get("Cached", 0) * 1024,
        "usagePercent": round((mem_total - mem_available) * 100 / mem_total, 2) if mem_total else 0,
    }

    # CPU 频率
    cores = _parse_cpu_freq(sections.get("@@@FREQ", ""))
    max_freq_raw = sections.get("@@@MAXFREQ", "").strip()
    cpu = {
        "coreCount": len(cores),
        "maxFreqKhz": int(max_freq_raw) if max_freq_raw.isdigit() else None,
        "cores": cores,
    }

    logger.info(f"Monitor done for {device_id}: {len(cores)} core(s)")

    return {
        "code": 200,
        "message": "success",
        "data": {
            "cpu": cpu,
            "memory": memory,
            "disk": _parse_df_data(sections.get("@@@DISK", "")),
        },
    }


# 获取设备实时速率（CPU 占用率、网络上下行速率）
@router.post("/device/monitor/rate")
def get_device_monitor_rate(request: MonitorRateRequest):
    """
    获取设备实时速率：CPU 占用率、网络上传/下载速率

    采用两次采样求差值（/proc/stat 与 /proc/net/dev 均为开机累计值，
    单次读取只能得到总量，无法得到占用率与速率）。

    采样在设备端 sleep，一次 adb 往返完成，避免两次往返之间被其他请求
    插队导致采样区间失真。
    """
    device_id = request.deviceId
    duration = request.duration if request.duration is not None else 1

    if not isinstance(duration, int) or duration < 1 or duration > 5:
        return {"code": 400, "message": "duration 必须在 1-5 秒范围内", "data": None}

    logger.info(f"Getting performance rate for device: {device_id}, duration={duration}s")

    command = (
        "echo @@@S1; head -1 /proc/stat; "
        "echo @@@N1; cat /proc/net/dev; "
        f"sleep {duration}; "
        "echo @@@S2; head -1 /proc/stat; "
        "echo @@@N2; cat /proc/net/dev"
    )

    result = execute_adb_command(
        f'adb -s {device_id} shell "{command}"',
        timeout=duration + 5,
    )
    if not result["success"]:
        error_msg = (result.get("error") or "获取速率数据失败").strip()
        logger.error(f"Monitor rate failed for {device_id}: {error_msg}")
        return {"code": 500, "message": error_msg, "data": None}

    sections = _split_sections(result["stdout"])

    # CPU 占用率
    stat1 = _parse_cpu_stat(sections.get("@@@S1", ""))
    stat2 = _parse_cpu_stat(sections.get("@@@S2", ""))
    if not stat1 or not stat2:
        logger.error(f"Failed to parse /proc/stat for {device_id}")
        return {"code": 500, "message": "读取 CPU 统计数据失败", "data": None}

    busy1, total1 = _cpu_times(stat1)
    busy2, total2 = _cpu_times(stat2)
    total_delta = total2 - total1
    cpu_usage = round((busy2 - busy1) * 100 / total_delta, 2) if total_delta > 0 else 0

    # 网络速率（计数器在网卡重启后会归零，用 max(...,0) 兜底避免出现负速率）
    net1 = _parse_net_dev(sections.get("@@@N1", ""))
    net2 = _parse_net_dev(sections.get("@@@N2", ""))

    interfaces = []
    for name, current in net2.items():
        previous = net1.get(name)
        if not previous:
            continue

        # 开机至今收发均为 0 的网卡（虚拟隧道口、未启用的 eth0 等）不参与上报
        if current["rxBytes"] == 0 and current["txBytes"] == 0:
            continue

        rx_delta = max(current["rxBytes"] - previous["rxBytes"], 0)
        tx_delta = max(current["txBytes"] - previous["txBytes"], 0)
        interfaces.append({
            "name": name,
            "rxBytes": current["rxBytes"],
            "txBytes": current["txBytes"],
            "rxBytesPerSec": round(rx_delta / duration, 2),
            "txBytesPerSec": round(tx_delta / duration, 2),
        })

    interfaces.sort(key=lambda x: x["name"])

    logger.info(f"Monitor rate done for {device_id}: cpu={cpu_usage}%, {len(interfaces)} interface(s)")

    return {
        "code": 200,
        "message": "success",
        "data": {
            "duration": duration,
            "cpu": {"usagePercent": cpu_usage},
            "network": interfaces,
        },
    }