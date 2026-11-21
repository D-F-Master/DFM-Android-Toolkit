import logging
from pathlib import Path

logger = logging.getLogger("ToolServer")

def setup_logger():
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # 文件日志：写到 service 程序同目录下的 log/service.log（打包后可追溯）
    try:
        from utils.tool_manager import get_log_path
        log_file = get_log_path() / "service.log"
        file_handler = logging.FileHandler(str(log_file), encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # 日志目录不可用时仅控制台告警，不影响服务启动
        print(f"[logger_config] file log disabled: {e}")

    logger.info("Logger initialized")
    return logger