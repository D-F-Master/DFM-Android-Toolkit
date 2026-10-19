from pathlib import Path
from fastapi import APIRouter
from starlette.responses import FileResponse

from config import logger
from utils import get_data_path

router = APIRouter()

# ========== 前端网页地址 ==========
FRONTEND_DIR = Path(f"{get_data_path()}\dist")

# 桌面端页面不缓存：前端产物更新后旧窗口不会拿到陈旧页面
NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}

logger.info("html initialized")
# ========== 引入网页地址 ==========
@router.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"), headers=NO_CACHE_HEADERS)


@router.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = FRONTEND_DIR / full_path
    if file_path.is_file():
        return FileResponse(str(file_path), headers=NO_CACHE_HEADERS)
    return FileResponse(str(FRONTEND_DIR / "index.html"), headers=NO_CACHE_HEADERS)
