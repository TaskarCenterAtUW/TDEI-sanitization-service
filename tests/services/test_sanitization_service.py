import unittest
from unittest.mock import MagicMock, patch

from src.models.queue_message_content import IncomingData, RequestMessage
from src.services.sanitization_service import SanitizationService


class TestSanitizationService(unittest.TestCase):
    def _build_service_without_init(self):
        service = SanitizationService.__new__(SanitizationService)
        service.core = MagicMock()
        service.container_name = "osw"
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
                file_upload_path="https://example.com/input.zip",
                user_id="user-1",
                tdei_project_group_id="group-1",
            ),
        )

        queue_message_mock.data_from.side_effect = lambda payload: payload

        service.send_status(
            valid=True,
            status_message="",
            request_message=request_msg,
            sanitized_dataset_url="https://example.com/sanitized.zip",
            original_file_upload_path="https://example.com/input.zip",
            metadata={"sanitized_features": ["feature_a"], "sanitized_tags": ["tag_x"]},
        )

        queue_message_mock.data_from.assert_called_once()
        payload = queue_message_mock.data_from.call_args[0][0]
        self.assertEqual(payload["messageId"], "mid-1")
        self.assertEqual(payload["messageType"], "workflow_identifier")
        self.assertTrue(payload["data"]["success"])
        self.assertEqual(
            payload["data"]["sanitization_dataset_url"],
            "https://example.com/sanitized.zip",
        )
        self.assertEqual(payload["data"]["metadata"]["sanitized_features"], ["feature_a"])
        self.assertEqual(payload["data"]["metadata"]["sanitized_tags"], ["tag_x"])

        response_topic.publish.assert_called_once_with(data=payload)

    def test_process_message_handles_missing_data_and_sends_failure(self):
        service = self._build_service_without_init()
        service.cleanup = MagicMock()
        service.send_status = MagicMock()

        request_msg = RequestMessage(
            messageId="mid-2",
            messageType="workflow_identifier",
            data=None,
        )

        service.process_message(request_msg)

        self.assertTrue(service.send_status.called)
        kwargs = service.send_status.call_args.kwargs
        self.assertFalse(kwargs["valid"])
        self.assertIn("missing data", kwargs["status_message"].lower())

    def test_subscribe_passes_max_receivable_messages(self):
        service = self._build_service_without_init()
        service._subscription_name = "sub-1"
        service.request_topic = MagicMock()
        service._config = MagicMock()
        service._config.max_receivable_messages = 3
        service._stop_server_and_container = MagicMock()

        service.subscribe()

        self.assertTrue(service.request_topic.subscribe.called)
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


if __name__ == "__main__":
    unittest.main()
