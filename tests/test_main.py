import asyncio
import unittest
from unittest.mock import MagicMock, patch

from src.main import app, get_settings, ping, root, shutdown_event, startup_event


class TestMain(unittest.TestCase):
    def test_root_health(self):
        self.assertEqual(root(), "I'm healthy !!")

    def test_ping_health(self):
        self.assertEqual(ping(), "I'm healthy !!")

    def test_get_settings_returns_settings_instance(self):
        settings = get_settings()
        self.assertIsNotNone(settings)

    @patch("src.main.SanitizationService")
    @patch("src.main.os.path.exists", return_value=True)
    def test_startup_event_creates_service(self, _mock_exists, mock_service_cls):
        mock_service_cls.return_value = MagicMock()
        asyncio.run(startup_event())
        mock_service_cls.assert_called_once()
        self.assertIsNotNone(app.sanitization_service)

    @patch("src.main.SanitizationService")
    @patch("src.main.os.makedirs")
    @patch("src.main.os.path.exists", return_value=False)
    def test_startup_event_creates_download_directory(self, _mock_exists, mock_makedirs, mock_service_cls):
        mock_service_cls.return_value = MagicMock()
        asyncio.run(startup_event())
        mock_makedirs.assert_called_once()

    @patch("src.main.psutil.Process")
    @patch("src.main.SanitizationService", side_effect=Exception("bad config"))
    @patch("src.main.os.path.exists", return_value=True)
    def test_startup_event_handles_exception_without_raising(self, _mock_exists, _mock_service, mock_process_cls):
        mock_proc = MagicMock()
        mock_proc.children.return_value = []
        mock_process_cls.return_value = mock_proc
        asyncio.run(startup_event())

    @patch("src.main.psutil.Process")
    @patch("src.main.SanitizationService", side_effect=Exception("bad config"))
    @patch("src.main.os.path.exists", return_value=True)
    def test_startup_event_kills_child_processes_on_exception(self, _mock_exists, _mock_service, mock_process_cls):
        mock_child = MagicMock()
        mock_proc = MagicMock()
        mock_proc.children.return_value = [mock_child]
        mock_process_cls.return_value = mock_proc
        asyncio.run(startup_event())
        mock_child.kill.assert_called_once()

    def test_shutdown_event_calls_stop_listening_when_service_set(self):
        mock_service = MagicMock()
        app.sanitization_service = mock_service
        asyncio.run(shutdown_event())
        mock_service.stop_listening.assert_called_once()

    def test_shutdown_event_is_safe_when_no_service(self):
        app.sanitization_service = None
        asyncio.run(shutdown_event())


if __name__ == "__main__":
    unittest.main()
