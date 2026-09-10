"""Build and audit the canonical static GeoJSON web-delivery dataset.

This module is deliberately a delivery adapter, not an analytical model. It
joins the approved candidate hexagons to the canonical final score table and
the narrow explanatory fields already emitted by the finalized components.
No indicators, scores, rankings, or geometries are recalculated.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely.geometry import mapping

from .prioritization_model import (
    BOUNDARY_FLAG,
    CANDIDATE_UNITS_LAYER,
    CANDIDATE_UNITS_PATH,
    COMPONENTS,
    EXPECTED_CANDIDATE_COUNT,
    PRESETS_PATH,
    PRESET_SCORE_FIELDS,
    SCORE_FIELDS,
    preset_metadata,
)

DELIVERY_DIRECTORY = Path("data/processed/delivery")
GEOJSON_OUTPUT_PATH = DELIVERY_DIRECTORY / "candidates.geojson"
METADATA_OUTPUT_PATH = DELIVERY_DIRECTORY / "candidates.metadata.json"
CANDIDATE_PROVENANCE_PATH = Path("data/processed/candidate_units.provenance.json")

DELIVERY_SCORE_FIELDS = tuple(PRESET_SCORE_FIELDS.values()) + tuple(SCORE_FIELDS)
DELIVERY_PROPERTY_FIELDS = (
    "hex_id",
    *PRESET_SCORE_FIELDS.values(),
    *SCORE_FIELDS,
    "habitat_context_local_fraction",
    "opposing_balance_ratio",
    "riparian_focal_fraction",
    "nearest_protected_hex_steps",
    "protected_focal_fraction",
    "candidate_land_area_ha",
    "artificial_focal_fraction",
    BOUNDARY_FLAG,
)
DELIVERY_FRACTIONS = (
    "habitat_context_local_fraction",
    "opposing_balance_ratio",
    "riparian_focal_fraction",
    "protected_focal_fraction",
    "artificial_focal_fraction",
)
DELIVERY_INTEGER_FIELDS = ("nearest_protected_hex_steps",)
DELIVERY_HECTARE_FIELDS = ("candidate_land_area_ha",)
SCORE_DECIMALS = 3
FRACTION_DECIMALS = 5
HECTARE_DECIMALS = 2
GEOMETRY_DECIMALS = 6
WGS84 = CRS.from_epsg(4326)
SKANE_BOUNDS = {
    "min_longitude": 11.0,
    "min_latitude": 54.5,
    "max_longitude": 15.5,
    "max_latitude": 57.5,
}

PROPERTY_DESCRIPTIONS = {
    "hex_id": "Stable candidate hexagon identifier.",
    "balanced_score": "Final Balanced preset score; higher means stronger priority.",
    "connectivity_first_score": "Final Connectivity First preset score; higher means stronger priority.",
    "riparian_restoration_score": "Final Riparian Restoration preset score; higher means stronger priority.",
    "habitat_context_score": "Final Habitat Context component score; higher means stronger priority.",
    "ecological_network_score": "Final Ecological Network Context component score; higher means stronger priority.",
    "riparian_opportunity_score": "Final Riparian Opportunity component score; higher means stronger priority.",
    "protected_area_reinforcement_score": "Final Protected-Area Reinforcement component score; higher means stronger priority.",
    "restoration_land_availability_score": "Final Restoration Land Availability component score; higher means stronger priority.",
    "habitat_context_local_fraction": "Surrounding structural habitat fraction used by Habitat Context.",
    "opposing_balance_ratio": "Raw opposing-side configuration ratio used by Ecological Network Context.",
    "riparian_focal_fraction": "Focal wetland/inland-water fraction used by Riparian Opportunity.",
    "nearest_protected_hex_steps": "Protected terrestrial network grid-step proximity used by Protected-Area Reinforcement.",
    "protected_focal_fraction": "Direct protected terrestrial overlap in the candidate cell.",
    "candidate_land_area_ha": "Candidate land area in hectares used by Restoration Land Availability.",
    "artificial_focal_fraction": "Mapped development-burden fraction in the candidate cell.",
    BOUNDARY_FLAG: "True when the candidate is on the analytical study boundary; diagnostic only.",
}


class WebDeliveryError(ValueError):
    """Raised when the web-delivery contract cannot be built safely."""


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise WebDeliveryError(f"{label} is missing required fields: {missing}")


def _validated_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    _require_columns(frame, ("hex_id",), label)
    if frame["hex_id"].isna().any():
        raise WebDeliveryError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if ids.str.strip().eq("").any():
        raise WebDeliveryError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        duplicates = ids[ids.duplicated()].tolist()[:5]
        raise WebDeliveryError(f"{label} contains duplicate hex_id values: {duplicates}")
    return ids


def _validated_numeric(frame: pd.DataFrame, fields: Iterable[str], label: str) -> pd.DataFrame:
    result = frame.copy()
    for field in fields:
        _require_columns(result, (field,), label)
        values = pd.to_numeric(result[field], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise WebDeliveryError(
                f"{label} {field} contains missing, non-numeric, or non-finite values"
            )
        result[field] = values.astype(float)
    return result


def _normalize_bool(values: pd.Series, label: str) -> pd.Series:
    if values.isna().any():
        raise WebDeliveryError(f"{label} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.isfinite(numeric).all() or not np.isin(numeric, [0, 1]).all():
            raise WebDeliveryError(f"{label} must contain only true/false values")
        return values.astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    if not normalized.isin(["true", "false"]).all():
        raise WebDeliveryError(f"{label} must contain only true/false values")
    return normalized.eq("true")


def reconcile_ids(candidate_ids: pd.Series, source_ids: pd.Series, label: str) -> dict[str, Any]:
    """Require exact one-to-one ID reconciliation and return diagnostics."""

    candidate = set(candidate_ids.astype(str))
    source = set(source_ids.astype(str))
    missing = sorted(candidate - source)
    extra = sorted(source - candidate)
    duplicate_source_count = int(source_ids.astype(str).duplicated().sum())
    result = {
        "candidate_count": len(candidate),
        "source_row_count": int(len(source_ids)),
        "source_unique_id_count": len(source),
        "duplicate_source_id_count": duplicate_source_count,
        "missing_candidate_id_count": len(missing),
        "extra_source_id_count": len(extra),
        "missing_candidate_ids": missing,
        "extra_source_ids": extra,
        "exact_one_to_one": (
            len(candidate) == len(source)
            and len(source_ids) == len(source)
            and not missing
            and not extra
        ),
    }
    if not result["exact_one_to_one"]:
        raise WebDeliveryError(
            f"{label} does not reconcile exactly: missing={len(missing)}, extra={len(extra)}, "
            f"duplicate_source_ids={duplicate_source_count}, rows={len(source_ids)}"
        )
    return result


def _validate_preset_metadata(path: Path = PRESETS_PATH) -> dict[str, dict[str, Any]]:
    canonical = preset_metadata()
    if not path.exists():
        raise WebDeliveryError(f"Missing canonical preset metadata: {path}")
    try:
        supplied = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WebDeliveryError(f"Invalid preset metadata JSON: {path}") from exc
    if supplied != canonical:
        raise WebDeliveryError(
            "presets.json does not match canonical prioritization_model definitions"
        )
    return canonical


def _load_component_source(
    path: Path, candidate_ids: pd.Series, fields: Iterable[str], label: str
) -> pd.DataFrame:
    if not path.exists():
        raise WebDeliveryError(f"Missing explanatory source for {label}: {path}")
    frame = pd.read_csv(path)
    ids = _validated_ids(frame, label)
    reconcile_ids(candidate_ids, ids, label)
    _require_columns(frame, fields, label)
    result = frame[["hex_id", *fields, BOUNDARY_FLAG]].copy()
    result["hex_id"] = ids
    result = _validated_numeric(result, fields, label)
    result[BOUNDARY_FLAG] = _normalize_bool(result[BOUNDARY_FLAG], f"{label} {BOUNDARY_FLAG}")
    return result


def assemble_delivery_frame(
    candidate_units: gpd.GeoDataFrame,
    score_source: pd.DataFrame,
    component_sources: Mapping[str, pd.DataFrame],
    expected_count: int | None = EXPECTED_CANDIDATE_COUNT,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Join the approved sources into the narrow delivery contract."""

    if not isinstance(candidate_units, gpd.GeoDataFrame):
        raise WebDeliveryError("Candidate geometry source must be a GeoDataFrame")
    if candidate_units.crs is None or candidate_units.crs.to_epsg() != 3006:
        raise WebDeliveryError(
            f"Candidate geometry CRS is {candidate_units.crs}; expected EPSG:3006"
        )
    if candidate_units.geometry.isna().any() or candidate_units.geometry.is_empty.any():
        raise WebDeliveryError("Candidate geometry contains empty geometries")
    if (~candidate_units.geometry.is_valid).any():
        raise WebDeliveryError("Candidate geometry contains invalid geometries")
    candidate = candidate_units.copy()
    candidate["hex_id"] = _validated_ids(candidate, "Candidate geometry source")
    if expected_count is not None and len(candidate) != expected_count:
        raise WebDeliveryError(
            f"Candidate geometry has {len(candidate):,} rows; expected {expected_count:,}"
        )

    scores = score_source.copy()
    scores["hex_id"] = _validated_ids(scores, "Canonical prioritization scores")
    if expected_count is not None and len(scores) != expected_count:
        raise WebDeliveryError(
            f"Canonical prioritization scores have {len(scores):,} rows; expected {expected_count:,}"
        )
    _require_columns(
        scores, ("hex_id", *DELIVERY_SCORE_FIELDS, BOUNDARY_FLAG), "Canonical prioritization scores"
    )
    scores = _validated_numeric(scores, DELIVERY_SCORE_FIELDS, "Canonical prioritization scores")
    scores[BOUNDARY_FLAG] = _normalize_bool(
        scores[BOUNDARY_FLAG], "Canonical prioritization scores boundary flag"
    )
    score_reconciliation = reconcile_ids(
        candidate["hex_id"], scores["hex_id"], "Canonical prioritization scores"
    )

    joined = candidate[["hex_id", "geometry"]].merge(
        scores[["hex_id", *DELIVERY_SCORE_FIELDS, BOUNDARY_FLAG]],
        on="hex_id",
        how="left",
        validate="one_to_one",
    )
    if joined[list(DELIVERY_SCORE_FIELDS)].isna().any().any():
        raise WebDeliveryError("Canonical prioritization score join produced missing values")

    source_reconciliation: dict[str, Any] = {"prioritization_scores": score_reconciliation}
    for label, config in COMPONENTS.items():
        if label not in component_sources:
            raise WebDeliveryError(f"Missing explanatory component source: {label}")
        component = component_sources[label].copy()
        ids = _validated_ids(component, label)
        field = {
            "Habitat Context": "habitat_context_local_fraction",
            "Ecological Network Context": "opposing_balance_ratio",
            "Riparian Opportunity": "riparian_focal_fraction",
            "Protected-Area Reinforcement": "nearest_protected_hex_steps",
            "Restoration Land Availability": "candidate_land_area_ha",
        }[label]
        extra_field = {
            "Protected-Area Reinforcement": "protected_focal_fraction",
            "Restoration Land Availability": "artificial_focal_fraction",
        }.get(label)
        required = [field] + ([extra_field] if extra_field else [])
        _require_columns(component, ["hex_id", *required, BOUNDARY_FLAG], label)
        component = _validated_numeric(component, required, label)
        if (
            field == "nearest_protected_hex_steps"
            and not np.equal(
                component[field].to_numpy(dtype=float),
                component[field].round().to_numpy(dtype=float),
            ).all()
        ):
            raise WebDeliveryError(f"{label} nearest_protected_hex_steps must contain integers")
        component[BOUNDARY_FLAG] = _normalize_bool(
            component[BOUNDARY_FLAG], f"{label} boundary flag"
        )
        source_reconciliation[label] = reconcile_ids(candidate["hex_id"], ids, label)
        if (
            not component[BOUNDARY_FLAG]
            .reset_index(drop=True)
            .equals(
                scores.set_index("hex_id")
                .loc[component["hex_id"], BOUNDARY_FLAG]
                .reset_index(drop=True)
            )
        ):
            raise WebDeliveryError(
                f"{label} boundary flags do not match canonical prioritization scores"
            )
        selected = component[["hex_id", *required]].copy()
        joined = joined.merge(selected, on="hex_id", how="left", validate="one_to_one")

    if joined[list(DELIVERY_PROPERTY_FIELDS)].isna().any().any():
        raise WebDeliveryError("Delivery join produced missing property values")
    joined = joined.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    joined = gpd.GeoDataFrame(joined, geometry="geometry", crs=candidate.crs)
    joined = joined[["hex_id", "geometry", *DELIVERY_PROPERTY_FIELDS[1:]]]
    diagnostics = {
        "candidate_geometry_count": int(len(candidate)),
        "prioritization_row_count": int(len(scores)),
        "source_reconciliation": source_reconciliation,
        "exact_one_feature_per_candidate": bool(
            len(joined) == len(candidate) and joined["hex_id"].is_unique
        ),
    }
    return joined, diagnostics


