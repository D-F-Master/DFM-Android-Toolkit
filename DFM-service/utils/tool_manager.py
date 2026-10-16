"""
Android 工具路径管理模块
提供 adb、scrcpy、aapt、data、conf 等工具的路径获取功能
支持开发环境和打包后环境的自动适配
"""
import os
import sys
import shutil
from pathlib import Path


def get_base_path() -> Path:
    """
    获取程序的基础路径
    
    Returns:
        Path: 基础路径对象
        - 开发环境: 项目根目录
        - 打包后: exe 所在目录
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的环境
        return Path(sys.executable).parent
    else:
        # 开发环境
        return Path(__file__).parent.parent

def get_data_path() -> Path:
    # 打包后的环境
    base_path = get_base_path()
    data_paths = [
        base_path / "_internal" / "data",
        base_path / "data"
    ]
    for data_path in data_paths:
        if data_path.exists():
            return data_path
    raise FileNotFoundError(f"data依赖文件异常")


def get_conf_path() -> Path:
    base_path = get_base_path()
    conf_paths = [
        base_path / "_internal" / "conf",
        base_path / "conf"
    ]
    for conf_path in conf_paths:
        if conf_path.exists():
            return conf_path
    raise FileNotFoundError(f"data依赖文件异常")

def get_lib_path() -> Path:
    """
    获取 lib 目录路径
    
    Returns:
        Path: lib 目录路径
    """
    base_path = get_base_path()
    
    # 尝试多个可能的位置（按优先级）
    possible_paths = [
        base_path / "lib",                          # 1. exe 同级的 lib 目录
        base_path / "_internal" / "lib",            # 2. _internal/lib (PyInstaller onedir)
        base_path.parent / "lib",                   # 3. 父目录的 lib
        base_path.parent / "_internal" / "lib",     # 4. 父目录的 _internal/lib
    ]
    
    for lib_path in possible_paths:
        if lib_path.exists():
            return lib_path
    
    # 如果都没找到，返回第一个路径（用于错误提示）
    return base_path / "lib"


def get_adb_path() -> str:
    """
    获取 ADB 工具的完整路径
    
    优先级：
    1. lib/adb/adb.exe (内置)
    2. 系统环境变量中的 adb
    
    Returns:
        str: ADB 可执行文件的完整路径
        
    Raises:
        FileNotFoundError: 当找不到 adb 时抛出异常
    """
    # 优先使用内置的 adb
    lib_path = get_lib_path()
    adb_exe = lib_path / "adb" / "adb.exe"
    
    if adb_exe.exists():
        return str(adb_exe)
    
    # 回退到系统环境变量
    system_adb = shutil.which("adb")
    if system_adb:
        return system_adb
    
    raise FileNotFoundError(
        "未找到 adb 工具！\n"
        "请确保以下任一条件满足：\n"
        "1. lib/adb/adb.exe 存在\n"
        "2. 系统环境变量 PATH 中包含 adb"
    )


def get_scrcpy_path() -> str:
    """
    获取 Scrcpy 工具的完整路径
    
    优先级：
    1. lib/scrcpy-win64-*/scrcpy.exe (内置)
    2. 系统环境变量中的 scrcpy
    
    Returns:
        str: Scrcpy 可执行文件的完整路径
        
    Raises:
        FileNotFoundError: 当找不到 scrcpy 时抛出异常
    """
    # 优先使用内置的 scrcpy
    lib_path = get_lib_path()
    
    # 查找 scrcpy 目录（支持不同版本）
    scrcpy_dir = None
    if lib_path.exists():
        for item in lib_path.iterdir():
            if item.is_dir() and item.name.startswith("scrcpy"):
                scrcpy_dir = item
                break
    
    if scrcpy_dir:
        scrcpy_exe = scrcpy_dir / "scrcpy.exe"
        if scrcpy_exe.exists():
            return str(scrcpy_exe)
    
    # 回退到系统环境变量
    system_scrcpy = shutil.which("scrcpy")
    if system_scrcpy:
        return system_scrcpy
    
    raise FileNotFoundError(
        "未找到 scrcpy 工具！\n"
        "请确保以下任一条件满足：\n"
        "1. lib/scrcpy-*/scrcpy.exe 存在\n"
        "2. 系统环境变量 PATH 中包含 scrcpy"
    )


def get_aapt_path() -> str:
    """
    获取 AAPT 工具的完整路径
    
    优先级：
    1. lib/aapt/aapt.exe (内置)
    2. 系统环境变量中的 aapt
    
    Returns:
        str: AAPT 可执行文件的完整路径
        
    Raises:
        FileNotFoundError: 当找不到 aapt 时抛出异常
    """
    # 优先使用内置的 aapt
    lib_path = get_lib_path()
    aapt_exe = lib_path / "aapt" / "aapt.exe"
    
    if aapt_exe.exists():
        return str(aapt_exe)
    
    # 回退到系统环境变量
    system_aapt = shutil.which("aapt")
    if system_aapt:
        return system_aapt
    
    raise FileNotFoundError(
        "未找到 aapt 工具！\n"
        "请确保以下任一条件满足：\n"
        "1. lib/aapt/aapt.exe 存在\n"
        "2. 系统环境变量 PATH 中包含 aapt"
    )


def get_adb_dependencies() -> list:
    """
    获取 ADB 依赖的 DLL 文件列表
    
    Returns:
        list: DLL 文件路径列表
    """
    lib_path = get_lib_path()
    adb_dir = lib_path / "adb"
    
    dependencies = []
    if adb_dir.exists():
        for dll_file in adb_dir.glob("*.dll"):
            if dll_file.exists():
                dependencies.append(str(dll_file))
    
    return dependencies


def get_scrcpy_dependencies() -> list:
    """
    获取 Scrcpy 依赖的 DLL 和服务器文件列表
    
    Returns:
        list: 依赖文件路径列表
    """
    lib_path = get_lib_path()
    
    # 查找 scrcpy 目录
    scrcpy_dir = None
    if lib_path.exists():
        for item in lib_path.iterdir():
            if item.is_dir() and item.name.startswith("scrcpy"):
                scrcpy_dir = item
                break
    
    dependencies = []
    if scrcpy_dir:
        # 添加所有 DLL 文件
        for dll_file in scrcpy_dir.glob("*.dll"):
            if dll_file.exists():
                dependencies.append(str(dll_file))
        
        # 添加 scrcpy-server
        server_file = scrcpy_dir / "scrcpy-server"
        if server_file.exists():
            dependencies.append(str(server_file))
    
    return dependencies


def verify_tools() -> dict:
    """
    验证所有工具的可用性
    
    Returns:
        dict: 工具状态信息
            {
                'adb': {'available': bool, 'path': str},
                'scrcpy': {'available': bool, 'path': str},
                'aapt': {'available': bool, 'path': str}
            }
    """
    tools = {}
    
    # 检查 adb
    try:
        adb_path = get_adb_path()
        tools['adb'] = {'available': True, 'path': adb_path}
    except FileNotFoundError as e:
        tools['adb'] = {'available': False, 'path': None, 'error': str(e)}
    
    # 检查 scrcpy
    try:
        scrcpy_path = get_scrcpy_path()
        tools['scrcpy'] = {'available': True, 'path': scrcpy_path}
    except FileNotFoundError as e:
        tools['scrcpy'] = {'available': False, 'path': None, 'error': str(e)}
    
    # 检查 aapt
    try:
        aapt_path = get_aapt_path()
        tools['aapt'] = {'available': True, 'path': aapt_path}
    except FileNotFoundError as e:
        tools['aapt'] = {'available': False, 'path': None, 'error': str(e)}
    
    return tools

