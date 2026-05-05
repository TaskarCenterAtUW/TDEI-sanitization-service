import os
import tempfile
import unittest

from src.sanitization.processor import SanitizationProcessor


class TestSanitizationProcessor(unittest.TestCase):
    def test_sanitize_dataset_returns_error_for_missing_input_path(self):
        success, message, output_path, metadata = SanitizationProcessor.sanitize_dataset("", "/tmp/work")

        self.assertFalse(success)
        self.assertIn("missing", message.lower())
        self.assertIsNone(output_path)
        self.assertEqual(metadata, {})

    def test_sanitize_dataset_placeholder_returns_success_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = os.path.join(temp_dir, "input.zip")
            work_dir = os.path.join(temp_dir, "work")

            with open(input_file, "wb") as f:
                f.write(b"zip-data")

            success, message, output_path, metadata = SanitizationProcessor.sanitize_dataset(input_file, work_dir)

            self.assertTrue(success)
            self.assertIn("completed", message.lower())
            self.assertIsNotNone(output_path)
            self.assertTrue(os.path.isfile(output_path))
            self.assertIn("sanitized_features", metadata)
            self.assertIn("sanitized_tags", metadata)


if __name__ == "__main__":
    unittest.main()
