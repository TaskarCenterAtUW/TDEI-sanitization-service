# TDEI Sanitization Service
[![Unit Tests](https://github.com/TaskarCenterAtUW/TDEI-sanitization-service/actions/workflows/unit_tests.yaml/badge.svg)](https://github.com/TaskarCenterAtUW/TDEI-sanitization-service/actions/workflows/unit_tests.yaml)
[![Coverage](https://raw.githubusercontent.com/TaskarCenterAtUW/TDEI-sanitization-service/badges/coverage.svg)](https://github.com/TaskarCenterAtUW/TDEI-sanitization-service/tree/badges)
[![python-ms-core](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fraw.githubusercontent.com%2FTaskarCenterAtUW%2FTDEI-sanitization-service%2Fmain%2Frequirements.txt&search=%28%3Fm%29%5Epython-ms-core%3D%3D%28%5B%5E%5Cr%5Cn%5D%2B%29&replace=%241&label=python-ms-core&color=blue)](https://pypi.org/project/python-ms-core/)
[![osw-sanitizer](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fraw.githubusercontent.com%2FTaskarCenterAtUW%2FTDEI-sanitization-service%2Fmain%2Frequirements.txt&search=%28%3Fm%29%5Eosw-sanitizer%3D%3D%28%5B%5E%5Cr%5Cn%5D%2B%29&replace=%241&label=osw-sanitizer&color=blue)](https://pypi.org/project/osw-sanitizer/)

A FastAPI microservice that listens to an Azure Service Bus topic, sanitizes OSW/GeoJSON dataset ZIP files with `osw-sanitizer`, uploads the cleaned artifacts to Azure Blob Storage, and publishes the result back to a response topic.

---

## What it does

1. **Subscribes** to an Azure Service Bus topic for incoming sanitization requests.
2. **Downloads** the dataset ZIP from the URL in the message.
3. **Sanitizes** the dataset ZIP through `osw-sanitizer==0.2.0`:
   - Removes unsupported files from the sanitized output ZIP and skips macOS packaging metadata (`__MACOSX/`, `._*`, `.DS_Store`).
   - Removes properties whose value is JSON `null` or a numeric `NaN`; string values such as `"None"`, `"nan"`, `"none"`, `"null"`, `"n/a"`, and `"na"` are preserved, as are falsy but meaningful values (`0`, `false`, `""`).
   - Normalizes geometry coordinate values to **at most 7 decimal places** by default (rounds by default, truncation is configurable); coordinates with fewer decimals keep their original precision and are never padded with trailing zeroes.
   - Creates missing nodes for every `_u_id` / `_v_id` / `_w_id` that no node declares.
   - Enforces unique node `_id`s — identical repeats are dropped, conflicting ones re-ided.
   - Collapses duplicate nodes that share coordinates and tags, repointing references at the survivor.
   - Verifies the node reference graph is intact, then validates the sanitized dataset with `python-osw-validation`.
4. **Writes** a `fixes.json` file that records every removed tag, coordinate adjustment, added/updated/collapsed node, and unsupported file removal, plus a `validation_issues.json` with the validator's findings.
5. **Repackages** the sanitized files into a new ZIP and bundles that ZIP together with `fixes.json` and `validation_issues.json` into `osw_data.zip`.
6. **Uploads** `osw_data.zip` and `fixes.json` to Azure Blob Storage under `jobs/<jobId>/`.
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
        │                         ├─ delegates to osw-sanitizer
        │                         ├─ sanitizes OSW GeoJSON files
        │                         ├─ write fixes.json
        │                         ├─ repack sanitised ZIP
        │                         ├─ validate with python-osw-validation
        │                         └─ bundle osw_data.zip
        │  uploads bundle  →  jobs/<jobId>/osw_data.zip
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
| OSW compliance cleanup | `Dataset was sanitized for OSW compliance.` |

When the sanitized dataset fails OSW validation the result is unsuccessful and the message carries the validator's issues instead of one of the summaries above.

### Sanitization configuration

The service delegates sanitization to `osw-sanitizer==0.2.0` and uses the package defaults:

| Package setting | Default | Purpose |
|---|---:|---|
| `coordinate_precision` | `7` | Maximum decimal places retained for coordinate values. |
| `coordinate_rounding` | `round` | `round` moves a too-long coordinate to the nearest value at the configured precision; `truncate` drops the extra digits. |
| `validate_output` | `true` | Validates the sanitized dataset with `python-osw-validation` and publishes the `osw_data.zip` bundle. When `false`, the sanitized dataset ZIP itself is the published artifact. |

> Geometry splitting and zero-length line removal were dropped in `osw-sanitizer` 0.2.0 — features are never split, regardless of vertex count, so `max_geometry_vertices` and `allow_zero_length_lines` no longer exist.

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
  ],
  "removedFiles": [
    {
      "filename": "readme.txt",
      "fixType": "unsupported_file_removed",
      "action": "removed_from_sanitized_output"
    }
  ]
}
```

Node repairs are recorded per file alongside the entries above, under `addedNodes`, `addedNodeReferences`, `unresolvedReferences`, `removedNodes`, `reassignedNodeIds`, `collapsedNodes`, and `updatedReferences`. Each entry carries a `fixType` (`missing_node_created`, `reference_id_missing`, `reference_coordinate_unknown`, `duplicate_node_removed`, `duplicate_node_id_reassigned`, `duplicate_node_collapsed`, `collapsed_node_reference_updated`) and an `action`.

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
    "sanitization_dataset_url": "https://tdeisamplestorage.blob.core.windows.net/osw/jobs/0001/osw_data.zip",
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
