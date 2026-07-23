import gc
import copy
import json
import math
import os
import shutil
import zipfile
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List

import ijson

from src.config import SanitizationConfig
from src.logger import Logger


class DatasetValidationError(Exception):
    """
    Raised when a dataset contains a value that cannot be sanitized into valid
    GeoJSON (e.g. a NaN/Infinity coordinate, or a non-finite property value).
    Carries a human-readable location so the failure message points at the
    offending file, feature, and field.
    """


class SanitizationProcessor:
    GEOJSON_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
    OSW_DATASET_KEYS = ("edges", "lines", "nodes", "points", "polygons", "zones")

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
                "removedFiles": [],
            }
            change_summary = {
                "removed_values": False,
                "precision_updates": False,
                "removed_edges": False,
                "split_edges": False,
                "removed_files": False,
            }
            config = SanitizationConfig()
            generated_nodes: List[Dict[str, Any]] = []
            nodes_file_written = False

            for current_root, _, files in os.walk(dataset_root):
                relative_root = os.path.relpath(current_root, dataset_root)
                if cls._should_skip_relative_path(relative_root):
                    continue
                sanitized_root = sanitized_root_dir if relative_root == "." else os.path.join(sanitized_root_dir, relative_root)
                os.makedirs(sanitized_root, exist_ok=True)

                for filename in sorted(files, key=cls._filename_sort_key):
                    if cls._should_skip_filename(filename):
                        continue
                    source_path = os.path.join(current_root, filename)
                    target_path = os.path.join(sanitized_root, filename)
                    relative_filename = filename if relative_root == "." else os.path.join(relative_root, filename)
                    if not cls._is_supported_osw_filename(filename):
                        fixes["removedFiles"].append(
                            {
                                "filename": relative_filename,
                                "fixType": "unsupported_file_removed",
                                "action": "removed_from_sanitized_output",
                            }
                        )
                        change_summary["removed_files"] = True
                        continue
                    if filename.lower().endswith(".geojson"):
                        with Logger.timer(f"sanitize_geojson_file ({filename})"):
                            file_metadata = cls._sanitize_geojson_file(
                                source_path, target_path, config, generated_nodes
                            )
                        if cls._dataset_key_for_filename(filename) == "nodes":
                            nodes_file_written = True
                        # Only record files that actually changed; skipping
                        # untouched files keeps fixes.json free of empty entries.
                        removed_tags = file_metadata["removedTags"]
                        precision_updates = file_metadata["precisionUpdates"]
                        removed_edges = file_metadata["removedEdges"]
                        split_edges = file_metadata["splitEdges"]
                        added_nodes = file_metadata["addedNodes"]
                        if removed_tags:
                            change_summary["removed_values"] = True
                        if precision_updates:
                            change_summary["precision_updates"] = True
                        if removed_edges:
                            change_summary["removed_edges"] = True
                        if split_edges:
                            change_summary["split_edges"] = True
                        if removed_tags or precision_updates or removed_edges or split_edges or added_nodes:
                            file_entry: Dict[str, Any] = {"filename": file_metadata["filename"]}
                            if removed_tags:
                                file_entry["removedTags"] = removed_tags
                            if precision_updates:
                                file_entry["precisionUpdates"] = precision_updates
                            if removed_edges:
                                file_entry["removedEdges"] = removed_edges
                            if split_edges:
                                file_entry["splitEdges"] = split_edges
                            if added_nodes:
                                file_entry["addedNodes"] = added_nodes
                            fixes["files"].append(file_entry)
                    else:
                        shutil.copy2(source_path, target_path)

            if generated_nodes and not nodes_file_written:
                nodes_path = os.path.join(sanitized_root_dir, "nodes.geojson")
                cls._write_generated_nodes_file(nodes_path, generated_nodes)
                fixes["files"].append(
                    {
                        "filename": "nodes.geojson",
                        "addedNodes": cls._generated_node_logs(generated_nodes),
                    }
                )
                change_summary["split_edges"] = True

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
    def _sanitize_geojson_file(
        cls,
        source_path: str,
        target_path: str,
        config: SanitizationConfig,
        generated_nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        file_metadata = {
            "filename": os.path.basename(source_path),
            "removedTags": [],
            "precisionUpdates": [],
            "removedEdges": [],
            "splitEdges": [],
            "addedNodes": [],
        }

        try:
            try:
                cls._stream_sanitize_geojson(source_path, target_path, file_metadata, config, generated_nodes)
            except (UnicodeDecodeError, ijson.JSONError):
                # Fallback for non-UTF-8 encodings (e.g. cp1252) or files ijson
                # cannot stream (e.g. literal NaN tokens). Pay the memory cost
                # rather than failing. Output is reopened with "w" so any partial
                # streamed content is truncated and fully rewritten.
                file_metadata["removedTags"].clear()
                file_metadata["precisionUpdates"].clear()
                file_metadata["removedEdges"].clear()
                file_metadata["splitEdges"].clear()
                file_metadata["addedNodes"].clear()
                cls._inmemory_sanitize_geojson(source_path, target_path, file_metadata, config, generated_nodes)
        except DatasetValidationError as exc:
            # Prefix the offending filename so the failure message identifies
            # exactly where the invalid value lives.
            raise DatasetValidationError(f"{file_metadata['filename']}: {exc}") from exc

        gc.collect()
        return file_metadata

    @classmethod
    def _stream_sanitize_geojson(
        cls,
        source_path: str,
        target_path: str,
        file_metadata: Dict[str, Any],
        config: SanitizationConfig,
        generated_nodes: List[Dict[str, Any]],
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
                            if cls._dataset_key_for_filename(file_metadata["filename"]) == "nodes":
                                cls._write_generated_nodes(generated_nodes, file_metadata, sanitized_geojson, first_feature)
                                first_feature = first_feature and not generated_nodes
                            break
                        feature = cls._build_value_from_events(events, prefix, event, value)
                        feature_index += 1
                        sanitized_features = cls._sanitize_feature(
                            feature, feature_index, file_metadata, config, generated_nodes
                        )

                        for sanitized_feature in sanitized_features:
                            if not first_feature:
                                sanitized_geojson.write(",")
                            first_feature = False
                            cls._stream_json_with_decimal(sanitized_feature, sanitized_geojson)
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
        cls,
        feature: Dict[str, Any],
        feature_index: int,
        file_metadata: Dict[str, Any],
        config: SanitizationConfig,
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
                config,
            )

    @classmethod
    def _inmemory_sanitize_geojson(
        cls,
        source_path: str,
        target_path: str,
        file_metadata: Dict[str, Any],
        config: SanitizationConfig,
        generated_nodes: List[Dict[str, Any]],
    ) -> None:
        payload = cls._load_geojson_payload(source_path)
        features = payload.get("features", [])
        sanitized_features = []
        for feature_index, feature in enumerate(features):
            sanitized_features.extend(cls._sanitize_feature(feature, feature_index, file_metadata, config, generated_nodes))
        if cls._dataset_key_for_filename(file_metadata["filename"]) == "nodes":
            sanitized_features.extend(generated_nodes)
            file_metadata["addedNodes"].extend(cls._generated_node_logs(generated_nodes))
        payload["features"] = sanitized_features
        with open(target_path, "w", encoding="utf-8") as sanitized_geojson:
            cls._stream_json_with_decimal(payload, sanitized_geojson)
        del payload

    @classmethod
    def _sanitize_feature(
        cls,
        feature: Dict[str, Any],
        feature_index: int,
        file_metadata: Dict[str, Any],
        config: SanitizationConfig,
        generated_nodes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        cls._sanitize_feature_inplace(feature, feature_index, file_metadata, config)
        if cls._should_remove_zero_length_edge(feature, file_metadata["filename"], config):
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            file_metadata["removedEdges"].append(
                {
                    "featureIndex": feature_index,
                    "featureId": cls._feature_id(feature, feature_index),
                    "fixType": "zero_length_edge_removed",
                    "actualLength": cls._line_length(coordinates),
                    "allowZeroLengthLines": config.allow_zero_length_lines,
                    "action": "removed_feature",
                }
            )
            return []
        return cls._split_oversized_edge(feature, feature_index, file_metadata, config, generated_nodes)

    @classmethod
    def _sanitize_coordinates(
        cls,
        coordinates: Any,
        feature_index: int,
        coordinate_path: str,
        precision_updates: List[Dict[str, Any]],
        config: SanitizationConfig,
    ) -> Any:
        if isinstance(coordinates, list):
            sanitized_coordinates = []
            for index, item in enumerate(coordinates):
                child_path = f"{coordinate_path}[{index}]"
                sanitized_coordinates.append(
                    cls._sanitize_coordinates(item, feature_index, child_path, precision_updates, config)
                )
            return sanitized_coordinates

        if isinstance(coordinates, (int, float)):
            if isinstance(coordinates, float) and not math.isfinite(coordinates):
                raise DatasetValidationError(
                    f"feature {feature_index} has a non-finite coordinate "
                    f"({cls._describe_non_finite(coordinates)}) at {coordinate_path}"
                )
            normalized, changed, original_text, updated_text = cls._normalize_coordinate(
                float(coordinates), config.coordinate_precision
            )
            if changed:
                precision_updates.append(
                    {
                        "featureIndex": feature_index,
                        "coordinatePath": coordinate_path,
                        "original": original_text,
                        "updated": updated_text,
                        "precision": config.coordinate_precision,
                    }
                )
            return normalized

        return coordinates

    @classmethod
    def _normalize_coordinate(cls, value: float, precision: int = 7) -> Any:
        decimal_value = Decimal(str(value))
        original_text = format(decimal_value, "f")
        original_fraction = original_text.partition(".")[2]

        # Coordinates within the configured precision keep their original
        # precision; never pad with trailing zeros. Only longer fractions are
        # truncated down to bound file size.
        if len(original_fraction) <= precision:
            return decimal_value, False, original_text, original_text

        truncated_decimal = decimal_value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_DOWN)
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
    def _split_oversized_edge(
        cls,
        feature: Dict[str, Any],
        feature_index: int,
        file_metadata: Dict[str, Any],
        config: SanitizationConfig,
        generated_nodes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if cls._dataset_key_for_filename(file_metadata["filename"]) != "edges":
            return [feature]

        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            return [feature]

        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) <= config.max_geometry_vertices:
            return [feature]

        split_features = []
        split_node_ids = []
        start_index = 0
        part_number = 1
        original_id = cls._feature_id(feature, feature_index)
        while start_index < len(coordinates) - 1:
            end_index = min(start_index + config.max_geometry_vertices, len(coordinates))
            part = copy.deepcopy(feature)
            part["geometry"]["coordinates"] = coordinates[start_index:end_index]
            cls._set_split_feature_id(part, original_id, part_number)
            split_features.append(part)
            if end_index == len(coordinates):
                break
            split_node_id = f"{original_id}-split-node-{part_number}"
            split_node_ids.append(split_node_id)
            generated_nodes.append(cls._generated_node_feature(split_node_id, coordinates[end_index - 1]))
            start_index = end_index - 1
            part_number += 1

        cls._set_split_endpoint_ids(split_features, feature, split_node_ids)

        file_metadata["splitEdges"].append(
            {
                "featureIndex": feature_index,
                "featureId": original_id,
                "fixType": "oversized_edge_split",
                "originalVertexCount": len(coordinates),
                "maxVertexCount": config.max_geometry_vertices,
                "splitCount": len(split_features),
                "generatedNodeIds": split_node_ids,
                "generatedFeatureIds": [
                    cls._feature_id(split_feature, feature_index)
                    for split_feature in split_features
                ],
            }
        )
        return split_features

    @classmethod
    def _should_remove_zero_length_edge(
        cls, feature: Dict[str, Any], filename: str, config: SanitizationConfig
    ) -> bool:
        if config.allow_zero_length_lines:
            return False
        if cls._dataset_key_for_filename(filename) != "edges":
            return False
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            return False
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            return False
        return cls._line_length(coordinates) <= 0

    @staticmethod
    def _line_length(coordinates: Any) -> float:
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            return 0.0

        total = 0.0
        previous = None
        for coordinate in coordinates:
            if not SanitizationProcessor._is_coordinate_pair(coordinate):
                previous = None
                continue
            current = (float(coordinate[0]), float(coordinate[1]))
            if previous is not None:
                total += math.dist(previous, current)
            previous = current
        return total

    @staticmethod
    def _is_coordinate_pair(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (int, float, Decimal))
            and isinstance(value[1], (int, float, Decimal))
        )

    @staticmethod
    def _feature_id(feature: Dict[str, Any], feature_index: int) -> Any:
        properties = feature.get("properties") or {}
        feature_id = properties.get("_id", feature.get("id", feature_index))
        return feature_index if feature_id in (None, "") else feature_id

    @staticmethod
    def _set_split_feature_id(feature: Dict[str, Any], original_id: Any, part_number: int) -> None:
        split_id = f"{original_id}-part-{part_number}"
        properties = feature.setdefault("properties", {})
        if "_id" in properties:
            properties["_id"] = split_id
        if "id" in feature:
            feature["id"] = split_id

    @staticmethod
    def _set_split_endpoint_ids(
        split_features: List[Dict[str, Any]],
        original_feature: Dict[str, Any],
        split_node_ids: List[str],
    ) -> None:
        original_properties = original_feature.get("properties") or {}
        if "_u_id" not in original_properties and "_v_id" not in original_properties:
            return

        original_u_id = original_properties.get("_u_id")
        original_v_id = original_properties.get("_v_id")
        for index, split_feature in enumerate(split_features):
            properties = split_feature.setdefault("properties", {})
            if "_u_id" in original_properties:
                properties["_u_id"] = original_u_id if index == 0 else split_node_ids[index - 1]
            if "_v_id" in original_properties:
                properties["_v_id"] = original_v_id if index == len(split_features) - 1 else split_node_ids[index]

    @staticmethod
    def _generated_node_feature(node_id: str, coordinate: Any) -> Dict[str, Any]:
        return {
            "type": "Feature",
            "properties": {"_id": node_id},
            "geometry": {
                "type": "Point",
                "coordinates": copy.deepcopy(coordinate),
            },
        }

    @classmethod
    def _write_generated_nodes(
        cls,
        generated_nodes: List[Dict[str, Any]],
        file_metadata: Dict[str, Any],
        file_handle,
        first_feature: bool,
    ) -> None:
        for generated_node in generated_nodes:
            if not first_feature:
                file_handle.write(",")
            first_feature = False
            cls._stream_json_with_decimal(generated_node, file_handle)
        file_metadata["addedNodes"].extend(cls._generated_node_logs(generated_nodes))

    @classmethod
    def _write_generated_nodes_file(cls, nodes_path: str, generated_nodes: List[Dict[str, Any]]) -> None:
        with open(nodes_path, "w", encoding="utf-8") as nodes_file:
            cls._stream_json_with_decimal(
                {
                    "type": "FeatureCollection",
                    "features": generated_nodes,
                },
                nodes_file,
            )

    @staticmethod
    def _generated_node_logs(generated_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "featureId": node["properties"]["_id"],
                "fixType": "split_node_added",
                "action": "added_feature",
            }
            for node in generated_nodes
        ]

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
        other_updates = (
            change_summary.get("removed_edges", False)
            or change_summary.get("split_edges", False)
            or change_summary.get("removed_files", False)
        )
        if other_updates:
            return "Dataset was sanitized for OSW compliance."
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
    def _is_supported_osw_filename(cls, filename: str) -> bool:
        return cls._dataset_key_for_filename(filename) is not None

    @classmethod
    def _dataset_key_for_filename(cls, filename: str) -> Any:
        lower_name = filename.lower()
        for dataset_key in cls.OSW_DATASET_KEYS:
            if (
                lower_name == f"{dataset_key}.geojson"
                or lower_name == f"{dataset_key}.osw.geojson"
                or lower_name.endswith(f".{dataset_key}.geojson")
                or lower_name.endswith(f".{dataset_key}.osw.geojson")
            ):
                return dataset_key
        return None

    @classmethod
    def _filename_sort_key(cls, filename: str) -> Any:
        dataset_key = cls._dataset_key_for_filename(filename)
        dataset_order = {
            "edges": 0,
            "lines": 1,
            "nodes": 2,
            "points": 3,
            "polygons": 4,
            "zones": 5,
        }
        return (dataset_order.get(dataset_key, 99), filename)

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
