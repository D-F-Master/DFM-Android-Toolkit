from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.queue_config import task_queue_lifespan

def create_app():
    app = FastAPI(
        title="uTools Service API",
        description="Android 设备管理工具服务",
        version="1.2.3",
        lifespan=task_queue_lifespan,
    )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    return app
