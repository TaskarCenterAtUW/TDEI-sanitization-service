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
    max_geometry_vertices: int = field(
        default_factory=lambda: int(os.environ.get("SANITIZATION_MAX_GEOMETRY_VERTICES", 2000))
    )
    allow_zero_length_lines: bool = field(
        default_factory=lambda: os.environ.get("SANITIZATION_ALLOW_ZERO_LENGTH_LINES", "false").lower()
        in ("1", "true", "yes")
    )

    def __post_init__(self) -> None:
        if isinstance(self.coordinate_precision, bool) or not isinstance(self.coordinate_precision, int):
            raise TypeError("coordinate_precision must be an integer.")
        if self.coordinate_precision < 0:
            raise ValueError("coordinate_precision must be zero or greater.")
        if isinstance(self.max_geometry_vertices, bool) or not isinstance(self.max_geometry_vertices, int):
            raise TypeError("max_geometry_vertices must be an integer.")
        if self.max_geometry_vertices < 2:
            raise ValueError("max_geometry_vertices must be at least 2.")
        if not isinstance(self.allow_zero_length_lines, bool):
            raise TypeError("allow_zero_length_lines must be a boolean.")


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
