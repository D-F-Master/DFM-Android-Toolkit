from utils.tool_manager import verify_tools, get_adb_path, get_scrcpy_path, get_aapt_path, get_data_path, get_conf_path

def check_tool_availability():
    tools_status = verify_tools()
    all_available = True
    
    # 输出工具位置信息
    try:
        adb_path = get_adb_path()
        print(f"ADB: {adb_path}")
    except FileNotFoundError:
        print("ADB: 未找到")
        all_available = False
    
    try:
        scrcpy_path = get_scrcpy_path()
        print(f"Scrcpy: {scrcpy_path}")
    except FileNotFoundError:
        print("Scrcpy: 未找到")
        all_available = False
    
    try:
        aapt_path = get_aapt_path()
        print(f"AAPT: {aapt_path}")
    except FileNotFoundError:
        print("AAPT: 未找到")
        all_available = False

    try:
        data_path = get_data_path()
        print(f"data: {data_path}")
    except FileNotFoundError:
        print("data: 未找到")
        all_available = False

    try:
        conf_path = get_conf_path()
        print(f"conf: {conf_path}")
    except FileNotFoundError:
        print("conf: 未找到")
        all_available = False

    # 输出总体状态
    if all_available:
        print("✅ 所有工具均已就绪")
    else:
        print("⚠️ 依赖文件不全，部分功能可能无法正常使用")
