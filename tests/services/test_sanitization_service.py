import json
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from src.models.queue_message_content import IncomingData, RequestMessage
from src.services.sanitization_service import SanitizationService


class TestSanitizationService(unittest.TestCase):
    def _build_service_without_init(self):
        service = SanitizationService.__new__(SanitizationService)
        service.core = MagicMock()
        service.container_name = "osw"
        service._shutdown_triggered = MagicMock()
        return service

    @patch("src.services.sanitization_service.QueueMessage")
    def test_send_status_publishes_expected_payload(self, queue_message_mock):
        service = self._build_service_without_init()
        response_topic = MagicMock()
        service.core.get_topic.return_value = response_topic

        request_msg = RequestMessage(
            messageId="mid-1",
            messageType="workflow_identifier",
            data=IncomingData(
                jobId="job-1",
                file_upload_path="https://example.com/input.zip",
                user_id="user-1",
            ),
        )

        queue_message_mock.data_from.side_effect = lambda payload: payload

        service.send_status(
            valid=True,
            status_message="",
            request_message=request_msg,
            sanitization_dataset_url="https://example.blob.core.windows.net/osw/jobs/job-1/input.zip",
            metadata_url="https://example.blob.core.windows.net/osw/jobs/job-1/metadata.json",
            original_file_upload_path="https://example.com/input.zip",
        )

        payload = queue_message_mock.data_from.call_args[0][0]
        self.assertEqual(payload["data"]["jobId"], "job-1")
        self.assertEqual(
            payload["data"]["sanitization_dataset_url"],
            "https://example.blob.core.windows.net/osw/jobs/job-1/input.zip",
        )
        self.assertEqual(
            payload["data"]["metadata_url"],
            "https://example.blob.core.windows.net/osw/jobs/job-1/metadata.json",
        )
        response_topic.publish.assert_called_once_with(data=payload)

    @patch("src.services.sanitization_service.QueueMessage")
    def test_send_status_blanks_urls_and_keeps_error_message_on_failure(self, queue_message_mock):
        service = self._build_service_without_init()
        service.core.get_topic.return_value = MagicMock()
        queue_message_mock.data_from.side_effect = lambda payload: payload

        request_msg = RequestMessage(
            messageId="mid-fail",
            messageType="workflow_identifier",
            data=IncomingData(jobId="job-f", file_upload_path="https://example.com/input.zip", user_id="user-1"),
        )

        service.send_status(
            valid=False,
            status_message="edges.geojson: feature 0 has a non-finite coordinate (NaN) at coordinates[0]",
            request_message=request_msg,
            sanitization_dataset_url="",
            metadata_url="",
            original_file_upload_path="https://example.com/input.zip",
        )

        payload = queue_message_mock.data_from.call_args[0][0]["data"]
        self.assertFalse(payload["success"])
        self.assertEqual(payload["sanitization_dataset_url"], "")
        self.assertEqual(payload["metadata_url"], "")
        self.assertIn("non-finite coordinate", payload["message"])
        self.assertIn("edges.geojson", payload["message"])

    def test_process_message_handles_none_data(self):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()

        request_msg = RequestMessage(
            messageId="mid-none",
            messageType="workflow_identifier",
            data=None,
        )

        service.process_message(request_msg)

        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertIn("missing data", kwargs["status_message"].lower())

    def test_process_message_handles_missing_file_upload_path(self):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()

        request_msg = RequestMessage(
            messageId="mid-nopath",
            messageType="workflow_identifier",
            data=IncomingData(jobId="job-nopath", file_upload_path="", user_id="user-1"),
        )

        service.process_message(request_msg)

        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertIn("file_upload_path", kwargs["status_message"].lower())

    def test_process_message_handles_missing_job_id(self):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()

        request_msg = RequestMessage(
            messageId="mid-2",
            messageType="workflow_identifier",
            data=IncomingData(jobId="", file_upload_path="https://example.com/input.zip", user_id="user-1"),
        )

        service.process_message(request_msg)

        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertIn("missing jobid", kwargs["status_message"].lower())

    def test_process_message_handles_download_failure(self):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()
        service.download_input_file = MagicMock(side_effect=RuntimeError("network failure"))

        request_msg = RequestMessage(
            messageId="mid-3",
            messageType="workflow_identifier",
            data=IncomingData(jobId="job-3", file_upload_path="https://example.com/input.zip", user_id="user-1"),
        )

        service.process_message(request_msg)

        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertIn("download failed", kwargs["status_message"].lower())

    @patch("src.services.sanitization_service.SanitizationProcessor")
    def test_azure_upload_called_with_correct_paths(self, processor_mock):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()
        service.download_input_file = MagicMock(return_value="/tmp/input.zip")
        service.upload_to_azure = MagicMock(return_value="https://example.blob.core.windows.net/osw/jobs/job-4/input.zip")
        service.upload_fixes_json = MagicMock(return_value="https://example.blob.core.windows.net/osw/jobs/job-4/fixes.json")

        with tempfile.TemporaryDirectory() as temp_dir:
            updated_dataset_zip = os.path.join(temp_dir, "input.zip")
            fixes_json = os.path.join(temp_dir, "fixes.json")
            with open(updated_dataset_zip, "wb") as dataset_file:
                dataset_file.write(b"zip-data")
            with open(fixes_json, "w", encoding="utf-8") as fixes_file:
                json.dump({"jobId": ""}, fixes_file)

            processor_mock.sanitize_dataset.return_value = {
                "success": True,
                "message": "Sanitization completed successfully",
                "updated_dataset_zip": updated_dataset_zip,
                "fixes_json": fixes_json,
            }

            request_msg = RequestMessage(
                messageId="mid-4",
                messageType="workflow_identifier",
                data=IncomingData(jobId="job-4", file_upload_path="https://example.com/input.zip", user_id="user-1"),
            )

            service.process_message(request_msg)

        service.upload_to_azure.assert_called_once_with(job_id="job-4", file_path=updated_dataset_zip)
        service.upload_fixes_json.assert_called_once_with(job_id="job-4", fixes_file_path=fixes_json)
        kwargs = service.send_status.call_args.kwargs
        self.assertTrue(kwargs["valid"])
        self.assertEqual(
            kwargs["sanitization_dataset_url"],
            "https://example.blob.core.windows.net/osw/jobs/job-4/input.zip",
        )
        self.assertEqual(
            kwargs["metadata_url"],
            "https://example.blob.core.windows.net/osw/jobs/job-4/fixes.json",
        )

    def test_update_fixes_job_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixes_path = os.path.join(temp_dir, "fixes.json")
            with open(fixes_path, "w", encoding="utf-8") as fixes_file:
                json.dump({"jobId": "", "files": []}, fixes_file)

            SanitizationService.update_fixes_job_id(fixes_path, "job-9")

            with open(fixes_path, "r", encoding="utf-8") as fixes_file:
                fixes = json.load(fixes_file)

            self.assertEqual(fixes["jobId"], "job-9")

    def test_subscribe_passes_max_receivable_messages(self):
        service = self._build_service_without_init()
        service._subscription_name = "sub-1"
        service.request_topic = MagicMock()
        service._config = MagicMock()
        service._config.max_receivable_messages = 3
        service._stop_server_and_container = MagicMock()

        service.subscribe()

        subscribe_kwargs = service.request_topic.subscribe.call_args.kwargs
        self.assertEqual(subscribe_kwargs["subscription"], "sub-1")
        self.assertEqual(subscribe_kwargs["max_receivable_messages"], 3)
        self.assertTrue(callable(subscribe_kwargs["callback"]))
        service._stop_server_and_container.assert_called_once_with(delay_seconds=2)

    def test_subscribe_does_not_stop_server_when_receivable_is_unlimited(self):
        service = self._build_service_without_init()
        service._subscription_name = "sub-1"
        service.request_topic = MagicMock()
        service._config = MagicMock()
        service._config.max_receivable_messages = -1
        service._stop_server_and_container = MagicMock()

        service.subscribe()

        service._stop_server_and_container.assert_not_called()


    # ── __init__ ──────────────────────────────────────────────────────────────

    @patch("src.services.sanitization_service.threading.Thread")
    @patch("src.services.sanitization_service.Core")
    def test_init_creates_core_and_starts_listening_thread(self, mock_core_cls, mock_thread_cls):
        mock_core_instance = MagicMock()
        mock_core_cls.return_value = mock_core_instance
        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance

        service = SanitizationService()

        mock_core_cls.assert_called_once()
        mock_thread_instance.start.assert_called_once()
        self.assertIs(service.core, mock_core_instance)

    # ── subscribe callback paths ──────────────────────────────────────────────

    def test_subscribe_callback_handles_none_message(self):
        service = self._build_service_without_init()
        service._subscription_name = "sub-1"
        service.request_topic = MagicMock()
        service._config = MagicMock()
        service._config.max_receivable_messages = -1
        service._stop_server_and_container = MagicMock()
        service.process_message = MagicMock()

        service.subscribe()
        callback = service.request_topic.subscribe.call_args.kwargs["callback"]
        callback(None)

        service.process_message.assert_not_called()

    @patch("src.services.sanitization_service.QueueMessage")
    @patch("src.services.sanitization_service.RequestMessage")
    def test_subscribe_callback_processes_valid_message(self, request_msg_mock, queue_msg_mock):
        service = self._build_service_without_init()
        service._subscription_name = "sub-1"
        service.request_topic = MagicMock()
        service._config = MagicMock()
        service._config.max_receivable_messages = -1
        service._stop_server_and_container = MagicMock()
        service.process_message = MagicMock()

        service.subscribe()
        callback = service.request_topic.subscribe.call_args.kwargs["callback"]
        callback(MagicMock())

        service.process_message.assert_called_once()

    @patch("src.services.sanitization_service.QueueMessage")
    def test_subscribe_callback_logs_unhandled_exception(self, queue_msg_mock):
        service = self._build_service_without_init()
        service._subscription_name = "sub-1"
        service.request_topic = MagicMock()
        service._config = MagicMock()
        service._config.max_receivable_messages = -1
        service._stop_server_and_container = MagicMock()

        queue_msg_mock.to_dict.side_effect = Exception("parse error")

        service.subscribe()
        callback = service.request_topic.subscribe.call_args.kwargs["callback"]
        # Must not raise even when callback body throws
        callback(MagicMock())

    # ── process_message edge cases ────────────────────────────────────────────

    @patch("src.services.sanitization_service.SanitizationProcessor")
    def test_process_message_sanitization_fails_with_empty_message(self, processor_mock):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()
        service.download_input_file = MagicMock(return_value="/tmp/input.zip")

        processor_mock.sanitize_dataset.return_value = {
            "success": False,
            "message": "",
            "updated_dataset_zip": None,
            "fixes_json": None,
        }

        request_msg = RequestMessage(
            messageId="mid-x",
            messageType="workflow_identifier",
            data=IncomingData(jobId="job-x", file_upload_path="https://example.com/input.zip", user_id="user-1"),
        )
        service.process_message(request_msg)

        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertEqual(kwargs["status_message"], "Sanitization failed")

    @patch("src.services.sanitization_service.SanitizationProcessor")
    def test_process_message_sets_message_when_upload_fails(self, processor_mock):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()
        service.download_input_file = MagicMock(return_value="/tmp/input.zip")
        service.upload_to_azure = MagicMock(return_value=None)
        service.upload_fixes_json = MagicMock(return_value=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            updated_dataset_zip = os.path.join(temp_dir, "input.zip")
            fixes_json = os.path.join(temp_dir, "fixes.json")
            with open(updated_dataset_zip, "wb") as f:
                f.write(b"zip")
            with open(fixes_json, "w") as f:
                json.dump({"jobId": ""}, f)

            processor_mock.sanitize_dataset.return_value = {
                "success": True,
                "message": "clean",
                "updated_dataset_zip": updated_dataset_zip,
                "fixes_json": fixes_json,
            }

            request_msg = RequestMessage(
                messageId="mid-upload-fail",
                messageType="workflow_identifier",
                data=IncomingData(jobId="job-uf", file_upload_path="https://example.com/input.zip", user_id="u1"),
            )
            service.process_message(request_msg)

        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertIn("Failed to upload", kwargs["status_message"])

    # ── send_status publish exception ─────────────────────────────────────────

    @patch("src.services.sanitization_service.QueueMessage")
    def test_send_status_handles_publish_exception(self, queue_message_mock):
        service = self._build_service_without_init()
        response_topic = MagicMock()
        response_topic.publish.side_effect = Exception("broker down")
        service.core.get_topic.return_value = response_topic
        queue_message_mock.data_from.side_effect = lambda p: p

        request_msg = RequestMessage(
            messageId="mid-err",
            messageType="workflow_identifier",
            data=IncomingData(jobId="job-err", file_upload_path="https://example.com/input.zip", user_id="user-1"),
        )
        # Must not raise
        service.send_status(
            valid=True,
            status_message="ok",
            request_message=request_msg,
            sanitization_dataset_url="",
            metadata_url="",
            original_file_upload_path="https://example.com/input.zip",
        )

    # ── download_input_file ────────────────────────────────────────────────────

    @patch("src.services.sanitization_service.shutil.copyfileobj")
    @patch("src.services.sanitization_service.urlrequest.urlopen")
    def test_download_input_file_http_success(self, mock_urlopen, mock_copyfileobj):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service_without_init()
            service._config = MagicMock()
            service._config.get_download_directory.return_value = temp_dir

            mock_response = MagicMock()
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            result = service.download_input_file("https://example.com/archive.zip", "job-dl")

            self.assertIn("archive.zip", result)
            mock_urlopen.assert_called_once()

    @patch("src.services.sanitization_service.shutil.copy")
    def test_download_input_file_local_path(self, mock_copy):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service_without_init()
            service._config = MagicMock()
            service._config.get_download_directory.return_value = temp_dir

            result = service.download_input_file("/local/path/archive.zip", "job-local")

            mock_copy.assert_called_once()
            self.assertIn("archive.zip", result)

    @patch("src.services.sanitization_service.urlrequest.urlopen")
    def test_download_input_file_raises_on_http_error(self, mock_urlopen):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service_without_init()
            service._config = MagicMock()
            service._config.get_download_directory.return_value = temp_dir

            mock_urlopen.side_effect = HTTPError("http://x", 404, "Not Found", {}, None)

            with self.assertRaises(RuntimeError) as ctx:
                service.download_input_file("https://example.com/archive.zip", "job-err")

            self.assertIn("HTTP download failed", str(ctx.exception))

    @patch("src.services.sanitization_service.urlrequest.urlopen")
    def test_download_input_file_raises_on_url_error(self, mock_urlopen):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service_without_init()
            service._config = MagicMock()
            service._config.get_download_directory.return_value = temp_dir

            mock_urlopen.side_effect = URLError("connection refused")

            with self.assertRaises(RuntimeError) as ctx:
                service.download_input_file("https://example.com/archive.zip", "job-err")

            self.assertIn("URL download failed", str(ctx.exception))

    @patch("src.services.sanitization_service.urlrequest.urlopen")
    def test_download_input_file_raises_on_generic_error(self, mock_urlopen):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._build_service_without_init()
            service._config = MagicMock()
            service._config.get_download_directory.return_value = temp_dir

            mock_urlopen.side_effect = Exception("timeout")

            with self.assertRaises(RuntimeError) as ctx:
                service.download_input_file("https://example.com/archive.zip", "job-err")

            self.assertIn("Failed to download", str(ctx.exception))

    # ── upload_to_azure ───────────────────────────────────────────────────────

    def test_upload_to_azure_success(self):
        service = self._build_service_without_init()
        mock_file = MagicMock()
        mock_file.get_remote_url.return_value = "https://blob/jobs/job-5/input.zip"
        mock_container = MagicMock()
        mock_container.create_file.return_value = mock_file
        service.storage_client = MagicMock()
        service.storage_client.get_container.return_value = mock_container

        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = os.path.join(temp_dir, "input.zip")
            with open(local_file, "wb") as f:
                f.write(b"zip-data")
            result = service.upload_to_azure(job_id="job-5", file_path=local_file)

        self.assertEqual(result, "https://blob/jobs/job-5/input.zip")
        mock_container.create_file.assert_called_once_with(name="jobs/job-5/input.zip")

    def test_upload_to_azure_returns_none_on_exception(self):
        service = self._build_service_without_init()
        service.storage_client = MagicMock()
        service.storage_client.get_container.side_effect = Exception("storage error")

        result = service.upload_to_azure(job_id="job-6", file_path="/nonexistent.zip")

        self.assertIsNone(result)

    # ── upload_fixes_json ─────────────────────────────────────────────────────

    def test_upload_fixes_json_success(self):
        service = self._build_service_without_init()
        mock_file = MagicMock()
        mock_file.get_remote_url.return_value = "https://blob/jobs/job-7/fixes.json"
        mock_container = MagicMock()
        mock_container.create_file.return_value = mock_file
        service.storage_client = MagicMock()
        service.storage_client.get_container.return_value = mock_container

        with tempfile.TemporaryDirectory() as temp_dir:
            fixes_path = os.path.join(temp_dir, "fixes.json")
            with open(fixes_path, "w") as f:
                json.dump({"jobId": "job-7"}, f)
            result = service.upload_fixes_json(job_id="job-7", fixes_file_path=fixes_path)

        self.assertEqual(result, "https://blob/jobs/job-7/fixes.json")
        mock_container.create_file.assert_called_once_with(name="jobs/job-7/fixes.json")

    def test_upload_fixes_json_returns_none_on_exception(self):
        service = self._build_service_without_init()
        service.storage_client = MagicMock()
        service.storage_client.get_container.side_effect = Exception("storage error")

        result = service.upload_fixes_json(job_id="job-8", fixes_file_path="/nonexistent/fixes.json")

        self.assertIsNone(result)

    # ── cleanup ───────────────────────────────────────────────────────────────

    def test_cleanup_removes_existing_directory(self):
        service = self._build_service_without_init()
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = os.path.join(temp_dir, "job-work")
            os.makedirs(work_dir)
            service.cleanup(path=work_dir)
            self.assertFalse(os.path.exists(work_dir))

    def test_cleanup_handles_nonexistent_path(self):
        service = self._build_service_without_init()
        # Must not raise
        service.cleanup(path="/nonexistent/job-work-xyz")

    # ── stop_listening ────────────────────────────────────────────────────────

    def test_stop_listening(self):
        service = self._build_service_without_init()
        service._stop_server_and_container = MagicMock()
        service.listening_thread = MagicMock()

        service.stop_listening()

        service._stop_server_and_container.assert_called_once()
        service.listening_thread.join.assert_called_once_with(timeout=0)

    # ── _stop_server_and_container ────────────────────────────────────────────

    def test_stop_server_and_container_skips_when_already_triggered(self):
        service = self._build_service_without_init()
        service._shutdown_triggered = threading.Event()
        service._shutdown_triggered.set()

        # Must return immediately without spawning another thread
        with patch("src.services.sanitization_service.threading.Thread") as mock_thread:
            service._stop_server_and_container()
            mock_thread.assert_not_called()

    @patch("src.services.sanitization_service.threading.Thread")
    @patch("src.services.sanitization_service.os._exit")
    @patch("src.services.sanitization_service.os.kill")
    @patch("src.services.sanitization_service.os.getppid", return_value=9999)
    @patch("src.services.sanitization_service.psutil.Process")
    def test_stop_server_and_container_sends_sigterm(
        self, mock_psutil, mock_getppid, mock_kill, mock_exit, mock_thread
    ):
        service = self._build_service_without_init()
        service._shutdown_triggered = threading.Event()

        captured = {}
        mock_thread.side_effect = lambda *a, **kw: captured.update(kw) or MagicMock()

        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["python", "app.py"]
        mock_psutil.return_value = mock_proc

        service._stop_server_and_container(delay_seconds=0)
        if "target" in captured:
            captured["target"]()

        mock_exit.assert_called_once_with(0)
        mock_kill.assert_called()

    @patch("src.services.sanitization_service.threading.Thread")
    @patch("src.services.sanitization_service.os._exit")
    @patch("src.services.sanitization_service.os.kill")
    @patch("src.services.sanitization_service.os.getppid", return_value=9999)
    @patch("src.services.sanitization_service.psutil.Process")
    def test_stop_server_kills_uvicorn_parent(
        self, mock_psutil, mock_getppid, mock_kill, mock_exit, mock_thread
    ):
        service = self._build_service_without_init()
        service._shutdown_triggered = threading.Event()

        captured = {}
        mock_thread.side_effect = lambda *a, **kw: captured.update(kw) or MagicMock()

        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["uvicorn", "src.main:app"]
        mock_psutil.return_value = mock_proc

        service._stop_server_and_container(delay_seconds=0)
        if "target" in captured:
            captured["target"]()

        self.assertEqual(mock_kill.call_count, 2)

    @patch("src.services.sanitization_service.threading.Thread")
    @patch("src.services.sanitization_service.os._exit")
    @patch("src.services.sanitization_service.os.kill")
    @patch("src.services.sanitization_service.os.getppid", return_value=9999)
    @patch("src.services.sanitization_service.psutil.Process")
    def test_stop_server_handles_parent_process_lookup_error(
        self, mock_psutil, mock_getppid, mock_kill, mock_exit, mock_thread
    ):
        service = self._build_service_without_init()
        service._shutdown_triggered = threading.Event()

        captured = {}
        mock_thread.side_effect = lambda *a, **kw: captured.update(kw) or MagicMock()
        mock_psutil.side_effect = Exception("no such process")

        service._stop_server_and_container(delay_seconds=0)
        if "target" in captured:
            captured["target"]()

        mock_exit.assert_called_once_with(0)

    @patch("src.services.sanitization_service.threading.Thread")
    @patch("src.services.sanitization_service.os._exit")
    @patch("src.services.sanitization_service.os.kill")
    @patch("src.services.sanitization_service.os.getppid", return_value=9999)
    @patch("src.services.sanitization_service.psutil.Process")
    @patch("src.services.sanitization_service.time.sleep")
    def test_stop_server_sleeps_when_delay_is_nonzero(
        self, mock_sleep, mock_psutil, mock_getppid, mock_kill, mock_exit, mock_thread
    ):
        service = self._build_service_without_init()
        service._shutdown_triggered = threading.Event()

        captured = {}
        mock_thread.side_effect = lambda *a, **kw: captured.update(kw) or MagicMock()
        mock_psutil.return_value = MagicMock()
        mock_psutil.return_value.cmdline.return_value = []

        service._stop_server_and_container(delay_seconds=5)
        if "target" in captured:
            captured["target"]()

        mock_sleep.assert_called_once_with(5)

    @patch("src.services.sanitization_service.threading.Thread")
    @patch("src.services.sanitization_service.os._exit")
    @patch("src.services.sanitization_service.os.kill")
    @patch("src.services.sanitization_service.os.getppid", return_value=9999)
    @patch("src.services.sanitization_service.psutil.Process")
    def test_stop_server_handles_kill_exception(
        self, mock_psutil, mock_getppid, mock_kill, mock_exit, mock_thread
    ):
        service = self._build_service_without_init()
        service._shutdown_triggered = threading.Event()

        captured = {}
        mock_thread.side_effect = lambda *a, **kw: captured.update(kw) or MagicMock()
        mock_psutil.return_value = MagicMock()
        mock_psutil.return_value.cmdline.return_value = []
        mock_kill.side_effect = Exception("permission denied")

        service._stop_server_and_container(delay_seconds=0)
        if "target" in captured:
            captured["target"]()

        mock_exit.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
