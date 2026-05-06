import os
from typing import ClassVar

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class EventBusSettings:
    connection_string: str = os.environ.get("QUEUECONNECTION", "")
    request_topic: str = os.environ.get("SANATIZATION_REQ_TOPIC", "")
    request_subscription: str = os.environ.get("SANATIZATION_REQ_SUB", "")
    response_topic: str = os.environ.get("SANATIZATION_RES_TOPIC", "")
    container_name: str = os.environ.get("CONTAINER_NAME", "osw")


class Settings(BaseSettings):
    app_name: str = "python-osw-sanitization"
    event_bus: ClassVar[EventBusSettings] = EventBusSettings()
    max_concurrent_messages: int = int(os.environ.get("MAX_CONCURRENT_MESSAGES", 2))
    max_receivable_messages: int = int(os.environ.get("MAX_RECEIVABLE_MESSAGES", -1))

    def get_root_directory(self) -> str:
        return os.path.dirname(os.path.abspath(__file__))

    def get_download_directory(self) -> str:
        root_dir = self.get_root_directory()
        parent_dir = os.path.dirname(root_dir)
        return os.path.join(parent_dir, "downloads")
