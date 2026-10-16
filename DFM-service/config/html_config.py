from pathlib import Path
from fastapi import APIRouter
from starlette.responses import FileResponse

from config import logger
from utils import get_data_path

router = APIRouter()

# ========== 前端网页地址 ==========
FRONTEND_DIR = Path(f"{get_data_path()}\dist")

logger.info("html initialized")
# ========== 引入网页地址 ==========
@router.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@router.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = FRONTEND_DIR / full_path
    if file_path.is_file():
        return FileResponse(str(file_path))
    return FileResponse(str(FRONTEND_DIR / "index.html"))