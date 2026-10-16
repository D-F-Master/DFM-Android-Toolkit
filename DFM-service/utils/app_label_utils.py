import os
import sys
import json
import threading
import tempfile
import subprocess
import re
import time
from pathlib import Path
from typing import Optional
from utils.tool_manager import get_aapt_path, get_base_path  # 导入工具管理器


# 配置：数据缓存目录
# 优先使用 _internal/data，如果不存在则在 exe 同目录创建
def get_data_dir() -> Path:
    """
    获取 data 目录路径
    
    Returns:
        Path: data 目录路径
    """
    base_path = get_base_path()
    
    # 尝试多个可能的位置（按优先级）
    possible_paths = [
        base_path / "_internal" / "data",            # 1. _internal/data (PyInstaller onedir)
        base_path / "data",                          # 2. exe 同级的 data 目录
        base_path.parent / "_internal" / "data",     # 3. 父目录的 _internal/data
        base_path.parent / "data",                   # 4. 父目录的 data
    ]
    
    for data_path in possible_paths:
        if data_path.exists():
            return data_path
    
    # 如果都没找到，在 exe 同级创建 data 目录
    data_dir = base_path / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir

DATA_DIR = get_data_dir()
LABEL_CACHE_FILE = DATA_DIR / "app_labels.json"

# 线程锁，防止并发写入冲突
cache_lock = threading.Lock()


