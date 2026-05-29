import json
import math
import os
import tempfile
import unittest
import zipfile

from src.sanitization.processor import SanitizationProcessor


class TestSanitizationProcessor(unittest.TestCase):
    def test_sanitize_dataset_returns_error_for_missing_input_path(self):
        result = SanitizationProcessor.sanitize_dataset("", "/tmp/work")

        self.assertFalse(result["success"])
        self.assertIn("missing", result["message"].lower())
        self.assertIsNone(result["updated_dataset_zip"])
        self.assertIsNone(result["metadata_json"])

    def test_geojson_with_null_tags(self):
        payload = self._feature_collection(
            properties={"name": "edge", "width": None},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertNotIn("width", sanitized_payload["features"][0]["properties"])
        self.assertEqual(result["message"], "Invalid or empty values were removed from the dataset.")
        self.assertEqual(metadata["files"][0]["removedTags"][0]["tag"], "width")

    def test_geojson_with_nan_tags(self):
        payload = self._feature_collection(
            properties={"name": "edge", "slope": float("nan")},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertNotIn("slope", sanitized_payload["features"][0]["properties"])
        self.assertEqual(result["message"], "Invalid or empty values were removed from the dataset.")
        self.assertTrue(math.isnan(metadata["files"][0]["removedTags"][0]["value"]))

    def test_geojson_with_coordinates_having_more_than_7_decimals(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.123456789, 47.123456789],
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(
            sanitized_payload["features"][0]["geometry"]["coordinates"],
            [-122.1234567, 47.1234567],
        )
        self.assertEqual(result["message"], "Coordinates were standardized for consistency.")
        self.assertEqual(len(metadata["files"][0]["precisionUpdates"]), 2)

    def test_geojson_with_coordinates_having_fewer_than_7_decimals(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1, 47.1234],
        )

        result, sanitized_payload, metadata, geojson_text = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(
            sanitized_payload["features"][0]["geometry"]["coordinates"],
            [-122.1, 47.1234],
        )
        self.assertEqual(result["message"], "Coordinates were standardized for consistency.")
        self.assertEqual(len(metadata["files"][0]["precisionUpdates"]), 2)
        self.assertEqual(metadata["files"][0]["precisionUpdates"][0]["original"], "-122.1")
        self.assertEqual(metadata["files"][0]["precisionUpdates"][0]["updated"], "-122.1000000")
        self.assertEqual(metadata["files"][0]["precisionUpdates"][1]["original"], "47.1234")
        self.assertEqual(metadata["files"][0]["precisionUpdates"][1]["updated"], "47.1234000")
        self.assertIn("-122.1000000", geojson_text)
        self.assertIn("47.1234000", geojson_text)

    def test_geojson_with_no_sanitization_needed(self):
        payload = self._feature_collection(
            properties={"name": "edge", "width": 2},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, _, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(result["message"], "No changes were needed. The dataset is already clean.")
        self.assertEqual(metadata["files"][0]["removedTags"], [])
        self.assertEqual(metadata["files"][0]["precisionUpdates"], [])

    def test_metadata_file_creation(self):
        payload = self._feature_collection(
            properties={"name": "edge", "width": None},
            coordinates=[-122.123456789, 47.1234567],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, payload)

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(os.path.isfile(result["metadata_json"]))
            with open(result["metadata_json"], "r", encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)
            self.assertIn("files", metadata)
            self.assertEqual(metadata["jobId"], "")

    def test_zip_generation(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1234567, 47.1234567],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, payload)

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(os.path.isfile(result["updated_dataset_zip"]))
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zip_file:
                self.assertIn("edges.geojson", zip_file.namelist())

    def test_geojson_with_cp1252_encoding(self):
        payload = self._feature_collection(
            properties={"name": "Bench \u00a2"},
            coordinates=[-122.1234567, 47.1234567],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, payload, encoding="cp1252")

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(result["success"])
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zip_file:
                with zip_file.open("edges.geojson") as geojson_file:
                    sanitized_payload = json.load(geojson_file)
            self.assertEqual(sanitized_payload["features"][0]["properties"]["name"], "Bench \u00a2")

    def test_ignores_macosx_resource_files(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1234567, 47.1234567],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_macosx_sidecar(input_zip_path, payload)

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(result["success"])
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zip_file:
                names = zip_file.namelist()
            self.assertIn("edges.geojson", names)
            self.assertNotIn("__MACOSX/._edges.geojson", names)

    def test_sanitize_dataset_returns_error_for_nonexistent_file(self):
        result = SanitizationProcessor.sanitize_dataset("/nonexistent/path/archive.zip", "/tmp/work")

        self.assertFalse(result["success"])
        self.assertIn("not found", result["message"].lower())
        self.assertIsNone(result["updated_dataset_zip"])
        self.assertIsNone(result["metadata_json"])

    def test_sanitize_dataset_copies_non_geojson_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            with zipfile.ZipFile(input_zip_path, "w") as zf:
                zf.writestr("readme.txt", "hello world")
            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)
            self.assertTrue(result["success"])
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zf:
                self.assertIn("readme.txt", zf.namelist())

    def test_sanitize_dataset_skips_macos_dotfiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            payload = self._feature_collection(properties={"name": "edge"}, coordinates=[-122.1234567, 47.1234567])
            with zipfile.ZipFile(input_zip_path, "w") as zf:
                zf.writestr("edges.geojson", json.dumps(payload))
                zf.writestr(".DS_Store", b"mac junk".decode())
                zf.writestr("._edges.geojson", b"resource fork".decode())
            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)
            self.assertTrue(result["success"])
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zf:
                names = zf.namelist()
            self.assertNotIn(".DS_Store", names)
            self.assertNotIn("._edges.geojson", names)

    def test_sanitize_dataset_handles_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_zip = os.path.join(temp_dir, "bad.zip")
            with open(bad_zip, "wb") as f:
                f.write(b"not a zip file")
            result = SanitizationProcessor.sanitize_dataset(input_zip_path=bad_zip, output_dir=os.path.join(temp_dir, "out"))
            self.assertFalse(result["success"])
            self.assertIn("sanitization failed", result["message"].lower())

    def test_sanitize_dataset_descends_into_single_nested_dir(self):
        payload = self._feature_collection(properties={"name": "edge"}, coordinates=[-122.1234567, 47.1234567])
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            with zipfile.ZipFile(input_zip_path, "w") as zf:
                zf.writestr("dataset/edges.geojson", json.dumps(payload))
            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)
            self.assertTrue(result["success"])
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zf:
                self.assertIn("edges.geojson", zf.namelist())

    def test_geojson_with_none_string_property_preserved(self):
        self._assert_string_property_preserved("None")

    def test_geojson_with_nan_string_property_preserved(self):
        self._assert_string_property_preserved("nan")

    def test_geojson_with_none_lowercase_string_property_preserved(self):
        self._assert_string_property_preserved("none")

    def test_geojson_with_null_string_property_preserved(self):
        self._assert_string_property_preserved("null")

    def test_geojson_with_n_a_string_property_preserved(self):
        self._assert_string_property_preserved("n/a")

    def test_geojson_with_na_string_property_preserved(self):
        self._assert_string_property_preserved("na")

    def test_sanitize_coordinates_ignores_non_numeric_non_list(self):
        updates = []
        result = SanitizationProcessor._sanitize_coordinates("string-coord", 0, "geometry.coordinates", updates)
        self.assertEqual(result, "string-coord")
        self.assertEqual(updates, [])

    def test_resolve_dataset_root_descends_into_single_subdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subdir = os.path.join(temp_dir, "dataset")
            os.makedirs(subdir)
            result = SanitizationProcessor._resolve_dataset_root(temp_dir)
            self.assertEqual(result, subdir)

    def test_load_geojson_payload_raises_when_all_encodings_fail(self):
        from unittest.mock import patch
        with patch("builtins.open", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad encoding")):
            with self.assertRaises(UnicodeDecodeError):
                SanitizationProcessor._load_geojson_payload("/fake/path.geojson")

    def _run_sanitizer(self, geojson_payload):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, geojson_payload)

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(result["success"])

            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zip_file:
                with zip_file.open("edges.geojson") as geojson_file:
                    raw_text = geojson_file.read().decode("utf-8")
                    sanitized_payload = json.loads(raw_text)

            with open(result["metadata_json"], "r", encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)

            return result, sanitized_payload, metadata, raw_text

    def _assert_string_property_preserved(self, string_value):
        payload = self._feature_collection(
            properties={"name": "edge", "bad_tag": string_value},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertEqual(sanitized_payload["features"][0]["properties"]["bad_tag"], string_value)
        self.assertEqual(result["message"], "No changes were needed. The dataset is already clean.")
        self.assertEqual(metadata["files"][0]["removedTags"], [])

    @staticmethod
    def _feature_collection(properties, coordinates):
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": {
                        "type": "Point",
                        "coordinates": coordinates,
                    },
                }
            ],
        }

    @staticmethod
    def _write_zip_with_geojson(zip_path, payload, encoding="utf-8"):
        with tempfile.TemporaryDirectory() as staging_dir:
            geojson_path = os.path.join(staging_dir, "edges.geojson")
            with open(geojson_path, "w", encoding=encoding) as geojson_file:
                json.dump(payload, geojson_file, allow_nan=True, ensure_ascii=False)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(geojson_path, "edges.geojson")

    @staticmethod
    def _write_zip_with_macosx_sidecar(zip_path, payload):
        with tempfile.TemporaryDirectory() as staging_dir:
            geojson_path = os.path.join(staging_dir, "edges.geojson")
            with open(geojson_path, "w", encoding="utf-8") as geojson_file:
                json.dump(payload, geojson_file, allow_nan=True, ensure_ascii=False)

            sidecar_path = os.path.join(staging_dir, "._edges.geojson")
            with open(sidecar_path, "wb") as sidecar_file:
                sidecar_file.write(b"\x00\x05\x16\x07Mac OS X resource fork")

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(geojson_path, "edges.geojson")
                zip_file.write(sidecar_path, "__MACOSX/._edges.geojson")


if __name__ == "__main__":
    unittest.main()
