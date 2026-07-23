import json
import math
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from src.config import SanitizationConfig
from src.sanitization.processor import SanitizationProcessor


class TestSanitizationProcessor(unittest.TestCase):
    def test_sanitize_dataset_returns_error_for_missing_input_path(self):
        result = SanitizationProcessor.sanitize_dataset("", "/tmp/work")

        self.assertFalse(result["success"])
        self.assertIn("missing", result["message"].lower())
        self.assertIsNone(result["updated_dataset_zip"])
        self.assertIsNone(result["fixes_json"])

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

    def test_nan_coordinate_fails_with_located_message(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[float("nan"), 47.1234567],
        )

        result = self._run_failing_sanitizer(payload)

        self.assertFalse(result["success"])
        self.assertIsNone(result["updated_dataset_zip"])
        self.assertIsNone(result["fixes_json"])
        self.assertIn("edges.geojson", result["message"])
        self.assertIn("feature 0", result["message"])
        self.assertIn("coordinates[0]", result["message"])
        self.assertIn("NaN", result["message"])

    def test_infinity_coordinate_fails_with_located_message(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[[-122.1234567, 47.1234567], [float("inf"), 47.0]],
        )

        result = self._run_failing_sanitizer(payload)

        self.assertFalse(result["success"])
        self.assertIn("edges.geojson", result["message"])
        self.assertIn("coordinates[1][0]", result["message"])
        self.assertIn("Infinity", result["message"])

    def test_infinity_property_fails_with_located_message(self):
        payload = self._feature_collection(
            properties={"name": "edge", "speed": float("inf")},
            coordinates=[-122.1234567, 47.1234567],
        )

        result = self._run_failing_sanitizer(payload)

        self.assertFalse(result["success"])
        self.assertIsNone(result["updated_dataset_zip"])
        self.assertIn("edges.geojson", result["message"])
        self.assertIn("speed", result["message"])
        self.assertIn("Infinity", result["message"])

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
        precision_update = metadata["files"][0]["precisionUpdates"][0]
        self.assertEqual(precision_update["coordinatePath"], "coordinates[0]")
        self.assertEqual(precision_update["original"], "-122.123456789")
        self.assertEqual(precision_update["updated"], "-122.1234567")
        self.assertEqual(metadata["files"][0]["precisionUpdates"][1]["coordinatePath"], "coordinates[1]")
        self.assertNotIn("geometry.coordinates", json.dumps(metadata))
        # Only the non-empty change list is included for the file entry.
        self.assertNotIn("removedTags", metadata["files"][0])

    def test_fixes_entry_omits_empty_precision_updates(self):
        payload = self._feature_collection(
            properties={"name": "edge", "width": None},
            coordinates=[-122.1234567, 47.1234567],
        )

        _, _, metadata, _ = self._run_sanitizer(payload)

        file_entry = metadata["files"][0]
        self.assertIn("removedTags", file_entry)
        self.assertNotIn("precisionUpdates", file_entry)

    def test_geojson_with_coordinates_having_fewer_than_7_decimals(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1, 47.1234],
        )

        result, sanitized_payload, metadata, geojson_text = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        # Coordinates with fewer than 7 decimals keep their original precision;
        # no trailing zeros are appended and no precision update is recorded.
        self.assertEqual(
            sanitized_payload["features"][0]["geometry"]["coordinates"],
            [-122.1, 47.1234],
        )
        self.assertEqual(result["message"], "No changes were needed. The dataset is already clean.")
        self.assertEqual(metadata["files"], [])
        self.assertNotIn("-122.1000000", geojson_text)
        self.assertNotIn("47.1234000", geojson_text)
        self.assertIn("-122.1", geojson_text)
        self.assertIn("47.1234", geojson_text)

    def test_geojson_coordinate_with_exactly_7_decimals_is_unchanged(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(
            sanitized_payload["features"][0]["geometry"]["coordinates"],
            [-122.1234567, 47.1234567],
        )
        self.assertEqual(metadata["files"], [])

    def test_truncated_coordinate_drops_trailing_zeros(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.10000009, 47.1234567],
        )

        result, sanitized_payload, metadata, geojson_text = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        # 8 decimals -> truncate to 7 (-122.1000000) -> strip trailing zeros.
        self.assertEqual(
            sanitized_payload["features"][0]["geometry"]["coordinates"][0],
            -122.1,
        )
        self.assertEqual(len(metadata["files"][0]["precisionUpdates"]), 1)
        self.assertEqual(metadata["files"][0]["precisionUpdates"][0]["updated"], "-122.1")
        self.assertNotIn("-122.1000000", geojson_text)

    def test_geojson_with_no_sanitization_needed(self):
        payload = self._feature_collection(
            properties={"name": "edge", "width": 2},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, _, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(result["message"], "No changes were needed. The dataset is already clean.")
        self.assertEqual(metadata["files"], [])

    def test_fixes_excludes_unchanged_files(self):
        changed = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.123456789, 47.1234567],
        )
        unchanged = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1234567, 47.1234567],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            with zipfile.ZipFile(input_zip_path, "w") as zf:
                zf.writestr("edges.geojson", json.dumps(changed))
                zf.writestr("zones.geojson", json.dumps(unchanged))

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(result["success"])
            with open(result["fixes_json"], "r", encoding="utf-8") as fixes_file:
                fixes = json.load(fixes_file)

        # Only the file with actual changes is recorded; the clean file is omitted.
        self.assertEqual([entry["filename"] for entry in fixes["files"]], ["edges.geojson"])

    def test_fixes_file_creation(self):
        payload = self._feature_collection(
            properties={"name": "edge", "width": None},
            coordinates=[-122.123456789, 47.1234567],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, payload)

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertEqual(os.path.basename(result["fixes_json"]), "fixes.json")
            self.assertTrue(os.path.isfile(result["fixes_json"]))
            self.assertFalse(os.path.isfile(os.path.join(output_dir, "metadata.json")))
            with open(result["fixes_json"], "r", encoding="utf-8") as fixes_file:
                fixes = json.load(fixes_file)
            self.assertIn("files", fixes)
            self.assertEqual(fixes["jobId"], "")

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
        self.assertIsNone(result["fixes_json"])

    def test_sanitize_dataset_removes_unsupported_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            with zipfile.ZipFile(input_zip_path, "w") as zf:
                zf.writestr("readme.txt", "hello world")
            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)
            self.assertTrue(result["success"])
            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zf:
                self.assertNotIn("readme.txt", zf.namelist())
            with open(result["fixes_json"], "r", encoding="utf-8") as fixes_file:
                fixes = json.load(fixes_file)
            self.assertEqual(fixes["removedFiles"][0]["filename"], "readme.txt")
            self.assertEqual(fixes["removedFiles"][0]["fixType"], "unsupported_file_removed")

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

    def test_zero_length_edge_is_removed_and_logged(self):
        payload = self._feature_collection(
            properties={"_id": "edge-1", "name": "edge"},
            coordinates=[[-122.1, 47.1], [-122.1, 47.1]],
            geometry_type="LineString",
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(sanitized_payload["features"], [])
        removed_edge = metadata["files"][0]["removedEdges"][0]
        self.assertEqual(removed_edge["featureId"], "edge-1")
        self.assertEqual(removed_edge["fixType"], "zero_length_edge_removed")
        self.assertEqual(removed_edge["threshold"], 0)

    def test_non_zero_length_edge_is_retained(self):
        payload = self._feature_collection(
            properties={"_id": "edge-1", "name": "edge"},
            coordinates=[[-122.1, 47.1], [-122.2, 47.2]],
            geometry_type="LineString",
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(len(sanitized_payload["features"]), 1)
        self.assertEqual(metadata["files"], [])

    def test_edge_with_exactly_configured_vertex_limit_is_not_split(self):
        payload = self._feature_collection(
            properties={"_id": "edge-1", "name": "edge"},
            coordinates=self._line_coordinates(5),
            geometry_type="LineString",
        )

        with patch("src.sanitization.processor.SanitizationConfig", return_value=SanitizationConfig(max_edge_vertices=5)):
            result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(len(sanitized_payload["features"]), 1)
        self.assertEqual(len(sanitized_payload["features"][0]["geometry"]["coordinates"]), 5)
        self.assertEqual(metadata["files"], [])

    def test_edge_over_configured_vertex_limit_is_split_and_logged(self):
        payload = self._feature_collection(
            properties={"_id": "edge-1", "_u_id": "node-u", "_v_id": "node-v", "name": "edge"},
            coordinates=self._line_coordinates(6),
            geometry_type="LineString",
        )

        with patch("src.sanitization.processor.SanitizationConfig", return_value=SanitizationConfig(max_edge_vertices=5)):
            result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        features = sanitized_payload["features"]
        self.assertEqual(len(features), 2)
        self.assertEqual(len(features[0]["geometry"]["coordinates"]), 5)
        self.assertEqual(len(features[1]["geometry"]["coordinates"]), 2)
        self.assertEqual(features[0]["properties"]["_id"], "edge-1-part-1")
        self.assertEqual(features[1]["properties"]["_id"], "edge-1-part-2")
        self.assertEqual(features[0]["properties"]["_u_id"], "node-u")
        self.assertEqual(features[0]["properties"]["_v_id"], "edge-1-split-node-1")
        self.assertEqual(features[1]["properties"]["_u_id"], "edge-1-split-node-1")
        self.assertEqual(features[1]["properties"]["_v_id"], "node-v")
        split_edge = metadata["files"][0]["splitEdges"][0]
        self.assertEqual(split_edge["featureId"], "edge-1")
        self.assertEqual(split_edge["originalVertexCount"], 6)
        self.assertEqual(split_edge["maxVertexCount"], 5)
        self.assertEqual(split_edge["splitCount"], 2)
        self.assertEqual(split_edge["generatedNodeIds"], ["edge-1-split-node-1"])
        nodes_payload = result["_zip_payloads"]["nodes.geojson"]
        self.assertEqual(nodes_payload["features"][0]["properties"]["_id"], "edge-1-split-node-1")
        self.assertEqual(nodes_payload["features"][0]["geometry"]["coordinates"], [4, 0.04])
        self.assertEqual(metadata["files"][1]["addedNodes"][0]["featureId"], "edge-1-split-node-1")

    def test_polygon_over_edge_vertex_limit_is_left_unchanged(self):
        payload = self._feature_collection(
            properties={"_id": "polygon-1", "name": "polygon"},
            coordinates=[self._line_coordinates(6) + [[0, 0]]],
            geometry_type="Polygon",
        )

        with patch("src.sanitization.processor.SanitizationConfig", return_value=SanitizationConfig(max_edge_vertices=5)):
            result, sanitized_payload, metadata, _ = self._run_sanitizer(payload, filename="polygons.geojson")

        self.assertTrue(result["success"])
        self.assertEqual(len(sanitized_payload["features"]), 1)
        self.assertEqual(len(sanitized_payload["features"][0]["geometry"]["coordinates"][0]), 7)
        self.assertEqual(metadata["files"], [])

    def test_configured_coordinate_precision_is_used(self):
        payload = self._feature_collection(
            properties={"name": "edge"},
            coordinates=[-122.1234567, 47.1234567],
        )

        with patch("src.sanitization.processor.SanitizationConfig", return_value=SanitizationConfig(coordinate_precision=6)):
            result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertTrue(result["success"])
        self.assertEqual(
            sanitized_payload["features"][0]["geometry"]["coordinates"],
            [-122.123456, 47.123456],
        )
        self.assertEqual(metadata["files"][0]["precisionUpdates"][0]["precision"], 6)

    def test_sanitize_coordinates_ignores_non_numeric_non_list(self):
        updates = []
        result = SanitizationProcessor._sanitize_coordinates(
            "string-coord",
            0,
            "coordinates",
            updates,
            SanitizationConfig(),
        )
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

    def _run_sanitizer(self, geojson_payload, filename="edges.geojson"):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, geojson_payload, filename=filename)

            result = SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

            self.assertTrue(result["success"])

            with zipfile.ZipFile(result["updated_dataset_zip"], "r") as zip_file:
                with zip_file.open(filename) as geojson_file:
                    raw_text = geojson_file.read().decode("utf-8")
                    sanitized_payload = json.loads(raw_text)
                result["_zip_payloads"] = {}
                if "nodes.geojson" in zip_file.namelist():
                    with zip_file.open("nodes.geojson") as nodes_file:
                        result["_zip_payloads"]["nodes.geojson"] = json.load(nodes_file)

            with open(result["fixes_json"], "r", encoding="utf-8") as fixes_file:
                metadata = json.load(fixes_file)

            return result, sanitized_payload, metadata, raw_text

    def _run_failing_sanitizer(self, geojson_payload):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip_path = os.path.join(temp_dir, "input.zip")
            output_dir = os.path.join(temp_dir, "output")
            self._write_zip_with_geojson(input_zip_path, geojson_payload)
            return SanitizationProcessor.sanitize_dataset(input_zip_path=input_zip_path, output_dir=output_dir)

    def _assert_string_property_preserved(self, string_value):
        payload = self._feature_collection(
            properties={"name": "edge", "bad_tag": string_value},
            coordinates=[-122.1234567, 47.1234567],
        )

        result, sanitized_payload, metadata, _ = self._run_sanitizer(payload)

        self.assertEqual(sanitized_payload["features"][0]["properties"]["bad_tag"], string_value)
        self.assertEqual(result["message"], "No changes were needed. The dataset is already clean.")
        self.assertEqual(metadata["files"], [])

    @staticmethod
    def _feature_collection(properties, coordinates, geometry_type="Point"):
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": {
                        "type": geometry_type,
                        "coordinates": coordinates,
                    },
                }
            ],
        }

    @staticmethod
    def _line_coordinates(count):
        return [[index, index * 0.01] for index in range(count)]

    @staticmethod
    def _write_zip_with_geojson(zip_path, payload, encoding="utf-8", filename="edges.geojson"):
        with tempfile.TemporaryDirectory() as staging_dir:
            geojson_path = os.path.join(staging_dir, "edges.geojson")
            with open(geojson_path, "w", encoding=encoding) as geojson_file:
                json.dump(payload, geojson_file, allow_nan=True, ensure_ascii=False)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(geojson_path, filename)

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
