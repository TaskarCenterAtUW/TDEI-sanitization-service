import gc
import json
import os
import signal
import shutil
import threading
import time
from typing import Dict
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse

import psutil
from python_ms_core import Core
from python_ms_core.core.queue.models.queue_message import QueueMessage

from src.config import Settings
from src.logger import Logger
from src.models.queue_message_content import RequestMessage
from src.sanitization.processor import SanitizationProcessor


class SanitizationService:
    _config = Settings()

    def __init__(self):
        # python-ms-core defaults topic callback execution to process/fork.
        # On macOS this can crash with objc fork-safety errors in callback workers.
        os.environ["TOPIC_CALLBACK_EXECUTION_MODE"] = "thread"
        self.core = Core()
        self._shutdown_triggered = threading.Event()
        self._subscription_name = self._config.event_bus.request_subscription
        self.request_topic = self.core.get_topic(
            topic_name=self._config.event_bus.request_topic,
            max_concurrent_messages=self._config.max_concurrent_messages,
        )
        self.storage_client = self.core.get_storage_client()
        self.container_name = self._config.event_bus.container_name
        self.listening_thread = threading.Thread(target=self.subscribe)
        self.listening_thread.start()

    def subscribe(self) -> None:
        def process(message) -> None:
            try:
                if message is None:
                    Logger.info("No message")
                    return

                request_message = QueueMessage.to_dict(message)
                request_msg = RequestMessage.from_dict(request_message)
                self.process_message(request_msg)
            except Exception as exc:
                # Never let callback crash propagate; this prevents unnecessary DLQ.
                Logger.error(f"Unhandled callback error: {exc}")

        self.request_topic.subscribe(
            subscription=self._subscription_name,
            callback=process,
            max_receivable_messages=self._config.max_receivable_messages,
        )
        if self._config.max_receivable_messages > 0:
            Logger.info("Listener finished processing available messages; stopping server/container.")
            self._stop_server_and_container(delay_seconds=2)

    def process_message(self, request_msg: RequestMessage) -> None:
        job_id = request_msg.data.jobId if request_msg.data else None
        input_path = request_msg.data.file_upload_path if request_msg.data else None
        success = False
        message = ""
        updated_dataset_path = ""
        metadata_path = ""

        job_work_dir = os.path.join(self._config.get_download_directory(), job_id or "unknown-job")
        local_input_file = None

        try:
            Logger.info(f"Message ID: {request_msg.messageId}")
            if not request_msg.data:
                raise ValueError("Invalid message: missing data")
            if not job_id:
                raise ValueError("Missing jobId in request data")

            if not input_path:
                raise ValueError("No file_upload_path found in request")

            with Logger.timer(f"process_message (job_id={job_id})"):
                try:
                    local_input_file = self.download_input_file(input_path=input_path, job_id=job_id)
                except Exception as exc:
                    raise RuntimeError(f"Download failed: {exc}") from exc
                with Logger.timer(f"sanitize_dataset (job_id={job_id})"):
                    sanitization_result = SanitizationProcessor.sanitize_dataset(
                        input_zip_path=local_input_file,
                        output_dir=job_work_dir,
                    )
                success = sanitization_result["success"]
                message = sanitization_result["message"]

                if success:
                    updated_dataset_zip = sanitization_result["updated_dataset_zip"]
                    metadata_json = sanitization_result["metadata_json"]
                    self.update_metadata_job_id(metadata_json, job_id)

                    updated_dataset_path = self.upload_to_azure(job_id=job_id, file_path=updated_dataset_zip) or ""
                    metadata_path = self.upload_metadata_json(job_id=job_id, metadata_file_path=metadata_json) or ""
                    success = bool(updated_dataset_path and metadata_path)
                    if not success:
                        message = "Failed to upload sanitized dataset artifacts"

                if not success and not message:
                    message = "Sanitization failed"

        except Exception as exc:
            Logger.error(f"Error while processing message: {exc}")
            success = False
            message = str(exc)
        finally:
            self.send_status(
                valid=success,
                status_message=message,
                request_message=request_msg,
                sanitization_dataset_url=updated_dataset_path,
                metadata_url=metadata_path,
                original_file_upload_path=input_path or "",
            )
            self.cleanup(path=job_work_dir)
            gc.collect()

    def send_status(
        self,
        valid: bool,
        status_message: str,
        request_message: RequestMessage,
        sanitization_dataset_url: str,
        metadata_url: str,
        original_file_upload_path: str,
    ) -> None:
        Logger.info(
            f"Publishing message ID: {request_message.messageId} with status: {valid}"
        )

        response_message = {
            "file_upload_path": original_file_upload_path,
            "user_id": request_message.data.user_id if request_message.data else "",
            "jobId": request_message.data.jobId if request_message.data else "",
            "success": valid,
            "message": status_message if status_message else ("Success" if valid else "Failed"),
            "sanitization_dataset_url": sanitization_dataset_url if valid else "",
            "metadata_url": metadata_url if valid else "",
        }

        data = QueueMessage.data_from(
            {
                "messageId": request_message.messageId,
                "messageType": request_message.messageType,
                "data": response_message,
            }
        )
        try:
            response_topic = self.core.get_topic(topic_name=self._config.event_bus.response_topic)
            response_topic.publish(data=data)
            Logger.info(f"Published message for ID: {request_message.messageId}")
        except Exception as exc:
            Logger.error(f"Error publishing message for ID {request_message.messageId}: {exc}")

    def download_input_file(self, input_path: str, job_id: str) -> str:
        Logger.info(f"Downloading input dataset from: {input_path}")
        target_directory = os.path.join(self._config.get_download_directory(), job_id)
        os.makedirs(target_directory, exist_ok=True)

        parsed = urlparse(input_path)
        filename = os.path.basename(parsed.path) or f"{job_id}.zip"
        local_file_path = os.path.join(target_directory, filename)

        with Logger.timer(f"download_input_file (job_id={job_id})"):
            if parsed.scheme in ("http", "https"):
                try:
                    with urlrequest.urlopen(input_path, timeout=120) as response, open(local_file_path, "wb") as output_file:
                        shutil.copyfileobj(response, output_file)
                except HTTPError as exc:
                    raise RuntimeError(f"HTTP download failed with status {exc.code}: {exc.reason}") from exc
                except URLError as exc:
                    raise RuntimeError(f"URL download failed: {exc.reason}") from exc
                except Exception as exc:
                    raise RuntimeError(f"Failed to download input file from URL: {exc}") from exc
            else:
                shutil.copy(input_path, local_file_path)

        Logger.info(f"Downloaded dataset to: {local_file_path}")
        return local_file_path

    def upload_to_azure(self, job_id: str, file_path: str):
        Logger.info(f"Uploading file to Azure: {file_path}")
        try:
            with Logger.timer(f"upload_to_azure (job_id={job_id})"):
                target_directory = f"jobs/{job_id}"
                target_file_remote_path = f"{target_directory}/{os.path.basename(file_path)}"

                container = self.storage_client.get_container(container_name=self.container_name)
                file = container.create_file(name=target_file_remote_path)
                with open(file_path, "rb") as data:
                    file.upload(data)
                uploaded_url = file.get_remote_url()
            Logger.info(f"File uploaded to Azure: {uploaded_url}")
            return uploaded_url
        except Exception as exc:
            Logger.error(f"Upload failed: {exc}")
            return None

    def upload_metadata_json(self, job_id: str, metadata_file_path: str):
        Logger.info(f"Uploading metadata JSON for job: {job_id}")
        try:
            with Logger.timer(f"upload_metadata_json (job_id={job_id})"):
                target_directory = f"jobs/{job_id}"
                target_file_remote_path = f"{target_directory}/metadata.json"

                container = self.storage_client.get_container(container_name=self.container_name)
                file = container.create_file(name=target_file_remote_path)
                with open(metadata_file_path, "rb") as metadata_file:
                    file.upload(metadata_file)
                uploaded_url = file.get_remote_url()
            Logger.info(f"Metadata uploaded to Azure: {uploaded_url}")
            return uploaded_url
        except Exception as exc:
            Logger.error(f"Metadata upload failed: {exc}")
            return None

    @staticmethod
    def update_metadata_job_id(metadata_file_path: str, job_id: str) -> None:
        with open(metadata_file_path, "r", encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)
        metadata["jobId"] = job_id
        with open(metadata_file_path, "w", encoding="utf-8") as metadata_file:
            json.dump(metadata, metadata_file, indent=2, ensure_ascii=True)

    def cleanup(self, path: str) -> None:
        if os.path.exists(path):
            Logger.info(f"Cleaning up files in: {path}")
            shutil.rmtree(path, ignore_errors=True)

    def stop_listening(self):
        self._stop_server_and_container()
        self.listening_thread.join(timeout=0)

    def _stop_server_and_container(self, delay_seconds: float = 0.0):
        Logger.info("Gracefully stopping FastAPI/uvicorn and Docker container")
        if self._shutdown_triggered.is_set():
            Logger.info("Server stop already in progress; skipping duplicate trigger.")
            return
        self._shutdown_triggered.set()

        def _terminate():
            if delay_seconds:
                time.sleep(delay_seconds)
            try:
                Logger.info("Sending SIGTERM to stop server/container.")
                # In uvicorn --reload mode, this worker is a child process.
                # Stop the reloader parent too so the service actually exits after one message.
                parent_pid = os.getppid()
                if parent_pid and parent_pid != 1:
                    try:
                        parent_cmd = " ".join(psutil.Process(parent_pid).cmdline()).lower()
                        if "uvicorn" in parent_cmd:
                            os.kill(parent_pid, signal.SIGTERM)
                    except Exception as exc:
                        Logger.warning(f"Could not terminate parent process {parent_pid}: {exc}")
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception as exc:
                Logger.warning(f"Error while sending SIGTERM: {exc}")
            finally:
                os._exit(0)

        threading.Thread(target=_terminate, daemon=True).start()
