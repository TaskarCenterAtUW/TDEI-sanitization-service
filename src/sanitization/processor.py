import gc
import json
import math
import os
import shutil
import zipfile
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List

import ijson

from src.logger import Logger


class DatasetValidationError(Exception):
    """
    Raised when a dataset contains a value that cannot be sanitized into valid
    GeoJSON (e.g. a NaN/Infinity coordinate, or a non-finite property value).
    Carries a human-readable location so the failure message points at the
    offending file, feature, and field.
    """


class SanitizationProcessor:
    COORDINATE_QUANTIZER = Decimal("0.0000000")
    GEOJSON_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")

    @classmethod
    def sanitize_dataset(cls, input_zip_path: str, output_dir: str) -> Dict[str, Any]:
        """
        Sanitize a dataset zip and create a sanitized zip plus a fixes json.
        """
        try:
            if not input_zip_path:
                return cls._failure("Input dataset path is missing")

            if not os.path.isfile(input_zip_path):
                return cls._failure(f"Input dataset not found at path: {input_zip_path}")

            os.makedirs(output_dir, exist_ok=True)
            extraction_dir = os.path.join(output_dir, "extracted")
            sanitized_root_dir = os.path.join(output_dir, "sanitized")
            os.makedirs(extraction_dir, exist_ok=True)
            os.makedirs(sanitized_root_dir, exist_ok=True)

            with zipfile.ZipFile(input_zip_path, "r") as zip_file:
                zip_file.extractall(extraction_dir)

            dataset_root = cls._resolve_dataset_root(extraction_dir)
            fixes: Dict[str, Any] = {
                "jobId": "",
                "files": [],
            }
            change_summary = {
                "removed_values": False,
                "precision_updates": False,
            }

            for current_root, _, files in os.walk(dataset_root):
                relative_root = os.path.relpath(current_root, dataset_root)
                if cls._should_skip_relative_path(relative_root):
                    continue
                sanitized_root = sanitized_root_dir if relative_root == "." else os.path.join(sanitized_root_dir, relative_root)
                os.makedirs(sanitized_root, exist_ok=True)

                for filename in files:
                    if cls._should_skip_filename(filename):
                        continue
                    source_path = os.path.join(current_root, filename)
                    target_path = os.path.join(sanitized_root, filename)
                    if filename.lower().endswith(".geojson"):
                        with Logger.timer(f"sanitize_geojson_file ({filename})"):
                            file_metadata = cls._sanitize_geojson_file(source_path, target_path)
                        # Only record files that actually changed; skipping
                        # untouched files keeps fixes.json free of empty entries.
                        removed_tags = file_metadata["removedTags"]
                        precision_updates = file_metadata["precisionUpdates"]
                        if removed_tags:
                            change_summary["removed_values"] = True
                        if precision_updates:
                            change_summary["precision_updates"] = True
                        if removed_tags or precision_updates:
                            file_entry: Dict[str, Any] = {"filename": file_metadata["filename"]}
                            if removed_tags:
                                file_entry["removedTags"] = removed_tags
                            if precision_updates:
                                file_entry["precisionUpdates"] = precision_updates
                            fixes["files"].append(file_entry)
                    else:
                        shutil.copy2(source_path, target_path)

            fixes_json_path = os.path.join(output_dir, "fixes.json")
            with open(fixes_json_path, "w", encoding="utf-8") as fixes_file:
                json.dump(fixes, fixes_file, indent=2, ensure_ascii=True)

            input_filename = os.path.basename(input_zip_path)
            updated_dataset_zip = os.path.join(output_dir, input_filename)
            cls._create_zip_from_directory(sanitized_root_dir, updated_dataset_zip)

            return {
                "success": True,
                "message": cls._build_message(change_summary),
                "updated_dataset_zip": updated_dataset_zip,
                "fixes_json": fixes_json_path,
            }
        except DatasetValidationError as exc:
            Logger.error(f"Sanitization failed: {exc}")
            return cls._failure(str(exc))
        except Exception as exc:
            Logger.error(f"Sanitization failed: {exc}")
            return cls._failure(f"Sanitization failed: {exc}")

    @classmethod
    def _sanitize_geojson_file(cls, source_path: str, target_path: str) -> Dict[str, Any]:
        file_metadata = {
            "filename": os.path.basename(source_path),
            "removedTags": [],
            "precisionUpdates": [],
        }

        try:
            try:
                cls._stream_sanitize_geojson(source_path, target_path, file_metadata)
            except (UnicodeDecodeError, ijson.JSONError):
                # Fallback for non-UTF-8 encodings (e.g. cp1252) or files ijson
                # cannot stream (e.g. literal NaN tokens). Pay the memory cost
                # rather than failing. Output is reopened with "w" so any partial
                # streamed content is truncated and fully rewritten.
                file_metadata["removedTags"].clear()
                file_metadata["precisionUpdates"].clear()
                cls._inmemory_sanitize_geojson(source_path, target_path, file_metadata)
        except DatasetValidationError as exc:
            # Prefix the offending filename so the failure message identifies
            # exactly where the invalid value lives.
            raise DatasetValidationError(f"{file_metadata['filename']}: {exc}") from exc

        gc.collect()
        return file_metadata

    @classmethod
    def _stream_sanitize_geojson(
        cls, source_path: str, target_path: str, file_metadata: Dict[str, Any]
    ) -> None:
        """
        Stream-parse the input geojson and stream-write the sanitized output so
        peak memory stays bounded by the size of the largest single feature
        rather than the entire FeatureCollection.
        """
        with open(source_path, "rb") as source_file, open(
            target_path, "w", encoding="utf-8"
        ) as sanitized_geojson:
            events = iter(ijson.parse(source_file, use_float=True))
            sanitized_geojson.write("{")

            prefix, event, value = next(events)
            if event != "start_map":
                raise ijson.JSONError("Top-level geojson is not an object")

            first_top_key = True
            feature_index = -1

            for prefix, event, value in events:
                if event == "end_map" and prefix == "":
                    break
                if event != "map_key" or prefix != "":
                    raise ijson.JSONError(f"Unexpected event at root: {event}")

                key = value
                if not first_top_key:
                    sanitized_geojson.write(",")
                first_top_key = False
                sanitized_geojson.write(json.dumps(key, ensure_ascii=True))
                sanitized_geojson.write(":")

                if key == "features":
                    array_event = next(events)
                    if array_event[1] != "start_array":
                        raise ijson.JSONError("'features' is not an array")
                    sanitized_geojson.write("[")
                    first_feature = True

                    for prefix, event, value in events:
                        if event == "end_array":
                            break
                        feature = cls._build_value_from_events(events, prefix, event, value)
                        feature_index += 1
                        cls._sanitize_feature_inplace(feature, feature_index, file_metadata)

                        if not first_feature:
                            sanitized_geojson.write(",")
                        first_feature = False
                        cls._stream_json_with_decimal(feature, sanitized_geojson)
                        del feature

                    sanitized_geojson.write("]")
                else:
                    prefix, event, value = next(events)
                    built = cls._build_value_from_events(events, prefix, event, value)
                    cls._stream_json_with_decimal(built, sanitized_geojson)
                    del built

            sanitized_geojson.write("}")

    @classmethod
    def _build_value_from_events(cls, events, prefix: str, event: str, value: Any) -> Any:
        if event in ("null", "boolean", "integer", "double", "number", "string"):
            return value
        if event == "start_map":
            result: Dict[str, Any] = {}
            for prefix, event, value in events:
                if event == "end_map":
                    return result
                if event != "map_key":
                    raise ijson.JSONError(f"Unexpected event in map: {event}")
                key = value
                next_prefix, next_event, next_value = next(events)
                result[key] = cls._build_value_from_events(events, next_prefix, next_event, next_value)
            return result
        if event == "start_array":
            result_list: List[Any] = []
            for prefix, event, value in events:
                if event == "end_array":
                    return result_list
                result_list.append(cls._build_value_from_events(events, prefix, event, value))
            return result_list
        raise ijson.JSONError(f"Unexpected event: {event}")

    @classmethod
    def _sanitize_feature_inplace(
        cls, feature: Dict[str, Any], feature_index: int, file_metadata: Dict[str, Any]
    ) -> None:
        properties = feature.get("properties") or {}
        sanitized_properties = {}
        for tag, value in properties.items():
            if cls._should_remove_value(value):
                file_metadata["removedTags"].append(
                    {
                        "featureIndex": feature_index,
                        "tag": tag,
                        "value": value,
                    }
                )
                continue
            if isinstance(value, float) and not math.isfinite(value):
                # NaN/None are removed above; a remaining non-finite value
                # (Infinity/-Infinity) cannot be serialized to valid JSON.
                raise DatasetValidationError(
                    f"feature {feature_index} property '{tag}' has a non-finite value "
                    f"({cls._describe_non_finite(value)})"
                )
            sanitized_properties[tag] = value
        feature["properties"] = sanitized_properties

        geometry = feature.get("geometry")
        if geometry and "coordinates" in geometry:
            geometry["coordinates"] = cls._sanitize_coordinates(
                geometry["coordinates"],
                feature_index,
                "coordinates",
                file_metadata["precisionUpdates"],
            )

    @classmethod
    def _inmemory_sanitize_geojson(
        cls, source_path: str, target_path: str, file_metadata: Dict[str, Any]
    ) -> None:
        payload = cls._load_geojson_payload(source_path)
        features = payload.get("features", [])
        for feature_index, feature in enumerate(features):
            cls._sanitize_feature_inplace(feature, feature_index, file_metadata)
        with open(target_path, "w", encoding="utf-8") as sanitized_geojson:
            cls._stream_json_with_decimal(payload, sanitized_geojson)
        del payload

    @classmethod
    def _sanitize_coordinates(
        cls,
        coordinates: Any,
        feature_index: int,
        coordinate_path: str,
        precision_updates: List[Dict[str, Any]],
    ) -> Any:
        if isinstance(coordinates, list):
            sanitized_coordinates = []
            for index, item in enumerate(coordinates):
                child_path = f"{coordinate_path}[{index}]"
                sanitized_coordinates.append(
                    cls._sanitize_coordinates(item, feature_index, child_path, precision_updates)
                )
            return sanitized_coordinates

        if isinstance(coordinates, (int, float)):
            if isinstance(coordinates, float) and not math.isfinite(coordinates):
                raise DatasetValidationError(
                    f"feature {feature_index} has a non-finite coordinate "
                    f"({cls._describe_non_finite(coordinates)}) at {coordinate_path}"
                )
            normalized, changed, original_text, updated_text = cls._normalize_coordinate(float(coordinates))
            if changed:
                precision_updates.append(
                    {
                        "featureIndex": feature_index,
                        "coordinatePath": coordinate_path,
                        "original": original_text,
                        "updated": updated_text,
                    }
                )
            return normalized

        return coordinates

    @classmethod
    def _normalize_coordinate(cls, value: float) -> Any:
        decimal_value = Decimal(str(value))
        original_text = format(decimal_value, "f")
        original_fraction = original_text.partition(".")[2]

        # Coordinates with 7 or fewer decimal digits keep their original
        # precision; never pad with trailing zeros. Only longer fractions are
        # truncated down to 7 places to bound file size.
        if len(original_fraction) <= 7:
            return decimal_value, False, original_text, original_text

        truncated_decimal = decimal_value.quantize(cls.COORDINATE_QUANTIZER, rounding=ROUND_DOWN)
        normalized_decimal = cls._strip_trailing_zeros(truncated_decimal)
        updated_text = format(normalized_decimal, "f")
        return normalized_decimal, True, original_text, updated_text

    @staticmethod
    def _strip_trailing_zeros(value: Decimal) -> Decimal:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return Decimal(text)

    @classmethod
    def _should_remove_value(cls, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, float) and math.isnan(value):
            return True
        return False

    @staticmethod
    def _describe_non_finite(value: float) -> str:
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"

    @staticmethod
    def _resolve_dataset_root(extraction_dir: str) -> str:
        children = os.listdir(extraction_dir)
        if len(children) == 1:
            candidate = os.path.join(extraction_dir, children[0])
            if os.path.isdir(candidate):
                return candidate
        return extraction_dir

    @staticmethod
    def _create_zip_from_directory(source_dir: str, zip_path: str) -> None:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for current_root, _, files in os.walk(source_dir):
                for filename in files:
                    file_path = os.path.join(current_root, filename)
                    arcname = os.path.relpath(file_path, source_dir)
                    zip_file.write(file_path, arcname)

    @staticmethod
    def _build_message(change_summary: Dict[str, bool]) -> str:
        removed_values = change_summary["removed_values"]
        precision_updates = change_summary["precision_updates"]
        if removed_values and precision_updates:
            return "Dataset was cleaned and coordinates were standardized."
        if precision_updates:
            return "Coordinates were standardized for consistency."
        if removed_values:
            return "Invalid or empty values were removed from the dataset."
        return "No changes were needed. The dataset is already clean."

    @staticmethod
    def _failure(message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "message": message,
            "updated_dataset_zip": None,
            "fixes_json": None,
        }

    @staticmethod
    def _should_skip_filename(filename: str) -> bool:
        return filename.startswith("._") or filename == ".DS_Store"

    @staticmethod
    def _should_skip_relative_path(relative_path: str) -> bool:
        return relative_path == "__MACOSX" or relative_path.startswith("__MACOSX" + os.sep)

    @classmethod
    def _load_geojson_payload(cls, source_path: str) -> Dict[str, Any]:
        last_error = None
        for encoding in cls.GEOJSON_ENCODINGS:
            try:
                with open(source_path, "r", encoding=encoding) as geojson_file:
                    return json.load(geojson_file)
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
        raise last_error or UnicodeDecodeError("utf-8", b"", 0, 1, "Unable to decode GeoJSON file")

    @classmethod
    def _stream_json_with_decimal(cls, value: Any, file_handle) -> None:
        """
        Serialize ``value`` directly to ``file_handle`` without materializing
        the whole JSON document as a Python string. This keeps peak memory
        bounded for large geojson payloads where the previous string-builder
        approach could allocate multiple gigabytes.
        """
        write = file_handle.write
        if isinstance(value, dict):
            write("{")
            first = True
            for key, item in value.items():
                if not first:
                    write(",")
                first = False
                write(json.dumps(key, ensure_ascii=True))
                write(":")
                cls._stream_json_with_decimal(item, file_handle)
            write("}")
            return
        if isinstance(value, list):
            write("[")
            first = True
            for item in value:
                if not first:
                    write(",")
                first = False
                cls._stream_json_with_decimal(item, file_handle)
            write("]")
            return
        if isinstance(value, Decimal):
            write(format(value, "f"))
            return
        write(json.dumps(value, ensure_ascii=True, allow_nan=False))
