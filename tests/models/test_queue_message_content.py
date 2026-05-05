import unittest

from src.models.queue_message_content import IncomingData, RequestMessage


class TestQueueMessageContent(unittest.TestCase):
    def test_from_dict_parses_expected_fields(self):
        payload = {
            "messageId": "mid-1",
            "messageType": "workflow_identifier",
            "data": {
                "jobId": "job-1",
                "file_upload_path": "https://example.com/archive.zip",
                "user_id": "user-1",
            },
        }

        result = RequestMessage.from_dict(payload)

        self.assertEqual(result.messageId, "mid-1")
        self.assertEqual(result.messageType, "workflow_identifier")
        self.assertIsInstance(result.data, IncomingData)
        self.assertEqual(result.data.jobId, "job-1")
        self.assertEqual(result.data.file_upload_path, "https://example.com/archive.zip")
        self.assertEqual(result.data.user_id, "user-1")

    def test_from_dict_handles_missing_data(self):
        payload = {"messageId": "mid-2", "messageType": "workflow_identifier"}

        result = RequestMessage.from_dict(payload)

        self.assertEqual(result.messageId, "mid-2")
        self.assertEqual(result.messageType, "workflow_identifier")
        self.assertIsNone(result.data)


if __name__ == "__main__":
    unittest.main()
