"""Calculate raw Habitat Context indicators for candidate units.

The candidate layer is the focal population, while all surrounding composition
comes from the complete terrestrial analysis grid. The regular grid
allows exact integer axial-neighborhood lookup without polygon buffering or
spatial joins.  This module deliberately stops at raw indicators and audit
diagnostics; it does not normalize, score, or combine them.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from restoration_prioritizer.config import TARGET_CRS

ANALYSIS_UNITS_PATH = Path("data/processed/analysis_units.gpkg")
ANALYSIS_UNITS_LAYER = "analysis_units"
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"
OUTPUT_PATH = Path("data/processed/indicators/habitat_context.csv")
PROVENANCE_PATH = Path("data/processed/indicators/habitat_context.provenance.json")

BOUNDARY_EDGE_DISTANCE_M = 1_000.0
CSV_FLOAT_PRECISION = 10

FULL_REQUIRED_FIELDS = (
    "hex_id",
    "grid_col",
    "grid_row",
    "terrestrial_pixels",
    "habitat_context_pixels",
    "geometry",
)
CANDIDATE_CORRELATION_FIELDS = (
    "candidate_fraction_of_terrestrial",
    "candidate_area_m2",
    "habitat_context_fraction_of_terrestrial",
)

OUTPUT_COLUMNS = (
    "hex_id",
    "adjacent_habitat_pixels",
    "adjacent_terrestrial_pixels",
    "habitat_context_adjacent_fraction",
    "adjacent_terrestrial_cells_present",
    "local_habitat_pixels",
    "local_terrestrial_pixels",
    "habitat_context_local_fraction",
    "local_terrestrial_cells_present",
    "boundary_edge_flag",
)


class HabitatContextError(ValueError):
    """Raised when the input contract is malformed."""


def axial_distance(first: tuple[int, int], second: tuple[int, int] = (0, 0)) -> int:
    """Return axial hex distance for the existing ``(grid_col, grid_row)`` axes."""

    delta_col = int(first[0]) - int(second[0])
    delta_row = int(first[1]) - int(second[1])
    return max(abs(delta_col), abs(delta_row), abs(delta_col + delta_row))


def ring_offsets(distance: int) -> tuple[tuple[int, int], ...]:
    """Return deterministic offsets at exactly one axial hex distance."""

    if distance < 0:
        raise HabitatContextError("Hex distance must be non-negative")
    offsets = [
        (delta_col, delta_row)
        for delta_col in range(-distance, distance + 1)
        for delta_row in range(-distance, distance + 1)
        if axial_distance((delta_col, delta_row)) == distance
    ]
    offsets.sort()
    return tuple(offsets)


RING_1_OFFSETS = ring_offsets(1)
RING_2_OFFSETS = ring_offsets(2)
LOCAL_OFFSETS = tuple(sorted((*RING_1_OFFSETS, *RING_2_OFFSETS)))


def _require_columns(frame: gpd.GeoDataFrame, required: Sequence[str], label: str) -> None:
    missing = [field for field in required if field not in frame.columns]
    if missing:
        raise HabitatContextError(f"{label} is missing required fields: {missing}")


def _validate_coordinate_fields(frame: gpd.GeoDataFrame, label: str) -> None:
    for field in ("grid_col", "grid_row"):
        values = frame[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)) or not np.all(values == np.floor(values)):
            raise HabitatContextError(f"{label} {field} values must be finite integers")


def _validate_geometry(frame: gpd.GeoDataFrame, label: str) -> None:
    if frame.geometry.name not in frame.columns:
        raise HabitatContextError(f"{label} has no geometry column")
    if frame.crs is None or str(frame.crs) != TARGET_CRS:
        raise HabitatContextError(f"{label} CRS is {frame.crs}; expected {TARGET_CRS}")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise HabitatContextError(f"{label} geometries must be non-empty")
    if (~frame.geometry.is_valid).any():
        raise HabitatContextError(f"{label} geometries must be valid")


def _validate_nonnegative_pixel_fields(frame: gpd.GeoDataFrame, label: str) -> None:
    for field in ("terrestrial_pixels", "habitat_context_pixels"):
        values = frame[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)) or np.any(values < 0):
            raise HabitatContextError(f"{label} {field} values must be finite and non-negative")
        if not np.all(values == np.floor(values)):
            raise HabitatContextError(f"{label} {field} values must be integer pixel counts")
    if np.any(
        frame["habitat_context_pixels"].to_numpy(dtype=float)
        > frame["terrestrial_pixels"].to_numpy(dtype=float)
    ):
        raise HabitatContextError(f"{label} habitat pixels exceed terrestrial pixels")


def _coordinate_pairs(frame: gpd.GeoDataFrame) -> list[tuple[int, int]]:
    return list(
        zip(
            frame["grid_col"].to_numpy(dtype=int),
            frame["grid_row"].to_numpy(dtype=int),
            strict=True,
        )
    )


def validate_inputs(
    analysis_units: gpd.GeoDataFrame, candidate_units: gpd.GeoDataFrame
) -> dict[str, int]:
    """Validate the full analysis grid and candidate focal population."""

    if not isinstance(analysis_units, gpd.GeoDataFrame):
        raise HabitatContextError("Analysis units must be a GeoDataFrame")
    if not isinstance(candidate_units, gpd.GeoDataFrame):
        raise HabitatContextError("Candidate units must be a GeoDataFrame")
    _require_columns(analysis_units, FULL_REQUIRED_FIELDS, "Analysis units")
    _require_columns(candidate_units, FULL_REQUIRED_FIELDS, "Candidate units")
    _validate_geometry(analysis_units, "Analysis units")
    _validate_geometry(candidate_units, "Candidate units")
    _validate_coordinate_fields(analysis_units, "Analysis units")
    _validate_coordinate_fields(candidate_units, "Candidate units")
    _validate_nonnegative_pixel_fields(analysis_units, "Analysis units")
    _validate_nonnegative_pixel_fields(candidate_units, "Candidate units")

    if analysis_units["hex_id"].isna().any() or analysis_units["hex_id"].duplicated().any():
        raise HabitatContextError("Analysis-unit hex_id values must be non-null and unique")
    if candidate_units["hex_id"].isna().any() or candidate_units["hex_id"].duplicated().any():
        raise HabitatContextError("Candidate hex_id values must be non-null and unique")

    analysis_pairs = _coordinate_pairs(analysis_units)
    candidate_pairs = _coordinate_pairs(candidate_units)
    if len(set(analysis_pairs)) != len(analysis_pairs):
        raise HabitatContextError("Analysis-unit (grid_col, grid_row) pairs must be unique")
    if len(set(candidate_pairs)) != len(candidate_pairs):
        raise HabitatContextError("Candidate (grid_col, grid_row) pairs must be unique")

    for frame, label in ((analysis_units, "Analysis units"), (candidate_units, "Candidate units")):
        expected_ids = [f"h_{col}_{row}" for col, row in _coordinate_pairs(frame)]
        if frame["hex_id"].tolist() != expected_ids:
            raise HabitatContextError(f"{label} hex_id values must match h_<grid_col>_<grid_row>")

    analysis_ids = set(analysis_units["hex_id"].tolist())
    candidate_ids = set(candidate_units["hex_id"].tolist())
    missing_ids = candidate_ids - analysis_ids
    if missing_ids:
        raise HabitatContextError(
            f"Candidate units missing from full analysis grid: {sorted(missing_ids)[:5]}"
        )

    return {
        "candidate_input_count": int(len(candidate_units)),
        "analysis_input_count": int(len(analysis_units)),
        "candidate_duplicate_hex_id_count": int(candidate_units["hex_id"].duplicated().sum()),
        "analysis_duplicate_hex_id_count": int(analysis_units["hex_id"].duplicated().sum()),
        "candidate_missing_from_analysis_count": int(len(missing_ids)),
    }


def _aggregate_offsets(
    coordinate: tuple[int, int],
    offsets: Iterable[tuple[int, int]],
    lookup: dict[tuple[int, int], tuple[int, int, str]],
) -> tuple[int, int, int, list[tuple[int, int]]]:
    habitat_pixels = 0
    terrestrial_pixels = 0
    cells_present = 0
    present_coordinates: list[tuple[int, int]] = []
    for delta_col, delta_row in offsets:
        neighbor = (coordinate[0] + delta_col, coordinate[1] + delta_row)
        values = lookup.get(neighbor)
        if values is None:
            continue
        terrestrial, habitat, _ = values
        habitat_pixels += habitat
        terrestrial_pixels += terrestrial
        cells_present += 1
        present_coordinates.append(neighbor)
    return habitat_pixels, terrestrial_pixels, cells_present, present_coordinates


def _fraction(habitat_pixels: int, terrestrial_pixels: int) -> float:
    return float(habitat_pixels / terrestrial_pixels) if terrestrial_pixels else float("nan")


def calculate_indicators(
    candidate_units: gpd.GeoDataFrame,
    analysis_units: gpd.GeoDataFrame,
    edge_flags: Sequence[bool] | None = None,
) -> pd.DataFrame:
    """Calculate raw immediate and local context indicators by integer lookup."""

    validate_inputs(analysis_units, candidate_units)
    if edge_flags is not None and len(edge_flags) != len(candidate_units):
        raise HabitatContextError("Boundary edge flags must match candidate row count")

    lookup: dict[tuple[int, int], tuple[int, int, str]] = {}
    for row in analysis_units.itertuples(index=False):
        lookup[(int(row.grid_col), int(row.grid_row))] = (
            int(row.terrestrial_pixels),
            int(row.habitat_context_pixels),
            str(row.hex_id),
        )

    records: list[dict[str, Any]] = []
    for row in candidate_units.itertuples(index=False):
        coordinate = (int(row.grid_col), int(row.grid_row))
        adjacent_habitat, adjacent_terrestrial, adjacent_present, _ = _aggregate_offsets(
            coordinate, RING_1_OFFSETS, lookup
        )
        local_habitat, local_terrestrial, local_present, _ = _aggregate_offsets(
            coordinate, LOCAL_OFFSETS, lookup
        )
        record: dict[str, Any] = {
            "hex_id": str(row.hex_id),
            "adjacent_habitat_pixels": adjacent_habitat,
            "adjacent_terrestrial_pixels": adjacent_terrestrial,
            "habitat_context_adjacent_fraction": _fraction(adjacent_habitat, adjacent_terrestrial),
            "adjacent_terrestrial_cells_present": adjacent_present,
            "local_habitat_pixels": local_habitat,
            "local_terrestrial_pixels": local_terrestrial,
            "habitat_context_local_fraction": _fraction(local_habitat, local_terrestrial),
            "local_terrestrial_cells_present": local_present,
        }
        records.append(record)

    indicators = pd.DataFrame.from_records(records)
    if indicators.empty:
        raise HabitatContextError("Candidate population is empty")
    if edge_flags is not None:
        indicators["boundary_edge_flag"] = np.asarray(edge_flags, dtype=bool)
    return indicators[[column for column in OUTPUT_COLUMNS if column in indicators.columns]]


def calculate_boundary_edge_flags(
    candidate_units: gpd.GeoDataFrame,
    study_geometry: BaseGeometry,
    threshold_m: float = BOUNDARY_EDGE_DISTANCE_M,
) -> np.ndarray:
    """Flag candidates whose centroid is within the selected study-boundary zone."""

    if threshold_m <= 0:
        raise HabitatContextError("Boundary edge threshold must be positive")
    if study_geometry is None or study_geometry.is_empty or not study_geometry.is_valid:
        raise HabitatContextError("Study-area geometry must be non-empty and valid")
    distances = candidate_units.geometry.centroid.distance(study_geometry.boundary)
    return distances.to_numpy(dtype=float) <= threshold_m


def _quantile_summary(values: pd.Series) -> dict[str, float | None]:
    valid = values.dropna()
    if valid.empty:
        return {
            key: None for key in ("min", "p10", "p25", "median", "p75", "p90", "p95", "max", "mean")
        }
    quantiles = valid.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95])
    return {
        "min": float(valid.min()),
        "p10": float(quantiles.loc[0.10]),
        "p25": float(quantiles.loc[0.25]),
        "median": float(quantiles.loc[0.50]),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "max": float(valid.max()),
        "mean": float(valid.mean()),
    }


def _fraction_bins(values: pd.Series) -> dict[str, int]:
    valid = values.dropna()
    return {
        "0%": int((valid == 0).sum()),
        ">0–10%": int(((valid > 0) & (valid <= 0.10)).sum()),
        ">10–25%": int(((valid > 0.10) & (valid <= 0.25)).sum()),
        ">25–50%": int(((valid > 0.25) & (valid <= 0.50)).sum()),
        ">50–75%": int(((valid > 0.50) & (valid <= 0.75)).sum()),
        ">75%": int((valid > 0.75).sum()),
        "missing": int(values.isna().sum()),
    }


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, float | int | None]:
    pair = pd.concat([first, second], axis=1).dropna()
    if len(pair) < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    first_values = pair.iloc[:, 0]
    second_values = pair.iloc[:, 1]
    if first_values.nunique() < 2 or second_values.nunique() < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    return {
        "n": int(len(pair)),
        "pearson": float(first_values.corr(second_values, method="pearson")),
        "spearman": float(
            first_values.rank(method="average").corr(second_values.rank(method="average"))
        ),
    }


def _count_distribution(values: pd.Series, expected_max: int) -> dict[str, int]:
    counts = values.value_counts().to_dict()
    return {str(value): int(counts.get(value, 0)) for value in range(expected_max + 1)}


def _missing_neighbor_summary(
    indicators: pd.DataFrame, edge_mask: pd.Series, subset_label: str
) -> dict[str, Any]:
    subset = indicators.loc[edge_mask]
    adjacent_missing = 6 - subset["adjacent_terrestrial_cells_present"]
    local_missing = 18 - subset["local_terrestrial_cells_present"]
    return {
        "label": subset_label,
        "candidate_count": int(len(subset)),
        "adjacent_missing_positions_distribution": _count_distribution(adjacent_missing, 6),
        "local_missing_positions_distribution": _count_distribution(local_missing, 18),
        "candidates_with_any_missing_adjacent_position": int((adjacent_missing > 0).sum()),
        "candidates_with_any_missing_local_position": int((local_missing > 0).sum()),
    }


def _indicator_distributions(indicators: pd.DataFrame) -> dict[str, Any]:
    return {
        "habitat_context_adjacent_fraction": {
            **_quantile_summary(indicators["habitat_context_adjacent_fraction"]),
            "descriptive_bins": _fraction_bins(indicators["habitat_context_adjacent_fraction"]),
        },
        "habitat_context_local_fraction": {
            **_quantile_summary(indicators["habitat_context_local_fraction"]),
            "descriptive_bins": _fraction_bins(indicators["habitat_context_local_fraction"]),
        },
    }


def _correlation_diagnostics(
    indicators: pd.DataFrame, candidate_units: gpd.GeoDataFrame
) -> dict[str, Any]:
    candidate_index = candidate_units.set_index("hex_id")
    joined = indicators.set_index("hex_id").join(
        candidate_index[list(CANDIDATE_CORRELATION_FIELDS)], how="left", validate="one_to_one"
    )
    indicator_names = (
        "habitat_context_adjacent_fraction",
        "habitat_context_local_fraction",
    )
    return {
        "immediate_vs_local": _correlation(joined[indicator_names[0]], joined[indicator_names[1]]),
        "against_candidate_attributes": {
            indicator: {
                field: _correlation(joined[indicator], joined[field])
                for field in CANDIDATE_CORRELATION_FIELDS
            }
            for indicator in indicator_names
        },
    }


def _spatial_sanity(
    analysis_units: gpd.GeoDataFrame,
    candidate_units: gpd.GeoDataFrame,
    indicators: pd.DataFrame,
) -> dict[str, Any]:
    grid_by_coordinate = {
        coordinate: row
        for coordinate, row in zip(
            _coordinate_pairs(analysis_units), analysis_units.itertuples(index=False), strict=True
        )
    }
    candidates_by_id = candidate_units.set_index("hex_id")
    joined = indicators.set_index("hex_id")
    interior_ids = joined.loc[
        (joined["adjacent_terrestrial_cells_present"] == 6)
        & (joined["local_terrestrial_cells_present"] == 18)
    ].index.tolist()
    sample_ids = sorted(interior_ids)[:5]
    ring_1_distances: list[float] = []
    ring_2_distances: list[float] = []
    samples: list[dict[str, Any]] = []
    for hex_id in sample_ids:
        candidate = candidates_by_id.loc[hex_id]
        coordinate = (int(candidate.grid_col), int(candidate.grid_row))
        focal_centroid = candidate.geometry.centroid
        ring_1_ids = []
        ring_2_ids = []
        for delta_col, delta_row in RING_1_OFFSETS:
            neighbor_coordinate = (coordinate[0] + delta_col, coordinate[1] + delta_row)
            neighbor = grid_by_coordinate.get(neighbor_coordinate)
            if neighbor is not None:
                ring_1_ids.append(str(neighbor.hex_id))
                ring_1_distances.append(float(focal_centroid.distance(neighbor.geometry.centroid)))
        for delta_col, delta_row in RING_2_OFFSETS:
            neighbor_coordinate = (coordinate[0] + delta_col, coordinate[1] + delta_row)
            neighbor = grid_by_coordinate.get(neighbor_coordinate)
            if neighbor is not None:
                ring_2_ids.append(str(neighbor.hex_id))
                ring_2_distances.append(float(focal_centroid.distance(neighbor.geometry.centroid)))
        samples.append(
            {
                "hex_id": hex_id,
                "ring_1_neighbor_ids": sorted(ring_1_ids),
                "ring_2_neighbor_ids": sorted(ring_2_ids),
                "ring_1_expected_count": 6,
                "ring_2_expected_count": 12,
            }
        )

    def distance_summary(
        values: list[float], expected_min: float, expected_max: float
    ) -> dict[str, Any]:
        return {
            "sampled_distance_count": len(values),
            "min_m": float(min(values)) if values else None,
            "max_m": float(max(values)) if values else None,
            "expected_min_m": expected_min,
            "expected_max_m": expected_max,
            "within_expected_range": bool(
                values and min(values) >= expected_min - 1e-6 and max(values) <= expected_max + 1e-6
            ),
        }

    return {
        "axial_distance_formula": "max(abs(delta_col), abs(delta_row), abs(delta_col + delta_row))",
        "ring_1_offsets": [list(offset) for offset in RING_1_OFFSETS],
        "ring_2_offsets": [list(offset) for offset in RING_2_OFFSETS],
        "local_offsets": [list(offset) for offset in LOCAL_OFFSETS],
        "interior_sample_count": len(samples),
        "interior_samples": samples,
        "ring_1_center_distance_m": distance_summary(ring_1_distances, 500.0, 500.0),
        "ring_2_center_distance_m": distance_summary(
            ring_2_distances, 500.0 * math.sqrt(3.0), 1_000.0
        ),
    }


def _boundary_diagnostics(indicators: pd.DataFrame) -> dict[str, Any]:
    edge_mask = indicators["boundary_edge_flag"].astype(bool)
    non_edge_mask = ~edge_mask
    adjacent_missing = 6 - indicators["adjacent_terrestrial_cells_present"]
    local_missing = 18 - indicators["local_terrestrial_cells_present"]
    return {
        "definition": (
            "boundary_edge_flag is true when the focal candidate centroid is within 1,000 m "
            "of the dissolved study-area boundary in EPSG:3006. The focal centroid convention "
            "is used; candidate geometry is not buffered."
        ),
        "threshold_m": BOUNDARY_EDGE_DISTANCE_M,
        "candidate_count_within_edge_zone": int(edge_mask.sum()),
        "candidate_percent_within_edge_zone": float(100.0 * edge_mask.mean()),
        "edge_zone": _missing_neighbor_summary(indicators, edge_mask, "edge_zone"),
        "non_edge_zone": _missing_neighbor_summary(indicators, non_edge_mask, "non_edge_zone"),
        "outside_edge_zone_with_missing_adjacent_positions": int(
            (non_edge_mask & (adjacent_missing > 0)).sum()
        ),
        "outside_edge_zone_with_missing_local_positions": int(
            (non_edge_mask & (local_missing > 0)).sum()
        ),
        "outside_edge_zone_with_any_missing_position": int(
            (non_edge_mask & ((adjacent_missing > 0) | (local_missing > 0))).sum()
        ),
    }


def _validate_indicator_values(indicators: pd.DataFrame) -> None:
    if indicators["hex_id"].duplicated().any():
        raise HabitatContextError("Indicator output contains duplicate hex_id values")
    for prefix in ("adjacent", "local"):
        habitat = indicators[f"{prefix}_habitat_pixels"]
        terrestrial = indicators[f"{prefix}_terrestrial_pixels"]
        if (habitat > terrestrial).any():
            raise HabitatContextError(f"{prefix} habitat pixels exceed terrestrial pixels")
        fraction = indicators[f"habitat_context_{prefix}_fraction"]
        valid = fraction.dropna()
        if ((valid < 0) | (valid > 1)).any():
            raise HabitatContextError(f"{prefix} habitat fraction is outside [0, 1]")
        if ((terrestrial == 0) & fraction.notna()).any():
            raise HabitatContextError(f"{prefix} zero-terrestrial fraction must be missing")
        if ((terrestrial > 0) & fraction.isna()).any():
            raise HabitatContextError(f"{prefix} positive-terrestrial fraction must be present")


def _read_source(path: Path, layer: str, label: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path, layer=layer)
    except (OSError, ValueError) as exc:
        raise HabitatContextError(f"Could not read {label}: {path}: {exc}") from exc


def _read_study_geometry(path: Path) -> BaseGeometry:
    study = _read_source(path, STUDY_AREA_LAYER, "study area")
    _validate_geometry(study, "Study area")
    if study.empty:
        raise HabitatContextError("Study area is empty")
    return study.geometry.union_all()


def _write_csv(indicators: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    indicators.to_csv(
        temporary_path,
        index=False,
        columns=OUTPUT_COLUMNS,
        float_format=f"%.{CSV_FLOAT_PRECISION}f",
        na_rep="NaN",
        lineterminator="\n",
    )
    os.replace(temporary_path, path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary_path, path)


def build_habitat_context(
    analysis_units_path: Path = ANALYSIS_UNITS_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the raw Habitat Context table, diagnostics, and provenance."""

    start = time.perf_counter()
    analysis_units = _read_source(analysis_units_path, ANALYSIS_UNITS_LAYER, "analysis units")
    candidate_units = _read_source(candidate_units_path, CANDIDATE_UNITS_LAYER, "candidate units")
    validation = validate_inputs(analysis_units, candidate_units)
    _require_columns(candidate_units, CANDIDATE_CORRELATION_FIELDS, "Candidate units")
    study_geometry = _read_study_geometry(study_area_path)
    edge_flags = calculate_boundary_edge_flags(candidate_units, study_geometry)
    indicators = calculate_indicators(candidate_units, analysis_units, edge_flags=edge_flags)
    _validate_indicator_values(indicators)

    candidate_ids = candidate_units["hex_id"].tolist()
    output_ids = indicators["hex_id"].tolist()
    duplicate_output_count = int(indicators["hex_id"].duplicated().sum())
    missing_output_ids = sorted(set(candidate_ids) - set(output_ids))
    extra_output_ids = sorted(set(output_ids) - set(candidate_ids))
    if missing_output_ids or extra_output_ids or len(output_ids) != len(candidate_ids):
        raise HabitatContextError("Indicator output IDs do not exactly match candidate IDs")

    distributions = _indicator_distributions(indicators)
    correlations = _correlation_diagnostics(indicators, candidate_units)
    completeness = {
        "adjacent_terrestrial_cells_present_distribution": _count_distribution(
            indicators["adjacent_terrestrial_cells_present"], 6
        ),
        "local_terrestrial_cells_present": _quantile_summary(
            indicators["local_terrestrial_cells_present"].astype(float)
        ),
        "local_all_18_positions_represented_count": int(
            (indicators["local_terrestrial_cells_present"] == 18).sum()
        ),
    }
    zero_denominators = {
        "adjacent": int((indicators["adjacent_terrestrial_pixels"] == 0).sum()),
        "local": int((indicators["local_terrestrial_pixels"] == 0).sum()),
    }
    provenance: dict[str, Any] = {
        "component_name": "Habitat Context",
        "indicator_role": (
            "Raw landscape-structure proxy: existing structurally vegetated/wetland land cover "
            "surrounding each eligible agricultural restoration-candidate analysis unit."
        ),
        "source": {
            "analysis_units_path": str(analysis_units_path),
            "analysis_units_layer": ANALYSIS_UNITS_LAYER,
            "candidate_units_path": str(candidate_units_path),
            "candidate_units_layer": CANDIDATE_UNITS_LAYER,
            "study_area_path": str(study_area_path),
            "study_area_layer": STUDY_AREA_LAYER,
        },
        "grid_geometry_convention": {
            "crs": TARGET_CRS,
            "orientation": "pointy-top axial-style grid",
            "flat_to_flat_width_m": 500.0,
            "fixed_origin": [0.0, 0.0],
            "center_formula": "x=(grid_col + grid_row/2)*500; y=grid_row*1.5*(500/sqrt(3))",
            "coordinate_axes": "grid_col=q and grid_row=r",
        },
        "neighborhood_convention": {
            "axial_distance_formula": "max(abs(delta_col), abs(delta_row), abs(delta_col + delta_row))",
            "ring_1_offsets": [list(offset) for offset in RING_1_OFFSETS],
            "ring_2_offsets": [list(offset) for offset in RING_2_OFFSETS],
            "immediate_definition": "all six positions with hex distance exactly 1",
            "local_definition": "all positions with 1 <= hex distance <= 2; 6 + 12 = 18 positions",
            "focal_cell_excluded": True,
        },
        "denominator_definition": (
            "Terrestrial NMD pixels in available surrounding analysis units; sea, inland water, "
            "and no-data are not denominators. Missing grid positions are omitted rather than "
            "treated as zero terrestrial habitat."
        ),
        "candidate_population_validation": {
            **validation,
            "output_indicator_count": int(len(indicators)),
            "duplicate_output_hex_id_count": duplicate_output_count,
            "missing_output_hex_id_count": len(missing_output_ids),
            "extra_output_hex_id_count": len(extra_output_ids),
            "candidate_ids_reconcile_exactly": candidate_ids == output_ids,
        },
        "zero_denominator_counts": zero_denominators,
        "neighborhood_completeness": completeness,
        "boundary_edge_diagnostic": _boundary_diagnostics(indicators),
        "indicator_distributions": distributions,
        "correlation_diagnostics": correlations,
        "spatial_sanity": _spatial_sanity(analysis_units, candidate_units, indicators),
        "validation": {
            "expected_ring_1_offset_count": 6,
            "observed_ring_1_offset_count": len(RING_1_OFFSETS),
            "expected_ring_2_offset_count": 12,
            "observed_ring_2_offset_count": len(RING_2_OFFSETS),
            "expected_local_offset_count": 18,
            "observed_local_offset_count": len(LOCAL_OFFSETS),
            "focal_cell_excluded": True,
            "candidate_coordinate_pairs_exist_in_analysis_grid": True,
            "habitat_pixels_constrained_by_terrestrial_pixels": True,
            "fractions_valid_or_missing_for_zero_denominator": True,
        },
        "output": {
            "path": str(output_path),
            "feature_count": int(len(indicators)),
            "columns": list(OUTPUT_COLUMNS),
        },
        "caveats": [
            "The habitat_context_proxy is a structural NMD land-cover proxy, not biodiversity, habitat quality, ecological condition, forest naturalness, species occurrence, legal protection, or restoration success probability.",
            "The current NMD and analysis-unit artifacts stop at the Skåne study boundary, so context across Halland or Blekinge is truncated and is only diagnosed here.",
            "The indicators are raw fractions and have not been normalized, scored, weighted, or combined.",
            "The regular hex neighborhoods are deterministic analytical conventions and should not be interpreted as exact ecological influence distances.",
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - start,
    }
    _write_csv(indicators, output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    _write_json(provenance_path, provenance)
    return indicators, provenance


def main() -> None:
    """Generate real-data raw Habitat Context indicators and print a summary."""

    indicators, provenance = build_habitat_context()
    output = provenance["output"]
    zero_denominators = provenance["zero_denominator_counts"]
    boundary = provenance["boundary_edge_diagnostic"]
    print(f"Habitat Context indicators: {output['path']} ({output['size_bytes']:,} bytes)")
    print(
        f"Candidates {len(indicators):,}; zero terrestrial denominators "
        f"adjacent {zero_denominators['adjacent']:,}, local {zero_denominators['local']:,}"
    )
    print(
        f"Boundary edge zone {boundary['candidate_count_within_edge_zone']:,} candidates "
        f"({boundary['candidate_percent_within_edge_zone']:.2f}%)"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
