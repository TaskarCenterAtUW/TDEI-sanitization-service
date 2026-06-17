# TDEI Sanitization Service
[![Unit Tests](https://github.com/TaskarCenterAtUW/TDEI-sanitization-service/actions/workflows/unit_tests.yaml/badge.svg)](https://github.com/TaskarCenterAtUW/TDEI-sanitization-service/actions/workflows/unit_tests.yaml)
[![Coverage](https://raw.githubusercontent.com/TaskarCenterAtUW/TDEI-sanitization-service/badges/coverage.svg)](https://github.com/TaskarCenterAtUW/TDEI-sanitization-service/tree/badges)
[![python-ms-core](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fraw.githubusercontent.com%2FTaskarCenterAtUW%2FTDEI-sanitization-service%2Fmain%2Frequirements.txt&search=%28%3Fm%29%5Epython-ms-core%3D%3D%28%5B%5E%5Cr%5Cn%5D%2B%29&replace=%241&label=python-ms-core&color=blue)](https://pypi.org/project/python-ms-core/)

A FastAPI microservice that listens to an Azure Service Bus topic, sanitizes OSW/GeoJSON dataset ZIP files, uploads the cleaned artifacts to Azure Blob Storage, and publishes the result back to a response topic.

---

## What it does

1. **Subscribes** to an Azure Service Bus topic for incoming sanitization requests.
2. **Downloads** the dataset ZIP from the URL in the message.
3. **Extracts** the ZIP and processes every `.geojson` file inside it:
   - Removes properties whose value is JSON `null` or a numeric `NaN`; string values such as `"None"`, `"nan"`, `"none"`, `"null"`, `"n/a"`, and `"na"` are preserved.
   - Normalizes geometry coordinate values to **at most 7 decimal places** (truncates if more); coordinates with fewer decimals keep their original precision and are never padded with trailing zeroes.
   - Skips macOS resource-fork files (`__MACOSX/`, `._*`, `.DS_Store`).
4. **Writes** a `fixes.json` file that records every removed tag and every coordinate that was adjusted.
5. **Repackages** the sanitized files into a new ZIP.
6. **Uploads** both artifacts to Azure Blob Storage under `jobs/<jobId>/`.
7. **Publishes** an outgoing message to a response topic with the result status and blob URLs.

---

## How it works

```
Azure Service Bus (request topic)
        │
        ▼
  SanitizationService.subscribe()
        │  deserialises message → RequestMessage
        ▼
  SanitizationService.process_message()
        │  validates jobId and file_upload_path
        │  downloads ZIP  →  SanitizationProcessor.sanitize_dataset()
        │                         ├─ extracts ZIP
        │                         ├─ per .geojson file:
        │                         │     remove null/NaN props
        │                         │     normalise coords → ≤7 d.p.
        │                         │     record changes
        │                         ├─ write fixes.json
        │                         └─ repack sanitised ZIP
        │  uploads ZIP     →  jobs/<jobId>/<filename>.zip
        │  uploads fixes    →  jobs/<jobId>/fixes.json
        ▼
Azure Service Bus (response topic)
```

### Sanitization messages

| What changed | Message |
|---|---|
| Nothing | `No changes were needed. The dataset is already clean.` |
| Coordinates only | `Coordinates were standardized for consistency.` |
| Null/NaN tags only | `Invalid or empty values were removed from the dataset.` |
| Both | `Dataset was cleaned and coordinates were standardized.` |

### Metadata format

```json
{
  "jobId": "0001",
  "files": [
    {
      "filename": "edges.geojson",
      "removedTags": [
        { "featureIndex": 0, "tag": "width", "value": null }
      ],
      "precisionUpdates": [
        {
          "featureIndex": 0,
          "coordinatePath": "geometry.coordinates[0]",
          "original": "-122.123456789",
          "updated": "-122.1234567"
        }
      ]
    }
  ]
}
```

---

## Message contracts

### Incoming

```json
{
  "messageId": "4",
  "messageType": "workflow_identifier",
  "data": {
    "jobId": "0001",
    "file_upload_path": "https://tdeisamplestorage.blob.core.windows.net/tdei-storage-test/Archivew.zip",
    "user_id": "c59d29b6-a063-4249-943f-d320d15ac9ab"
  }
}
```

### Outgoing

```json
{
  "messageId": "c8c76e89f30944d2b2abd2491bd95337",
  "messageType": "workflow_identifier",
  "data": {
    "jobId": "0001",
    "file_upload_path": "https://tdeisamplestorage.blob.core.windows.net/tdei-storage-test/Archivew.zip",
    "user_id": "c59d29b6-a063-4249-943f-d320d15ac9ab",
    "success": true,
    "message": "Coordinates were standardized for consistency.",
    "sanitization_dataset_url": "https://tdeisamplestorage.blob.core.windows.net/osw/jobs/0001/Archivew.zip",
    "metadata_url": "https://tdeisamplestorage.blob.core.windows.net/osw/jobs/0001/fixes.json"
  }
}
```

---

## Required environment variables

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Default | Description |
|---|---|---|---|
| `PROVIDER` | Yes | — | Cloud provider. Set to `Azure`. |
| `QUEUECONNECTION` | Yes | — | Azure Service Bus connection string. |
| `STORAGECONNECTION` | Yes | — | Azure Blob Storage connection string. |
| `SANATIZATION_REQ_TOPIC` | Yes | — | Service Bus topic to subscribe to for requests. |
| `SANATIZATION_REQ_SUB` | Yes | — | Subscription name on the request topic. |
| `SANATIZATION_RES_TOPIC` | Yes | — | Service Bus topic to publish responses to. |
| `CONTAINER_NAME` | No | `osw` | Blob Storage container where uploads go. |
| `MAX_CONCURRENT_MESSAGES` | No | `2` | Maximum messages processed in parallel. |
| `MAX_RECEIVABLE_MESSAGES` | No | `-1` | `-1` = run indefinitely. Any positive integer = stop after that many messages. |

---

## Running the application

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your Azure connection strings

# 4. Start the service
uvicorn src.main:app --reload
```

The service starts on `http://localhost:8000`. On startup it immediately begins listening to the configured Service Bus topic.

### Docker

```bash
docker build -t tdei-sanitization-service .
docker run --env-file .env -p 8000:8000 tdei-sanitization-service
```

---

## Health endpoints

| Method | Path | Response |
|---|---|---|
| `GET` | `/` | `"I'm healthy !!"` |
| `GET` | `/health` | `"I'm healthy !!"` |
| `GET` | `/ping` | `"I'm healthy !!"` |
| `POST` | `/ping` | `"I'm healthy !!"` |
| `GET` | `/health/ping` | `"I'm healthy !!"` |
| `POST` | `/health/ping` | `"I'm healthy !!"` |

---

## Running unit tests

```bash
python -m unittest discover -s tests
```

### Running with coverage

```bash
# Install coverage if not already present
pip install coverage

# Run tests and collect coverage (example.py is excluded)
python -m coverage run --rcfile=.coveragerc -m unittest discover -s tests

# Print a summary report
python -m coverage report

# Generate an HTML report (opens in browser)
python -m coverage html
open htmlcov/index.html
```

Coverage is configured in [`.coveragerc`](.coveragerc) to measure `src/` and exclude `src/example.py`.

Current coverage: **100%** across all source modules.
