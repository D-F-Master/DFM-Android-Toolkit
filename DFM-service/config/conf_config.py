import json
from pathlib import Path

from config import logger
from utils import get_conf_path

CONF = None

def get_conf():
    logger.info("conf initialized")
    global CONF
    if CONF is None:
        conf_dir = Path(get_conf_path())
        with open(conf_dir / "conf.json", "r", encoding="UTF-8") as f:
            CONF = json.load(f)
    return CONF