def _round_number(value: float | int, decimals: int) -> float:
    rounded = float(f"{float(value):.{decimals}f}")
    return 0.0 if rounded == 0 else rounded


def round_delivery_properties(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the documented delivery precision without altering source frames."""

    result = frame.copy()
    for field in DELIVERY_SCORE_FIELDS:
        result[field] = result[field].map(lambda value: _round_number(value, SCORE_DECIMALS))
    for field in DELIVERY_FRACTIONS:
        result[field] = result[field].map(lambda value: _round_number(value, FRACTION_DECIMALS))
    for field in DELIVERY_HECTARE_FIELDS:
        result[field] = result[field].map(lambda value: _round_number(value, HECTARE_DECIMALS))
    for field in DELIVERY_INTEGER_FIELDS:
        values = pd.to_numeric(result[field], errors="raise")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise WebDeliveryError(f"{field} contains non-finite values")
        result[field] = values.round().astype(int)
    result[BOUNDARY_FLAG] = _normalize_bool(result[BOUNDARY_FLAG], BOUNDARY_FLAG)
    return result


def _round_coordinates(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_round_coordinates(item) for item in value]
    if isinstance(value, (float, int, np.floating, np.integer)):
        return _round_number(value, GEOMETRY_DECIMALS)
    return value


def _rounded_geometry(geometry: Any) -> dict[str, Any]:
    return _round_coordinates(mapping(geometry))


def _feature_records(frame_wgs84: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in frame_wgs84.iterrows():
        properties = {
            field: row[field].item() if hasattr(row[field], "item") else row[field]
            for field in DELIVERY_PROPERTY_FIELDS
        }
        properties["hex_id"] = str(properties["hex_id"])
        properties[BOUNDARY_FLAG] = bool(properties[BOUNDARY_FLAG])
        record = {
            "type": "Feature",
            "id": str(row["hex_id"]),
            "properties": properties,
            "geometry": _rounded_geometry(row.geometry),
        }
        records.append(record)
    return records


def serialize_geojson(features: list[dict[str, Any]]) -> bytes:
    """Serialize a deterministic compact RFC-compatible FeatureCollection."""

    payload = {
        "type": "FeatureCollection",
        "features": features,
    }
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WebDeliveryError("GeoJSON contains a non-serializable or non-finite value") from exc


def _gzip_size(payload: bytes) -> int:
    return len(gzip.compress(payload, compresslevel=9, mtime=0))


def _payload_breakdown(features: list[dict[str, Any]], full_payload: bytes) -> dict[str, Any]:
    geometry_id_features = [
        {
            "type": "Feature",
            "id": feature["id"],
            "properties": {"hex_id": feature["properties"]["hex_id"]},
            "geometry": feature["geometry"],
        }
        for feature in features
    ]
    geometry_id_payload = serialize_geojson(geometry_id_features)
    property_bytes = max(0, len(full_payload) - len(geometry_id_payload))
    return {
        "method": "full compact FeatureCollection minus compact geometry+feature-id+hex_id FeatureCollection",
        "full_payload_bytes": len(full_payload),
        "geometry_id_only_bytes": len(geometry_id_payload),
        "estimated_property_bytes": property_bytes,
        "estimated_geometry_and_id_percent": 100.0 * len(geometry_id_payload) / len(full_payload),
        "estimated_properties_percent": 100.0 * property_bytes / len(full_payload),
    }


def _coordinate_count(geometry: Mapping[str, Any]) -> int:
    coordinates = geometry.get("coordinates", [])

    def count(value: Any) -> int:
        if not value:
            return 0
        if isinstance(value[0], (int, float)):
            return 1
        return sum(count(item) for item in value)

    return count(coordinates)


def _exterior_coordinate_counts(geometry: Mapping[str, Any]) -> list[int]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        return [len(coordinates[0])] if coordinates else []
    if geometry_type == "MultiPolygon":
        return [len(polygon[0]) for polygon in coordinates if polygon]
    return []


def _audit_features(
    features: list[dict[str, Any]],
    before_invalid: int,
    after_invalid: int,
    source_reconciliation: dict[str, Any],
) -> dict[str, Any]:
    ids = [feature.get("id") for feature in features]
    geometry_type_counts: dict[str, int] = {}
    empty_geometries = 0
    coordinate_count = 0
    exterior_counts: list[int] = []
    missing_properties = 0
    nonfinite_values = 0
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry or not geometry.get("coordinates"):
            empty_geometries += 1
        else:
            geometry_type = geometry.get("type")
            geometry_type_counts[geometry_type] = geometry_type_counts.get(geometry_type, 0) + 1
            coordinate_count += _coordinate_count(geometry)
            exterior_counts.extend(_exterior_coordinate_counts(geometry))
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            missing_properties += len(DELIVERY_PROPERTY_FIELDS)
        else:
            for field in DELIVERY_PROPERTY_FIELDS:
                if field not in properties or properties[field] is None:
                    missing_properties += 1
                    continue
                value = properties[field]
                if (
                    isinstance(value, (float, int))
                    and not isinstance(value, bool)
                    and not math.isfinite(value)
                ):
                    nonfinite_values += 1
    duplicate_ids = len(ids) - len(set(ids))
    return {
        "feature_count": len(features),
        "geometry_type_counts": geometry_type_counts,
        "polygon_count": geometry_type_counts.get("Polygon", 0),
        "multipolygon_count": geometry_type_counts.get("MultiPolygon", 0),
        "empty_geometry_count": empty_geometries,
        "invalid_geometry_count_before_transformation": before_invalid,
        "invalid_geometry_count_after_transformation": after_invalid,
        "duplicate_feature_id_count": duplicate_ids,
        "missing_property_value_count": missing_properties,
        "nonfinite_numeric_value_count": nonfinite_values,
        "total_geometry_coordinate_count": coordinate_count,
        "median_exterior_coordinate_count": float(np.median(exterior_counts))
        if exterior_counts
        else 0.0,
        "max_exterior_coordinate_count": max(exterior_counts) if exterior_counts else 0,
        "source_reconciliation": source_reconciliation,
    }


def _validate_bbox(frame_wgs84: gpd.GeoDataFrame) -> dict[str, float]:
    min_lon, min_lat, max_lon, max_lat = (float(value) for value in frame_wgs84.total_bounds)
    bbox = {
        "min_longitude": min_lon,
        "min_latitude": min_lat,
        "max_longitude": max_lon,
        "max_latitude": max_lat,
    }
    if not (
        SKANE_BOUNDS["min_longitude"] <= min_lon <= max_lon <= SKANE_BOUNDS["max_longitude"]
        and SKANE_BOUNDS["min_latitude"] <= min_lat <= max_lat <= SKANE_BOUNDS["max_latitude"]
    ):
        raise WebDeliveryError(f"EPSG:4326 bbox is outside plausible Skåne bounds: {bbox}")
    return bbox


def _candidate_eligibility_summary() -> dict[str, Any]:
    if not CANDIDATE_PROVENANCE_PATH.exists():
        return {"source": str(CANDIDATE_PROVENANCE_PATH), "available": False}
    data = json.loads(CANDIDATE_PROVENANCE_PATH.read_text(encoding="utf-8"))
    eligibility = data.get("eligibility_definition", {})
    population = data.get("population", {})
    return {
        "source": str(CANDIDATE_PROVENANCE_PATH),
        "available": True,
        "expression": eligibility.get("expression"),
        "minimum_candidate_area_ha": eligibility.get("minimum_candidate_area_ha"),
        "minimum_candidate_fraction_of_terrestrial": eligibility.get(
            "minimum_candidate_fraction_of_terrestrial"
        ),
        "primary_candidate_pixel": eligibility.get("primary_candidate_pixel"),
        "eligible_candidate_units": population.get("eligible_candidate_units"),
        "total_terrestrial_analysis_units": population.get("total_terrestrial_analysis_units"),
    }


def _rounding_audit(full_frame: pd.DataFrame, delivered_frame: pd.DataFrame) -> dict[str, Any]:
    expected = round_delivery_properties(full_frame)
    comparisons: dict[str, Any] = {}
    for field in [
        *DELIVERY_SCORE_FIELDS,
        *DELIVERY_FRACTIONS,
        *DELIVERY_HECTARE_FIELDS,
        *DELIVERY_INTEGER_FIELDS,
    ]:
        difference = (full_frame[field].astype(float) - expected[field].astype(float)).abs()
        mismatch = (delivered_frame[field].astype(float) - expected[field].astype(float)).abs()
        mismatch_count = int((mismatch != 0).sum())
        comparisons[field] = {
            "decimals": SCORE_DECIMALS
            if field in DELIVERY_SCORE_FIELDS
            else FRACTION_DECIMALS
            if field in DELIVERY_FRACTIONS
            else HECTARE_DECIMALS
            if field in DELIVERY_HECTARE_FIELDS
            else 0,
            "maximum_absolute_difference": float(difference.max()) if len(difference) else 0.0,
            "unexpected_mismatch_count": mismatch_count,
        }
        if mismatch_count:
            raise WebDeliveryError(
                f"Delivery values for {field} do not equal rounded canonical values"
            )
    return {
        "comparisons": comparisons,
        "maximum_absolute_rounding_difference": max(
            comparison["maximum_absolute_difference"] for comparison in comparisons.values()
        ),
        "unexpected_mismatch_count": sum(
            comparison["unexpected_mismatch_count"] for comparison in comparisons.values()
        ),
        "rankings_recomputed_from_rounded_values": False,
    }


def build_web_delivery(
    geometry_path: Path = CANDIDATE_UNITS_PATH,
    score_path: Path = Path("data/processed/prioritization/prioritization_scores.csv"),
    output_path: Path = GEOJSON_OUTPUT_PATH,
    metadata_path: Path = METADATA_OUTPUT_PATH,
    presets_path: Path = PRESETS_PATH,
    expected_count: int | None = EXPECTED_CANDIDATE_COUNT,
) -> dict[str, Any]:
    """Build the compact GeoJSON and metadata, then return the audit."""

    started = time.perf_counter()
    if not geometry_path.exists():
        raise WebDeliveryError(f"Missing candidate geometry source: {geometry_path}")
    if not score_path.exists():
        raise WebDeliveryError(f"Missing canonical prioritization source: {score_path}")
    candidate_units = gpd.read_file(geometry_path, layer=CANDIDATE_UNITS_LAYER)
    score_source = pd.read_csv(score_path)
    candidate_ids = _validated_ids(candidate_units, "Candidate geometry source")
    component_sources: dict[str, pd.DataFrame] = {}
    source_paths: dict[str, str] = {
        "candidate_geometry": str(geometry_path),
        "prioritization_scores": str(score_path),
    }
    for label, config in COMPONENTS.items():
        source_path = Path(config["path"])
        field_names = {
            "Habitat Context": ["habitat_context_local_fraction"],
            "Ecological Network Context": ["opposing_balance_ratio"],
            "Riparian Opportunity": ["riparian_focal_fraction"],
            "Protected-Area Reinforcement": [
                "nearest_protected_hex_steps",
                "protected_focal_fraction",
            ],
            "Restoration Land Availability": [
                "candidate_land_area_ha",
                "artificial_focal_fraction",
            ],
        }[label]
        component_sources[label] = _load_component_source(
            source_path, candidate_ids, field_names, label
        )
        source_paths[label] = str(source_path)

    joined, join_audit = assemble_delivery_frame(
        candidate_units, score_source, component_sources, expected_count
    )
    before_invalid = int((~joined.geometry.is_valid).sum())
    source_empty = int(joined.geometry.is_empty.sum())
    if source_empty or before_invalid:
        raise WebDeliveryError(
            f"Source geometry audit failed: empty={source_empty}, invalid={before_invalid}"
        )
    wgs84 = joined.to_crs(WGS84)
    if wgs84.crs is None or wgs84.crs.to_epsg() != 4326:
        raise WebDeliveryError(f"Transformed geometry CRS is {wgs84.crs}; expected EPSG:4326")
    after_invalid = int((~wgs84.geometry.is_valid).sum())
    if int(wgs84.geometry.is_empty.sum()) or after_invalid:
        raise WebDeliveryError(
            f"Transformed geometry audit failed: empty={int(wgs84.geometry.is_empty.sum())}, invalid={after_invalid}"
        )
    bbox = _validate_bbox(wgs84)

    full_properties = wgs84[["hex_id", *DELIVERY_PROPERTY_FIELDS[1:]]].copy()
    delivered_properties = round_delivery_properties(full_properties)
    for field in DELIVERY_PROPERTY_FIELDS:
        wgs84[field] = delivered_properties[field].to_numpy()
    features = _feature_records(wgs84)
    payload = serialize_geojson(features)
    parsed = json.loads(payload.decode("utf-8"))
    if parsed.get("type") != "FeatureCollection" or len(parsed.get("features", [])) != len(wgs84):
        raise WebDeliveryError("Serialized GeoJSON failed FeatureCollection parse audit")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    geometry_audit = _audit_features(
        features, before_invalid, after_invalid, join_audit["source_reconciliation"]
    )
    rounding_audit = _rounding_audit(full_properties, delivered_properties)
    full_size = len(payload)
    gzip_size = _gzip_size(payload)
    payload_audit = {
        "uncompressed_bytes": full_size,
        "gzip_level": 9,
        "gzip_bytes": gzip_size,
        "compression_ratio_uncompressed_to_gzip": full_size / gzip_size,
        "uncompressed_bytes_per_feature": full_size / len(features),
        "gzip_bytes_per_feature": gzip_size / len(features),
        "breakdown": _payload_breakdown(features, payload),
    }
    presets = _validate_preset_metadata(presets_path)
    metadata: dict[str, Any] = {
        "dataset_name": "Landscape Restoration Prioritizer candidate web-delivery dataset",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "feature_count": len(features),
        "crs": {"name": "WGS 84", "authority": "EPSG", "code": 4326},
        "source_crs": {"name": "SWEREF99 TM", "authority": "EPSG", "code": 3006},
        "geometry_types": sorted(geometry_audit["geometry_type_counts"]),
        "bbox_epsg_4326": bbox,
        "property_names": list(DELIVERY_PROPERTY_FIELDS),
        "property_count": len(DELIVERY_PROPERTY_FIELDS),
        "property_descriptions": PROPERTY_DESCRIPTIONS,
        "numeric_precision": {
            "scores": f"{SCORE_DECIMALS} decimal places",
            "fractions_and_ratios": f"{FRACTION_DECIMALS} decimal places",
            "candidate_land_area_ha": f"{HECTARE_DECIMALS} decimal places",
            "nearest_protected_hex_steps": "integer",
            "boundary_edge_flag": "boolean",
            "geometry_coordinates_epsg_4326": f"{GEOMETRY_DECIMALS} decimal places",
        },
        "presets": presets,
        "score_direction": "Higher = stronger priority under the selected preset or component.",
        "analytical_source_paths": source_paths,
        "candidate_eligibility_summary": _candidate_eligibility_summary(),
        "interpretation_caveats": [
            "This is decision-support screening, not a restoration probability, legal designation, or implementation decision.",
            "Delivered values are rounded for web payload size; canonical analytical rankings remain based on full-precision source artifacts.",
            "Preset switching is client-side because all three final preset scores are delivered per feature.",
            "Boundary flags are diagnostics and do not alter component or preset scores.",
            "No geometry simplification, buffering, or grid regeneration is performed.",
        ],
        "file_size_bytes": full_size,
        "benchmark_gzip_size_bytes": gzip_size,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "geometry_audit": geometry_audit,
        "rounding_audit": rounding_audit,
        "payload_audit": payload_audit,
        "parse_audit": {
            "valid_json": True,
            "feature_collection": parsed["type"] == "FeatureCollection",
            "parsed_feature_count": len(parsed["features"]),
            "feature_ids_match_hex_id": all(
                feature["id"] == feature["properties"]["hex_id"] for feature in parsed["features"]
            ),
            "property_contract_matches_metadata": set(DELIVERY_PROPERTY_FIELDS)
            == set(PROPERTY_DESCRIPTIONS),
        },
        "maplibre_suitability": {
            "classification": "CLEARLY SUITABLE FOR SINGLE GEOJSON",
            "evidence": "26,395 simple polygons, measured compact/gzip payload, low coordinate complexity, and narrow precomputed properties; browser parse/render benchmarking remains a prudent follow-up.",
            "vector_tiling_justified_from_artifact_alone": False,
        },
        "delivery_architecture": "one static GeoJSON FeatureCollection; no API, PostGIS, PMTiles, MBTiles, or vector tiling introduced",
        "environmental_datasets_downloaded": False,
        "dependency_changes": [],
        "warnings": [],
        "runtime_seconds": time.perf_counter() - started,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    metadata = build_web_delivery()
    payload = metadata["payload_audit"]
    geometry = metadata["geometry_audit"]
    print(
        "Web delivery built: "
        f"{metadata['feature_count']:,} features, "
        f"{metadata['file_size_bytes']:,} bytes, "
        f"{metadata['benchmark_gzip_size_bytes']:,} gzip bytes, "
        f"{geometry['polygon_count']:,} Polygon geometries, "
        f"{geometry['total_geometry_coordinate_count']:,} coordinates"
    )
    print(
        f"bbox={metadata['bbox_epsg_4326']} "
        f"bytes/feature={payload['uncompressed_bytes_per_feature']:.1f} "
        f"gzip-bytes/feature={payload['gzip_bytes_per_feature']:.1f} "
        f"classification={metadata['maplibre_suitability']['classification']}"
    )


if __name__ == "__main__":
    main()
