"""Calculate raw Ecological Network Context bridging indicators.

This module measures the arrangement of habitat-context composition in the six
immediate hex neighbors of each eligible candidate.  It intentionally stops at
two raw opposing-axis indicators and diagnostics; it does not create habitat
patches, normalize, score, or combine indicators.
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
from restoration_prioritizer.habitat_context import calculate_boundary_edge_flags

ANALYSIS_UNITS_PATH = Path("data/processed/analysis_units.gpkg")
ANALYSIS_UNITS_LAYER = "analysis_units"
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"
HABITAT_CONTEXT_PATH = Path("data/processed/indicators/habitat_context.csv")
HABITAT_CONTEXT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
OUTPUT_PATH = Path("data/processed/indicators/ecological_network.csv")
PROVENANCE_PATH = Path("data/processed/indicators/ecological_network.provenance.json")

HABITAT_FRACTION_FIELD = "habitat_context_fraction_of_terrestrial"
BOUNDARY_FLAG = "boundary_edge_flag"
CSV_FLOAT_PRECISION = 10
GEOMETRY_TOLERANCE_M = 1e-6

FIRST_RING_OFFSETS = (
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
)

OPPOSITE_AXIS_PAIRS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "axis_a": ((-1, 0), (1, 0)),
    "axis_b": ((0, -1), (0, 1)),
    "axis_c": ((-1, 1), (1, -1)),
}

AXIS_STRENGTH_COLUMNS = (
    "bridge_axis_a_strength",
    "bridge_axis_b_strength",
    "bridge_axis_c_strength",
)
NETWORK_INDICATORS = ("bridge_strength_max", "bridge_strength_mean")
OUTPUT_COLUMNS = (
    "hex_id",
    *AXIS_STRENGTH_COLUMNS,
    "bridge_strength_max",
    "bridge_strength_mean",
    "strongest_axis",
    "strongest_axis_tie_count",
    "adjacent_cells_missing",
    BOUNDARY_FLAG,
)
CANDIDATE_CORRELATION_FIELDS = (
    "candidate_fraction_of_terrestrial",
    "candidate_area_m2",
    HABITAT_FRACTION_FIELD,
)
FULL_REQUIRED_FIELDS = (
    "hex_id",
    "grid_col",
    "grid_row",
    "terrestrial_pixels",
    HABITAT_FRACTION_FIELD,
    "geometry",
)


class EcologicalNetworkError(ValueError):
    """Raised when the Step 6/7 network-indicator input contract is invalid."""


def _require_columns(frame: pd.DataFrame, required: Sequence[str], label: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise EcologicalNetworkError(f"{label} is missing required fields: {missing}")


def _validate_geometry(frame: gpd.GeoDataFrame, label: str) -> None:
    if frame.geometry.name not in frame.columns:
        raise EcologicalNetworkError(f"{label} has no geometry column")
    if frame.crs is None or str(frame.crs) != TARGET_CRS:
        raise EcologicalNetworkError(f"{label} CRS is {frame.crs}; expected {TARGET_CRS}")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise EcologicalNetworkError(f"{label} geometries must be non-empty")
    if (~frame.geometry.is_valid).any():
        raise EcologicalNetworkError(f"{label} geometries must be valid")


def _validate_coordinates(frame: pd.DataFrame, label: str) -> None:
    for field in ("grid_col", "grid_row"):
        values = frame[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)) or not np.all(values == np.floor(values)):
            raise EcologicalNetworkError(f"{label} {field} values must be finite integers")


def _validate_nonnegative_integers(frame: pd.DataFrame, field: str, label: str) -> None:
    values = frame[field].to_numpy(dtype=float)
    if (
        not np.all(np.isfinite(values))
        or np.any(values < 0)
        or not np.all(values == np.floor(values))
    ):
        raise EcologicalNetworkError(f"{label} {field} values must be finite non-negative integers")


def _validate_fraction(values: pd.Series, field: str, allow_missing: bool = False) -> None:
    numeric = pd.to_numeric(values, errors="coerce")
    if not allow_missing and numeric.isna().any():
        raise EcologicalNetworkError(f"{field} contains missing or non-numeric values")
    valid = numeric.dropna().to_numpy(dtype=float)
    if not np.all(np.isfinite(valid)):
        raise EcologicalNetworkError(f"{field} contains non-finite values")
    if np.any((valid < 0) | (valid > 1)):
        raise EcologicalNetworkError(f"{field} must lie in [0, 1]")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    if frame["hex_id"].isna().any():
        raise EcologicalNetworkError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if (ids.str.len() == 0).any():
        raise EcologicalNetworkError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise EcologicalNetworkError(f"{label} hex_id values must be unique")
    return ids


def _coordinate_pairs(frame: pd.DataFrame) -> list[tuple[int, int]]:
    return list(
        zip(
            frame["grid_col"].to_numpy(dtype=int),
            frame["grid_row"].to_numpy(dtype=int),
            strict=True,
        )
    )


def validate_grid_inputs(
    analysis_units: gpd.GeoDataFrame, candidate_units: gpd.GeoDataFrame
) -> dict[str, int]:
    """Validate the full terrestrial grid and the focal candidate population."""

    if not isinstance(analysis_units, gpd.GeoDataFrame):
        raise EcologicalNetworkError("Analysis units must be a GeoDataFrame")
    if not isinstance(candidate_units, gpd.GeoDataFrame):
        raise EcologicalNetworkError("Candidate units must be a GeoDataFrame")
    _require_columns(analysis_units, FULL_REQUIRED_FIELDS, "Analysis units")
    _require_columns(
        candidate_units, (*FULL_REQUIRED_FIELDS, *CANDIDATE_CORRELATION_FIELDS), "Candidate units"
    )
    _validate_geometry(analysis_units, "Analysis units")
    _validate_geometry(candidate_units, "Candidate units")
    _validate_coordinates(analysis_units, "Analysis units")
    _validate_coordinates(candidate_units, "Candidate units")
    _validate_nonnegative_integers(analysis_units, "terrestrial_pixels", "Analysis units")
    _validate_nonnegative_integers(candidate_units, "terrestrial_pixels", "Candidate units")
    if (analysis_units["terrestrial_pixels"] <= 0).any():
        raise EcologicalNetworkError("Analysis units must contain positive terrestrial pixels")
    if (candidate_units["terrestrial_pixels"] <= 0).any():
        raise EcologicalNetworkError("Candidate units must contain positive terrestrial pixels")
    _validate_fraction(analysis_units[HABITAT_FRACTION_FIELD], HABITAT_FRACTION_FIELD)
    _validate_fraction(candidate_units[HABITAT_FRACTION_FIELD], HABITAT_FRACTION_FIELD)
    for field in CANDIDATE_CORRELATION_FIELDS[:2]:
        values = pd.to_numeric(candidate_units[field], errors="coerce")
        if values.isna().any() or not np.all(np.isfinite(values.to_numpy(dtype=float))):
            raise EcologicalNetworkError(f"Candidate field {field} must be finite and numeric")
    if (candidate_units["candidate_area_m2"] < 0).any():
        raise EcologicalNetworkError("candidate_area_m2 must be non-negative")
    if (candidate_units["candidate_fraction_of_terrestrial"] < 0).any() or (
        candidate_units["candidate_fraction_of_terrestrial"] > 1
    ).any():
        raise EcologicalNetworkError("candidate_fraction_of_terrestrial must lie in [0, 1]")

    analysis_ids = _normalise_ids(analysis_units, "Analysis units")
    candidate_ids = _normalise_ids(candidate_units, "Candidate units")
    analysis_pairs = _coordinate_pairs(analysis_units)
    candidate_pairs = _coordinate_pairs(candidate_units)
    if len(set(analysis_pairs)) != len(analysis_pairs):
        raise EcologicalNetworkError("Analysis-unit (grid_col, grid_row) pairs must be unique")
    if len(set(candidate_pairs)) != len(candidate_pairs):
        raise EcologicalNetworkError("Candidate (grid_col, grid_row) pairs must be unique")
    for frame, label in ((analysis_units, "Analysis units"), (candidate_units, "Candidate units")):
        expected_ids = [f"h_{col}_{row}" for col, row in _coordinate_pairs(frame)]
        if frame["hex_id"].astype(str).tolist() != expected_ids:
            raise EcologicalNetworkError(
                f"{label} hex_id values must match h_<grid_col>_<grid_row>"
            )
    missing_ids = set(candidate_ids) - set(analysis_ids)
    if missing_ids:
        raise EcologicalNetworkError(
            f"Candidate units missing from full analysis grid: {sorted(missing_ids)[:5]}"
        )
    return {
        "candidate_input_count": int(len(candidate_units)),
        "analysis_input_count": int(len(analysis_units)),
        "candidate_missing_from_analysis_count": int(len(missing_ids)),
    }


def _axis_strengths(
    coordinate: tuple[int, int], lookup: dict[tuple[int, int], float]
) -> tuple[dict[str, float], int]:
    strengths: dict[str, float] = {}
    missing = 0
    for axis, (side_1, side_2) in OPPOSITE_AXIS_PAIRS.items():
        values = []
        for delta_col, delta_row in (side_1, side_2):
            value = lookup.get((coordinate[0] + delta_col, coordinate[1] + delta_row))
            if value is None:
                missing += 1
                value = 0.0
            values.append(value)
        strengths[axis] = float(min(values))
    return strengths, missing


def calculate_indicators(
    candidate_units: gpd.GeoDataFrame,
    analysis_units: gpd.GeoDataFrame,
    edge_flags: Sequence[bool] | None = None,
) -> pd.DataFrame:
    """Calculate raw opposing-axis bridge indicators for each candidate."""

    validation = validate_grid_inputs(analysis_units, candidate_units)
    del validation
    if edge_flags is not None and len(edge_flags) != len(candidate_units):
        raise EcologicalNetworkError("Boundary edge flags must match candidate row count")
    lookup = {
        (int(row.grid_col), int(row.grid_row)): float(getattr(row, HABITAT_FRACTION_FIELD))
        for row in analysis_units.itertuples(index=False)
    }
    records: list[dict[str, Any]] = []
    for row in candidate_units.itertuples(index=False):
        coordinate = (int(row.grid_col), int(row.grid_row))
        strengths, missing = _axis_strengths(coordinate, lookup)
        values = np.array([strengths[axis] for axis in ("axis_a", "axis_b", "axis_c")])
        maximum = float(np.max(values))
        ties = np.isclose(values, maximum, rtol=0.0, atol=1e-12)
        strongest_axis = ("axis_a", "axis_b", "axis_c")[int(np.flatnonzero(ties)[0])]
        records.append(
            {
                "hex_id": str(row.hex_id),
                "bridge_axis_a_strength": strengths["axis_a"],
                "bridge_axis_b_strength": strengths["axis_b"],
                "bridge_axis_c_strength": strengths["axis_c"],
                "bridge_strength_max": maximum,
                "bridge_strength_mean": float(np.mean(values)),
                "strongest_axis": strongest_axis,
                "strongest_axis_tie_count": int(ties.sum()),
                "adjacent_cells_missing": int(missing),
            }
        )
    indicators = pd.DataFrame.from_records(records)
    if indicators.empty:
        raise EcologicalNetworkError("Candidate population is empty")
    if edge_flags is not None:
        indicators[BOUNDARY_FLAG] = np.asarray(edge_flags, dtype=bool)
    indicators = indicators.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    return indicators[list(OUTPUT_COLUMNS if edge_flags is not None else OUTPUT_COLUMNS[:-1])]


def _quantile_summary(values: pd.Series) -> dict[str, float | None]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {
            key: None
            for key in ("min", "p10", "p25", "median", "p75", "p90", "p95", "p99", "max", "mean")
        }
    quantiles = numeric.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
        "min": float(numeric.min()),
        "p10": float(quantiles.loc[0.10]),
        "p25": float(quantiles.loc[0.25]),
        "median": float(quantiles.loc[0.50]),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "p99": float(quantiles.loc[0.99]),
        "max": float(numeric.max()),
        "mean": float(numeric.mean()),
    }


def _axis_distribution(values: pd.Series) -> dict[str, float | None]:
    summary = _quantile_summary(values)
    return {key: summary[key] for key in ("min", "median", "mean", "p75", "p90", "p95", "max")}


def _bridge_bins(values: pd.Series) -> dict[str, int]:
    numeric = pd.to_numeric(values, errors="coerce")
    return {
        "0": int((numeric == 0).sum()),
        ">0–0.10": int(((numeric > 0) & (numeric <= 0.10)).sum()),
        ">0.10–0.25": int(((numeric > 0.10) & (numeric <= 0.25)).sum()),
        ">0.25–0.50": int(((numeric > 0.25) & (numeric <= 0.50)).sum()),
        ">0.50–0.75": int(((numeric > 0.50) & (numeric <= 0.75)).sum()),
        ">0.75": int((numeric > 0.75).sum()),
    }


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, float | int | None]:
    pair = pd.concat(
        [pd.to_numeric(first, errors="coerce"), pd.to_numeric(second, errors="coerce")], axis=1
    ).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    return {
        "n": int(len(pair)),
        "pearson": float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="pearson")),
        "spearman": float(
            pair.iloc[:, 0].rank(method="average").corr(pair.iloc[:, 1].rank(method="average"))
        ),
    }


def _count_distribution(values: pd.Series, maximum: int) -> dict[str, int]:
    numeric = pd.to_numeric(values, errors="raise").astype(int)
    counts = numeric.value_counts().to_dict()
    return {str(value): int(counts.get(value, 0)) for value in range(maximum + 1)}


def _distribution_with_bins(values: pd.Series) -> dict[str, Any]:
    return {**_quantile_summary(values), "diagnostic_bins": _bridge_bins(values)}


def _normalise_boundary_flags(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise EcologicalNetworkError(f"{BOUNDARY_FLAG} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise EcologicalNetworkError(f"{BOUNDARY_FLAG} must contain only true/false values")
        return values.astype(bool)
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin(["true", "false"]).all():
        raise EcologicalNetworkError(f"{BOUNDARY_FLAG} must contain only true/false values")
    return normalised.eq("true")


def _validate_indicator_values(indicators: pd.DataFrame) -> None:
    _require_columns(indicators, OUTPUT_COLUMNS, "Network indicators")
    _normalise_ids(indicators, "Network indicators")
    numeric_columns = (*AXIS_STRENGTH_COLUMNS, *NETWORK_INDICATORS)
    for field in numeric_columns:
        _validate_fraction(indicators[field], field)
    if (indicators["strongest_axis_tie_count"] < 1).any() or (
        indicators["strongest_axis_tie_count"] > 3
    ).any():
        raise EcologicalNetworkError("strongest_axis_tie_count must lie in [1, 3]")
    if (indicators["adjacent_cells_missing"] < 0).any() or (
        indicators["adjacent_cells_missing"] > 6
    ).any():
        raise EcologicalNetworkError("adjacent_cells_missing must lie in [0, 6]")
    _normalise_boundary_flags(indicators[BOUNDARY_FLAG])


def _read_source(path: Path, layer: str, label: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path, layer=layer)
    except (OSError, ValueError) as exc:
        raise EcologicalNetworkError(f"Could not read {label}: {path}: {exc}") from exc


def _read_study_geometry(path: Path) -> BaseGeometry:
    study = _read_source(path, STUDY_AREA_LAYER, "study area")
    _validate_geometry(study, "Study area")
    if study.empty:
        raise EcologicalNetworkError("Study area is empty")
    return study.geometry.union_all()


def _reconcile_ids(
    output: pd.DataFrame, candidates: gpd.GeoDataFrame, analysis_units: gpd.GeoDataFrame
) -> dict[str, Any]:
    candidate_ids = candidates["hex_id"].astype(str)
    output_ids = output["hex_id"].astype(str)
    analysis_ids = analysis_units["hex_id"].astype(str)
    missing_ids = sorted(set(candidate_ids) - set(output_ids))
    extra_ids = sorted(set(output_ids) - set(candidate_ids))
    not_in_analysis = sorted(set(candidate_ids) - set(analysis_ids))
    return {
        "input_candidate_count": int(len(candidates)),
        "output_row_count": int(len(output)),
        "duplicate_output_ids": int(output_ids.duplicated().sum()),
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "candidates_not_represented_in_full_analysis_grid": not_in_analysis,
        "one_row_per_candidate": bool(
            len(output) == len(candidates)
            and not missing_ids
            and not extra_ids
            and output_ids.is_unique
        ),
    }


def _load_habitat_context(
    raw_path: Path, score_path: Path, candidate_ids: Iterable[str]
) -> pd.DataFrame:
    try:
        raw = pd.read_csv(raw_path)
        score = pd.read_csv(score_path)
    except (OSError, ValueError) as exc:
        raise EcologicalNetworkError(
            f"Could not read finalized Habitat Context artifacts: {exc}"
        ) from exc
    _require_columns(
        raw,
        ("hex_id", "habitat_context_local_fraction", "habitat_context_adjacent_fraction"),
        "Raw Habitat Context",
    )
    _require_columns(
        score,
        ("hex_id", "habitat_context_local_fraction", "habitat_context_score"),
        "Habitat Context score",
    )
    _normalise_ids(raw, "Raw Habitat Context")
    _normalise_ids(score, "Habitat Context score")
    raw["hex_id"] = raw["hex_id"].astype(str)
    score["hex_id"] = score["hex_id"].astype(str)
    for field in ("habitat_context_local_fraction", "habitat_context_adjacent_fraction"):
        _validate_fraction(raw[field], field)
    _validate_fraction(score["habitat_context_local_fraction"], "habitat_context_local_fraction")
    score_values = pd.to_numeric(score["habitat_context_score"], errors="coerce")
    if score_values.isna().any() or not np.all(np.isfinite(score_values)):
        raise EcologicalNetworkError("Habitat Context score contains non-finite values")
    expected = set(candidate_ids)
    if set(raw["hex_id"]) != expected or set(score["hex_id"]) != expected:
        raise EcologicalNetworkError("Habitat Context artifacts do not reconcile to candidate IDs")
    score_by_id = score.set_index("hex_id")
    raw_by_id = raw.set_index("hex_id")
    joined = raw_by_id[
        ["habitat_context_local_fraction", "habitat_context_adjacent_fraction"]
    ].join(
        score_by_id[["habitat_context_local_fraction", "habitat_context_score"]],
        lsuffix="_raw",
        rsuffix="_score",
        how="inner",
        validate="one_to_one",
    )
    if not np.allclose(
        joined["habitat_context_local_fraction_raw"],
        joined["habitat_context_local_fraction_score"],
        rtol=0,
        atol=1e-9,
    ):
        raise EcologicalNetworkError("Habitat Context raw and scored local fractions disagree")
    return joined.rename(
        columns={
            "habitat_context_local_fraction_raw": "habitat_context_local_fraction",
            "habitat_context_score": "habitat_context_score",
        }
    )


def _candidate_join(indicators: pd.DataFrame, candidates: gpd.GeoDataFrame) -> pd.DataFrame:
    candidate_frame = candidates.drop(columns="geometry").copy()
    candidate_frame["hex_id"] = candidate_frame["hex_id"].astype(str)
    return indicators.set_index("hex_id").join(
        candidate_frame.set_index("hex_id")[list(CANDIDATE_CORRELATION_FIELDS)],
        how="left",
        validate="one_to_one",
    )


def _geometry_sanity(
    analysis_units: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame,
    indicators: pd.DataFrame,
    sample_size: int = 10,
) -> dict[str, Any]:
    grid = {
        (int(row.grid_col), int(row.grid_row)): row
        for row in analysis_units.itertuples(index=False)
    }
    candidate_by_id = candidates.set_index(candidates["hex_id"].astype(str))
    eligible = indicators.loc[
        (indicators["adjacent_cells_missing"] == 0) & ~indicators[BOUNDARY_FLAG].astype(bool)
    ]
    sample_ids = eligible.sort_values("hex_id")["hex_id"].head(sample_size).tolist()
    sample_results: list[dict[str, Any]] = []
    pair_sum_passed = True
    focal_side_distance_passed = True
    opposing_distance_passed = True
    midpoint_passed = True
    for hex_id in sample_ids:
        focal = candidate_by_id.loc[hex_id].geometry.centroid
        coordinate = (
            int(candidate_by_id.loc[hex_id].grid_col),
            int(candidate_by_id.loc[hex_id].grid_row),
        )
        result: dict[str, Any] = {"hex_id": hex_id, "axes": {}}
        for axis, (side_1, side_2) in OPPOSITE_AXIS_PAIRS.items():
            first = grid[(coordinate[0] + side_1[0], coordinate[1] + side_1[1])].geometry.centroid
            second = grid[(coordinate[0] + side_2[0], coordinate[1] + side_2[1])].geometry.centroid
            vector_1 = np.array([first.x - focal.x, first.y - focal.y])
            vector_2 = np.array([second.x - focal.x, second.y - focal.y])
            pair_sum_m = float(np.linalg.norm(vector_1 + vector_2))
            first_distance_m = float(focal.distance(first))
            second_distance_m = float(focal.distance(second))
            opposing_distance_m = float(first.distance(second))
            midpoint_error_m = float(
                np.linalg.norm(
                    np.array([focal.x, focal.y])
                    - (np.array([first.x, first.y]) + np.array([second.x, second.y])) / 2.0
                )
            )
            pair_pass = pair_sum_m <= GEOMETRY_TOLERANCE_M
            side_pass = (
                abs(first_distance_m - 500.0) <= GEOMETRY_TOLERANCE_M
                and abs(second_distance_m - 500.0) <= GEOMETRY_TOLERANCE_M
            )
            opposing_pass = abs(opposing_distance_m - 1_000.0) <= GEOMETRY_TOLERANCE_M
            midpoint_pass = midpoint_error_m <= GEOMETRY_TOLERANCE_M
            pair_sum_passed &= pair_pass
            focal_side_distance_passed &= side_pass
            opposing_distance_passed &= opposing_pass
            midpoint_passed &= midpoint_pass
            result["axes"][axis] = {
                "pair_vector_sum_m": pair_sum_m,
                "side_1_distance_m": first_distance_m,
                "side_2_distance_m": second_distance_m,
                "opposing_centers_distance_m": opposing_distance_m,
                "focal_midpoint_error_m": midpoint_error_m,
                "pair_vectors_opposite": pair_pass,
                "both_sides_500m_from_focal": side_pass,
                "opposing_centers_1000m_apart": opposing_pass,
                "focal_at_midpoint": midpoint_pass,
            }
        sample_results.append(result)
    all_passed = bool(
        sample_ids
        and pair_sum_passed
        and focal_side_distance_passed
        and opposing_distance_passed
        and midpoint_passed
    )
    return {
        "tolerance_m": GEOMETRY_TOLERANCE_M,
        "requested_sample_count": sample_size,
        "sampled_interior_candidate_count": len(sample_ids),
        "sampled_candidate_ids": sample_ids,
        "pair_vectors_sum_to_zero": pair_sum_passed,
        "all_pair_members_500m_from_focal": focal_side_distance_passed,
        "opposing_centers_1000m_apart": opposing_distance_passed,
        "focal_centers_are_midpoints": midpoint_passed,
        "all_checks_passed": all_passed,
        "samples": sample_results,
    }


def _top_tail_boundary_counts(indicators: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for indicator in NETWORK_INDICATORS:
        ordered = indicators.sort_values(
            [indicator, "hex_id"], ascending=[False, True], kind="mergesort"
        )
        indicator_result: dict[str, Any] = {}
        for fraction, label in (
            (0.10, "top_10_percent"),
            (0.05, "top_5_percent"),
            (0.01, "top_1_percent"),
        ):
            count = max(1, math.ceil(len(ordered) * fraction))
            selected = ordered.head(count)
            edge_count = int(selected[BOUNDARY_FLAG].astype(bool).sum())
            indicator_result[label] = {
                "candidate_count": int(count),
                "boundary_edge_candidate_count": edge_count,
                "boundary_edge_candidate_percent": float(100.0 * edge_count / count),
            }
        result[indicator] = indicator_result
    return result


def _bridge_split(indicators: pd.DataFrame, mask: pd.Series, label: str) -> dict[str, Any]:
    subset = indicators.loc[mask]
    return {
        "label": label,
        "candidate_count": int(len(subset)),
        "missing_neighbor_distribution": _count_distribution(subset["adjacent_cells_missing"], 6),
        "bridge_strength_max": _distribution_with_bins(subset["bridge_strength_max"]),
        "bridge_strength_mean": _distribution_with_bins(subset["bridge_strength_mean"]),
    }


def _audit(
    indicators: pd.DataFrame,
    candidates: gpd.GeoDataFrame,
    analysis_units: gpd.GeoDataFrame,
    habitat_context: pd.DataFrame,
) -> dict[str, Any]:
    joined = (
        _candidate_join(indicators, candidates)
        .join(habitat_context, how="left", validate="one_to_one")
        .reset_index()
    )
    if joined.isna().any().any():
        raise EcologicalNetworkError(
            "Network audit join contains missing candidate or Habitat Context values"
        )
    correlations = {
        "max_vs_mean": _correlation(joined["bridge_strength_max"], joined["bridge_strength_mean"]),
        "against_habitat_context": {
            network_indicator: {
                "habitat_context_local_fraction": _correlation(
                    joined[network_indicator], joined["habitat_context_local_fraction"]
                ),
                "habitat_context_score": _correlation(
                    joined[network_indicator], joined["habitat_context_score"]
                ),
                "habitat_context_adjacent_fraction": _correlation(
                    joined[network_indicator], joined["habitat_context_adjacent_fraction"]
                ),
            }
            for network_indicator in NETWORK_INDICATORS
        },
        "against_candidate_composition": {
            network_indicator: {
                field: _correlation(joined[network_indicator], joined[field])
                for field in CANDIDATE_CORRELATION_FIELDS
            }
            for network_indicator in NETWORK_INDICATORS
        },
    }
    boundary = indicators[BOUNDARY_FLAG].astype(bool)
    missing = indicators["adjacent_cells_missing"]
    strongest_counts = indicators["strongest_axis"].value_counts().to_dict()
    tie_count = int((indicators["strongest_axis_tie_count"] >= 2).sum())
    balance = {
        "axis_a": int(strongest_counts.get("axis_a", 0)),
        "axis_b": int(strongest_counts.get("axis_b", 0)),
        "axis_c": int(strongest_counts.get("axis_c", 0)),
        "tied_between_2_or_more_axes": tie_count,
    }
    balance["percent_axis_a"] = float(100.0 * balance["axis_a"] / len(indicators))
    balance["percent_axis_b"] = float(100.0 * balance["axis_b"] / len(indicators))
    balance["percent_axis_c"] = float(100.0 * balance["axis_c"] / len(indicators))
    balance["percent_tied_between_2_or_more_axes"] = float(100.0 * tie_count / len(indicators))
    top_examples = joined.sort_values(
        ["bridge_strength_max", "hex_id"], ascending=[False, True], kind="mergesort"
    ).head(10)
    top_fields = [
        "hex_id",
        *AXIS_STRENGTH_COLUMNS,
        "strongest_axis",
        "candidate_fraction_of_terrestrial",
        "habitat_context_fraction_of_terrestrial",
        "habitat_context_local_fraction",
        BOUNDARY_FLAG,
    ]
    top_records = top_examples[top_fields].to_dict(orient="records")
    for record in top_records:
        record[BOUNDARY_FLAG] = bool(record[BOUNDARY_FLAG])
        for field in top_fields:
            if isinstance(record.get(field), np.generic):
                record[field] = record[field].item()
    return {
        "axis_strength_distributions": {
            column: _axis_distribution(indicators[column]) for column in AXIS_STRENGTH_COLUMNS
        },
        "bridge_strength_max_distribution": _distribution_with_bins(
            indicators["bridge_strength_max"]
        ),
        "bridge_strength_mean_distribution": _distribution_with_bins(
            indicators["bridge_strength_mean"]
        ),
        "zero_low_network_opportunity": {
            "all_three_axis_strengths_equal_zero": int(
                (indicators[list(AXIS_STRENGTH_COLUMNS)] == 0).all(axis=1).sum()
            ),
            "bridge_strength_max_greater_than_zero": int(
                (indicators["bridge_strength_max"] > 0).sum()
            ),
            "bridge_strength_max_at_least_0.25": int(
                (indicators["bridge_strength_max"] >= 0.25).sum()
            ),
            "bridge_strength_max_at_least_0.50": int(
                (indicators["bridge_strength_max"] >= 0.50).sum()
            ),
            "bridge_strength_max_at_least_0.75": int(
                (indicators["bridge_strength_max"] >= 0.75).sum()
            ),
        },
        "correlation_diagnostics": correlations,
        "strongest_axis_balance": balance,
        "missing_neighbor_distribution": _count_distribution(missing, 6),
        "missing_neighbor_effects": {
            "zero_missing_positions": _bridge_split(
                indicators, missing == 0, "0 missing positions"
            ),
            "one_or_more_missing_positions": _bridge_split(
                indicators, missing >= 1, ">=1 missing position"
            ),
            "boundary_edge": _bridge_split(indicators, boundary, "boundary_edge_flag=true"),
            "non_boundary_edge": _bridge_split(indicators, ~boundary, "boundary_edge_flag=false"),
        },
        "boundary_edge_candidates": int(boundary.sum()),
        "boundary_edge_candidate_percent": float(100.0 * boundary.mean()),
        "top_tail_boundary_edge_counts": _top_tail_boundary_counts(indicators),
        "top_network_examples": top_records,
        "analysis_grid_count": int(len(analysis_units)),
    }


def _write_csv(indicators: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    indicators.to_csv(
        temporary_path,
        index=False,
        columns=OUTPUT_COLUMNS,
        float_format=f"%.{CSV_FLOAT_PRECISION}f",
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


def build_ecological_network(
    analysis_units_path: Path = ANALYSIS_UNITS_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
    habitat_context_path: Path = HABITAT_CONTEXT_PATH,
    habitat_context_score_path: Path = HABITAT_CONTEXT_SCORE_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build, audit, write, and provenance the raw network indicators."""

    start = time.perf_counter()
    analysis_units = _read_source(analysis_units_path, ANALYSIS_UNITS_LAYER, "analysis units")
    candidate_units = _read_source(candidate_units_path, CANDIDATE_UNITS_LAYER, "candidate units")
    validation = validate_grid_inputs(analysis_units, candidate_units)
    study_geometry = _read_study_geometry(study_area_path)
    edge_flags = calculate_boundary_edge_flags(candidate_units, study_geometry)
    indicators = calculate_indicators(candidate_units, analysis_units, edge_flags=edge_flags)
    _validate_indicator_values(indicators)
    reconciliation = _reconcile_ids(indicators, candidate_units, analysis_units)
    if (
        not reconciliation["one_row_per_candidate"]
        or reconciliation["candidates_not_represented_in_full_analysis_grid"]
    ):
        raise EcologicalNetworkError("Network indicator output does not reconcile to candidates")
    habitat_context = _load_habitat_context(
        habitat_context_path, habitat_context_score_path, indicators["hex_id"]
    )
    audit = _audit(indicators, candidate_units, analysis_units, habitat_context)
    geometry_sanity = _geometry_sanity(analysis_units, candidate_units, indicators)
    if not geometry_sanity["all_checks_passed"]:
        raise EcologicalNetworkError("Actual generated hex-center geometry sanity checks failed")

    provenance: dict[str, Any] = {
        "component_working_name": "Ecological Network Context",
        "status": "RAW INDICATORS ONLY; NO FINAL COMPONENT INPUT SELECTED",
        "source": {
            "candidate_source": {
                "path": str(candidate_units_path),
                "layer": CANDIDATE_UNITS_LAYER,
                "role": "focal candidate population",
            },
            "analysis_unit_source": {
                "path": str(analysis_units_path),
                "layer": ANALYSIS_UNITS_LAYER,
                "role": "full surrounding terrestrial analysis grid",
            },
            "study_area_source": {"path": str(study_area_path), "layer": STUDY_AREA_LAYER},
            "habitat_context_raw_source": str(habitat_context_path),
            "habitat_context_score_source": str(habitat_context_score_path),
        },
        "underlying_habitat_proxy": (
            "habitat_context_fraction_of_terrestrial from each Step 6 terrestrial analysis unit; "
            "the Step 5 habitat_context_proxy land-cover role is used factually without thresholds."
        ),
        "grid_geometry_convention": {
            "crs": TARGET_CRS,
            "first_ring_offsets": [list(offset) for offset in FIRST_RING_OFFSETS],
            "axial_distance_formula": "max(abs(delta_col), abs(delta_row), abs(delta_col + delta_row))",
            "axis_geometry_note": "The three pairs are axes of the projected hex grid, not compass labels.",
        },
        "opposite_pair_definitions": {
            axis: {"side_1": list(pair[0]), "side_2": list(pair[1])}
            for axis, pair in OPPOSITE_AXIS_PAIRS.items()
        },
        "missing_neighbor_treatment": (
            "A missing adjacent analysis-grid position is assigned habitat fraction 0 for this "
            "bridging calculation. Missing positions are counted; values are not imputed across "
            "the study/county boundary."
        ),
        "formulas": {
            "neighbor_habitat_fraction": "habitat_context_pixels / terrestrial_pixels, equivalent to habitat_context_fraction_of_terrestrial",
            "axis_strength": "min(neighbor_habitat_fraction_side_1, neighbor_habitat_fraction_side_2)",
            "bridge_strength_max": "max(bridge_axis_a_strength, bridge_axis_b_strength, bridge_axis_c_strength)",
            "bridge_strength_mean": "mean(bridge_axis_a_strength, bridge_axis_b_strength, bridge_axis_c_strength)",
            "strongest_axis_tie": "all axes within 1e-12 of the maximum; first stable order axis_a, axis_b, axis_c is recorded",
        },
        "candidate_population_validation": {**validation, **reconciliation},
        "audit": audit,
        "geometry_sanity": geometry_sanity,
        "output": {
            "path": str(output_path),
            "columns": list(OUTPUT_COLUMNS),
            "row_count": int(len(indicators)),
            "no_geometry_copied": True,
        },
        "validation": {
            "fractions_finite_and_in_range": True,
            "focal_cell_excluded": True,
            "full_analysis_grid_used_for_surrounding_cells": True,
            "candidate_eligibility_not_used_for_neighbors": True,
            "no_habitat_threshold_used": True,
            "no_patch_or_connected_component_network": True,
            "no_normalization_or_scoring": True,
            "no_indicator_combination": True,
        },
        "caveats": [
            "This is a landscape-configuration proxy, not species connectivity.",
            "No habitat-quality weighting is applied.",
            "No patch-size threshold is applied.",
            "No resistance surface is used.",
            "No cross-county context is known or imputed; the source domain remains truncated at Skåne.",
            "No species-specific dispersal distance is modeled.",
            "Axis geometry is imposed by the 500 m analysis grid.",
            "Boundary-edge candidates are retained and neither excluded nor penalized.",
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - start,
    }
    _write_csv(indicators, output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["provenance_output"] = {"path": str(provenance_path), "size_bytes": 0}
    for _ in range(5):
        _write_json(provenance_path, provenance)
        actual_size = int(provenance_path.stat().st_size)
        if actual_size == provenance["provenance_output"]["size_bytes"]:
            break
        provenance["provenance_output"]["size_bytes"] = actual_size
    return indicators, provenance


def main() -> None:
    """Generate real-data raw Ecological Network Context indicators and audit them."""

    indicators, provenance = build_ecological_network()
    print(
        f"Ecological Network Context indicators: {provenance['output']['path']} "
        f"({provenance['output']['size_bytes']:,} bytes, {len(indicators):,} rows)"
    )
    print(f"Provenance: {provenance['provenance_output']['path']}")
    print(json.dumps(provenance["audit"], ensure_ascii=False, indent=2))
    print(json.dumps(provenance["geometry_sanity"], ensure_ascii=False, indent=2))
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
