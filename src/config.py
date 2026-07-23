import os
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class SanitizationConfig:
    coordinate_precision: int = field(
        default_factory=lambda: int(os.environ.get("SANITIZATION_COORDINATE_PRECISION", 7))
    )
    zero_length_edge_threshold: float = field(
        default_factory=lambda: float(os.environ.get("SANITIZATION_ZERO_LENGTH_EDGE_THRESHOLD", 0))
    )
    max_edge_vertices: int = field(
        default_factory=lambda: int(os.environ.get("SANITIZATION_MAX_EDGE_VERTICES", 2000))
    )

    def __post_init__(self) -> None:
        if isinstance(self.coordinate_precision, bool) or not isinstance(self.coordinate_precision, int):
            raise TypeError("coordinate_precision must be an integer.")
        if self.coordinate_precision < 0:
            raise ValueError("coordinate_precision must be zero or greater.")
        if self.zero_length_edge_threshold < 0:
            raise ValueError("zero_length_edge_threshold must be zero or greater.")
        if isinstance(self.max_edge_vertices, bool) or not isinstance(self.max_edge_vertices, int):
            raise TypeError("max_edge_vertices must be an integer.")
        if self.max_edge_vertices < 2:
            raise ValueError("max_edge_vertices must be at least 2.")


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
