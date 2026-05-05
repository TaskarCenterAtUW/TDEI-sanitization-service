# TDEI-sanitization-service

FastAPI service that listens to an Azure topic subscription, sanitizes incoming dataset zip files, uploads sanitized zip to Azure Blob Storage, and publishes the result to an Azure response topic.

## Features
- FastAPI app with health endpoints
- Startup queue subscriber listener
- Incoming message contract support:
  - `messageId`
  - `messageType`
  - `data.file_upload_path`
  - `data.user_id`
  - `data.tdei_project_group_id`
- Sanitization placeholder module (`src/sanitization/processor.py`) where exact logic can be added
- Upload sanitized zip to Azure Blob on success
- Publish outgoing status message to response topic

## Environment variables
Copy `.env.example` to `.env` and set:

```bash
PROVIDER=Azure
QUEUECONNECTION=xxx
STORAGECONNECTION=xxx
SANATIZATION_REQ_TOPIC=xxx
SANATIZATION_REQ_SUB=xxx
SANATIZATION_RES_TOPIC=xxx
CONTAINER_NAME=osw
MAX_CONCURRENT_MESSAGES=2
MAX_RECEIVABLE_MESSAGES=-1
TOPIC_CALLBACK_EXECUTION_MODE=thread
```

`MAX_RECEIVABLE_MESSAGES` behavior:
- `-1` means no limit (service keeps running).
- `> 0` means process only that many receivable messages, then stop the server/container.

`TOPIC_CALLBACK_EXECUTION_MODE=thread` avoids macOS fork-based callback worker crashes.

## Run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload
```

## Health endpoints
- `GET /`
- `GET /health`
- `GET /ping`
- `POST /ping`
- `GET /health/ping`
- `POST /health/ping`

## Message contracts
- Incoming example: `src/assets/incoming_message.json`
- Outgoing example: `src/assets/outgoing_message.json`

## Where to add real sanitization logic
Implement exact sanitization in:
- `src/sanitization/processor.py`

Current placeholder behavior copies the input zip to `sanitized_<original_name>.zip`.