def load_label_cache() -> dict:
    """加载应用标签缓存"""
    if LABEL_CACHE_FILE.exists():
        try:
            with open(LABEL_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_label_cache(cache_dict: dict):
    """安全保存缓存到文件"""
    with cache_lock:
        with open(LABEL_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_dict, f, ensure_ascii=False, indent=2)


def extract_app_name_from_dumpsys(dumpsys_output: str) -> Optional[str]:
    """
    从 dumpsys package 输出中提取应用名称
    
    尝试多种模式匹配：
    1. application-label='xxx'
    2. ApplicationLabel{ xxx }
    3. nonDataLabels=[xxx]
    
    Args:
        dumpsys_output: dumpsys package 命令的输出
        
    Returns:
        str | None: 应用名称，未找到返回 None
    """
    if not dumpsys_output:
        return None
    
    lines = dumpsys_output.split('\n')
    
    for line in lines:
        line_stripped = line.strip()
            
        # 模式 1: application-label:'微信' 或 application-label:"微信"
        if line_stripped.startswith('application-label:'):
            # 匹配单引号或双引号包裹的内容
            match = re.search(r"application-label:['\"]([^'\"]+)['\"]", line_stripped)
            if match:
                name = match.group(1)
                return name
        
        # 模式 2: ApplicationLabel{ 微信 }
        if 'ApplicationLabel{' in line_stripped:
            match = re.search(r'ApplicationLabel\{\s*(.+?)\s*\}', line_stripped)
            if match:
                name = match.group(1).strip()
                if name and name != 'null':
                    return name
        
        # 模式 3: nonDataLabels=[微信]
        if 'nonDataLabels=[' in line_stripped:
            match = re.search(r'nonDataLabels=\[(.+?)\]', line_stripped)
            if match:
                name = match.group(1).strip()
                if name and name != 'null':
                    return name
    
    return None


def extract_app_name_via_shell(execute_adb_command, device_id: str, package_name: str) -> Optional[str]:
    """
    通过 shell 命令快速获取应用名称（不拉取 APK）
    
    使用多种方法尝试获取：
    1. 从 dumpsys package 输出中提取
    2. 使用 pm dump 获取简要信息
    
    Args:
        execute_adb_command: ADB 执行函数
        device_id: 设备 ID
        package_name: 应用包名
        
    Returns:
        str | None: 应用名称，失败返回 None
    """
    start_time = time.time()
    
    try:
        # 方法 1: 从 dumpsys package 中提取
        result = execute_adb_command(
            f"adb -s {device_id} shell dumpsys package {package_name}"
        )
        
        if not result.get("success"):
            return None
        
        if not result["stdout"]:
            return None
        
        app_name = extract_app_name_from_dumpsys(result["stdout"])
        if app_name:
            return app_name
        
        # 方法 2: 尝试使用 cmd package resolve-activity（Android 11+）
        result = execute_adb_command(
            f"adb -s {device_id} shell cmd package resolve-activity --brief {package_name}"
        )
        
        if result.get("success") and result["stdout"]:
            # 输出格式可能包含应用标签信息
            for line in result["stdout"].split('\n'):
                if 'label' in line.lower():
                    match = re.search(r'label[:\s]+(.+)', line, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        if name:
                            return name
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None


def pull_apk_and_extract_app_name(execute_adb_command, device_id: str, package_name: str) -> Optional[str]:
    """
    通过 ADB 拉取 APK 并用 aapt 提取真实应用名称（如"智能货柜"）
    
    这是最慢的方法，仅在其他快速方法失败时使用。
    
    Args:
        execute_adb_command: 外部传入的 ADB 执行函数
        device_id: 设备 ID
        package_name: 应用包名
    
    Returns:
        str | None: 成功返回应用名，失败返回 None
    """
    start_time = time.time()
    
    try:
        # 1. 获取 APK 在设备上的路径
        step_start = time.time()
        result = execute_adb_command(f"adb -s {device_id} shell pm path {package_name}")
        step_duration = time.time() - step_start
        
        if not result.get("success") or not result["stdout"].strip():
            return None

        stdout = result["stdout"].strip()
        if not stdout.startswith("package:"):
            return None
        remote_apk_path = stdout[8:]  # 去掉 "package:"

        # 2. 创建本地临时 APK 文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.apk') as tmp:
            local_apk_path = tmp.name

        try:
            # 3. 拉取 APK 到本地
            step_start = time.time()
            pull_result = execute_adb_command(
                f'adb -s {device_id} pull "{remote_apk_path}" "{local_apk_path}"'
            )
            step_duration = time.time() - step_start
            
            if not pull_result.get("success"):
                return None
            
            # 检查文件大小
            if os.path.exists(local_apk_path):
                file_size = os.path.getsize(local_apk_path)
                size_mb = file_size / (1024 * 1024)

            # 4. ✅ 使用工具管理器获取 aapt 完整路径
            aapt_cmd = get_aapt_path()
            
            # 直接使用 subprocess 执行 aapt，指定 utf-8 编码避免 GBK 解码错误
            step_start = time.time()
            try:
                aapt_process = subprocess.run(
                    [aapt_cmd, "dump", "badging", local_apk_path],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',  # 无法解码的字符替换为替代字符
                    timeout=30
                )
                step_duration = time.time() - step_start
                
                if aapt_process.returncode != 0:
                    return None
                
                aapt_output = aapt_process.stdout

            except subprocess.TimeoutExpired:
                return None
            except Exception as e:
                import traceback
                traceback.print_exc()
                return None

            # 5. 解析 application-label
            for line in aapt_output.splitlines():
                if line.startswith("application-label:"):
                    parts = line.split("'", 2)
                    if len(parts) >= 2:
                        app_name = parts[1]
                        return app_name
            
            return None

        finally:
            # 清理临时 APK 文件
            if os.path.exists(local_apk_path):
                try:
                    os.unlink(local_apk_path)
                except OSError:
                    pass

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None


def background_fetch_app_name(execute_adb_command, device_id: str, package_name: str):
    """
    后台线程：多层次异步获取真实应用名并更新缓存
    
    采用渐进式策略：
    1. 首先尝试快速方法（dumpsys 提取）
    2. 如果失败，才使用慢速方法（拉取 APK + aapt）
    """
    real_name = None
    method_used = "unknown"
    
    # 第 1 层：尝试从 dumpsys 快速提取（不需要拉取 APK）
    real_name = extract_app_name_via_shell(execute_adb_command, device_id, package_name)
    
    if real_name:
        method_used = "dumpsys"
    else:
        # 第 2 层：快速方法失败，使用慢速方法（拉取 APK）
        real_name = pull_apk_and_extract_app_name(execute_adb_command, device_id, package_name)
        
        if real_name:
            method_used = "aapt"
    
    # 更新缓存
    if real_name and real_name != package_name:
        cache = load_label_cache()
        # 仅当新名称不同且有意义时才更新
        if cache.get(package_name) != real_name:
            cache[package_name] = real_name
            save_label_cache(cache)