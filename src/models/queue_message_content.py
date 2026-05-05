from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class IncomingData:
    file_upload_path: str
    user_id: str
    tdei_project_group_id: str


@dataclass
class RequestMessage:
    messageId: str
    messageType: str
    data: Optional[IncomingData]

    @classmethod
    def from_dict(cls, data: Dict):
        incoming_data = data.get("data")
        data_obj = IncomingData(**incoming_data) if incoming_data else None
        return cls(
            messageId=data.get("messageId", ""),
            messageType=data.get("messageType", ""),
            data=data_obj,
        )
