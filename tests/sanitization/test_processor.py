import unittest
from unittest.mock import patch

from osw_sanitizer import SanitizationConfig

from src.sanitization.processor import SanitizationProcessor


class TestSanitizationProcessor(unittest.TestCase):
    @patch("src.sanitization.processor.OSWSanitization")
    def test_sanitize_dataset_delegates_to_osw_sanitizer_package(self, sanitizer_mock):
        sanitizer_mock.sanitize_dataset.return_value = {
            "success": True,
            "message": "No changes were needed. The dataset is already clean.",
            "updated_dataset_zip": "/tmp/output/input.zip",
            "fixes_json": "/tmp/output/fixes.json",
        }

        result = SanitizationProcessor.sanitize_dataset(
            input_zip_path="/tmp/input.zip",
            output_dir="/tmp/output",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["updated_dataset_zip"], "/tmp/output/input.zip")
        sanitizer_mock.sanitize_dataset.assert_called_once_with(
            input_zip_path="/tmp/input.zip",
            output_dir="/tmp/output",
            config=None,
        )

    @patch("src.sanitization.processor.OSWSanitization")
    def test_sanitize_dataset_forwards_config_to_osw_sanitizer_package(self, sanitizer_mock):
        config = SanitizationConfig(
            coordinate_precision=7,
            max_geometry_vertices=2000,
            allow_zero_length_lines=False,
        )
        sanitizer_mock.sanitize_dataset.return_value = {
            "success": True,
            "message": "Dataset was sanitized for OSW compliance.",
            "updated_dataset_zip": "/tmp/output/input.zip",
            "fixes_json": "/tmp/output/fixes.json",
        }

        SanitizationProcessor.sanitize_dataset(
            input_zip_path="/tmp/input.zip",
            output_dir="/tmp/output",
            config=config,
        )

        sanitizer_mock.sanitize_dataset.assert_called_once_with(
            input_zip_path="/tmp/input.zip",
            output_dir="/tmp/output",
            config=config,
        )


if __name__ == "__main__":
    unittest.main()
