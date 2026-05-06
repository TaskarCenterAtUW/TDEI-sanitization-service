import os
import psutil
from functools import lru_cache

from fastapi import APIRouter, Depends, FastAPI, status

from src.config import Settings
from src.services.sanitization_service import SanitizationService

app = FastAPI()
app.sanitization_service = None

prefix_router = APIRouter(prefix="/health")


@lru_cache()
def get_settings():
    return Settings()


@app.on_event("startup")
async def startup_event(settings: Settings = Depends(get_settings)) -> None:
    try:
        _ = settings
        config = Settings()
        dl_directory = config.get_download_directory()
        if not os.path.exists(dl_directory):
            os.makedirs(dl_directory)
        app.sanitization_service = SanitizationService()
    except Exception as exc:
        print(exc)
        print("\n\n\x1b[31mApplication startup failed due to missing or invalid .env file\x1b[0m")
        print("\x1b[31mPlease provide a valid .env file with the following parameters\x1b[0m")
        print("\x1b[31mSANATIZATION_REQ_TOPIC=xxxx\x1b[0m")
        print("\x1b[31mSANATIZATION_REQ_SUB=xxxx\x1b[0m")
        print("\x1b[31mSANATIZATION_RES_TOPIC=xxxx\x1b[0m")
        print("\x1b[31mQUEUECONNECTION=xxxx\x1b[0m")
        print("\x1b[31mSTORAGECONNECTION=xxxx\x1b[0m")
        print("\x1b[31mMAX_CONCURRENT_MESSAGES=2\x1b[0m")
        print("\x1b[31mMAX_RECEIVABLE_MESSAGES=-1\x1b[0m")
        parent_pid = os.getpid()
        parent = psutil.Process(parent_pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if app.sanitization_service:
        app.sanitization_service.stop_listening()


@app.get("/", status_code=status.HTTP_200_OK)
@prefix_router.get("/", status_code=status.HTTP_200_OK)
def root():
    return "I'm healthy !!"


@app.get("/ping", status_code=status.HTTP_200_OK)
@app.post("/ping", status_code=status.HTTP_200_OK)
@prefix_router.get("/ping", status_code=status.HTTP_200_OK)
@prefix_router.post("/ping", status_code=status.HTTP_200_OK)
def ping():
    return "I'm healthy !!"


app.include_router(prefix_router)
