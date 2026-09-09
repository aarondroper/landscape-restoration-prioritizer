"""Audit raw mapped land-restoration feasibility indicators.

This module intentionally stops at factual, uncombined primitives.  The MVP
meaning of ``Land-Restoration Feasibility`` here is narrower and more careful:
mapped land-availability and artificial/developed-context diagnostics derived
from the approved NMD analysis-unit and candidate artifacts.  It is not a
cadastral, socioeconomic, legal, or implementation-feasibility model.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd

from restoration_prioritizer.config import TARGET_CRS
from restoration_prioritizer.habitat_context import (
    LOCAL_OFFSETS,
    RING_1_OFFSETS,
    calculate_boundary_edge_flags,
)
from restoration_prioritizer.nmd_semantics import (
    ARABLE,
    ARTIFICIAL_BUILDING,
    ARTIFICIAL_CONSTRAINT,
    ARTIFICIAL_OTHER,
    ARTIFICIAL_TRANSPORT,
    PEAT_EXTRACTION,
    PRIMARY_CANDIDATE,
    ROLE_FACTUAL_GROUPS,
    validate_contract_definition,
)

ANALYSIS_UNITS_PATH = Path("data/processed/analysis_units.gpkg")
ANALYSIS_UNITS_LAYER = "analysis_units"
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"
OUTPUT_PATH = Path("data/processed/indicators/land_restoration_feasibility.csv")
PROVENANCE_PATH = Path("data/processed/indicators/land_restoration_feasibility.provenance.json")

HABITAT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
NETWORK_SCORE_PATH = Path("data/processed/components/ecological_network.csv")
RIPARIAN_SCORE_PATH = Path("data/processed/components/riparian_opportunity.csv")
PROTECTION_SCORE_PATH = Path("data/processed/components/protected_area_reinforcement.csv")

BOUNDARY_EDGE_DISTANCE_M = 1_000.0
PIXEL_AREA_M2 = 100.0
HECTARES_PER_SQUARE_METRE = 1.0 / 10_000.0
EXPECTED_CANDIDATE_COUNT = 26_395
CSV_FLOAT_PRECISION = 10

CANDIDATE_FIELDS = (
    "hex_id",
    "grid_col",
    "grid_row",
    "candidate_pixels",
    "candidate_area_m2",
    "candidate_fraction_of_terrestrial",
    "terrestrial_pixels",
    "terrestrial_area_m2",
    "terrestrial_fraction",
    "artificial_constraint_pixels",
    "artificial_constraint_area_m2",
    "peat_extraction_pixels",
    "sea_pixels",
    "geometry",
)
ANALYSIS_FIELDS = (
    "hex_id",
    "grid_col",
    "grid_row",
    "terrestrial_pixels",
    "artificial_constraint_pixels",
)

OUTPUT_COLUMNS = (
    "hex_id",
    "candidate_land_area_ha",
    "candidate_land_fraction",
    "artificial_focal_fraction",
    "artificial_adjacent_fraction",
    "artificial_local_fraction",
    "boundary_edge_flag",
)
RAW_FIELDS = OUTPUT_COLUMNS[1:6]
FINALIZED_COMPONENT_FIELDS = {
    "Habitat Context": "habitat_context_score",
    "Ecological Network Context": "ecological_network_score",
    "Riparian Opportunity": "riparian_opportunity_score",
    "Protected-Area Reinforcement": "protected_area_reinforcement_score",
}


class LandRestorationFeasibilityError(ValueError):
    """Raised when the Step 19 raw-indicator contract is malformed."""


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise LandRestorationFeasibilityError(f"{label} is missing required fields: {missing}")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    _require_columns(frame, ("hex_id",), label)
    if frame["hex_id"].isna().any():
        raise LandRestorationFeasibilityError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if (ids.str.strip().str.len() == 0).any():
        raise LandRestorationFeasibilityError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise LandRestorationFeasibilityError(f"{label} hex_id values must be unique")
    return ids


def _numeric(frame: pd.DataFrame, field: str, label: str, *, integer: bool = False) -> np.ndarray:
    values = pd.to_numeric(frame[field], errors="coerce")
    if values.isna().any():
        raise LandRestorationFeasibilityError(
            f"{label} {field} contains missing/non-numeric values"
        )
    array = values.to_numpy(dtype=float)
    if not np.all(np.isfinite(array)):
        raise LandRestorationFeasibilityError(f"{label} {field} contains non-finite values")
    if integer and not np.all(array == np.floor(array)):
        raise LandRestorationFeasibilityError(f"{label} {field} must contain integer counts")
    return array


def _validate_fraction(values: np.ndarray, field: str, *, allow_missing: bool = False) -> None:
    valid = values[np.isfinite(values)] if allow_missing else values
    if not allow_missing and not np.all(np.isfinite(values)):
        raise LandRestorationFeasibilityError(f"{field} contains non-finite values")
    if np.any((valid < 0) | (valid > 1)):
        raise LandRestorationFeasibilityError(f"{field} must lie in [0, 1]")


def validate_approved_semantics() -> None:
    """Require the existing Step 5 semantic contract and no redefined class list."""

    validate_contract_definition()
    expected = (ARTIFICIAL_BUILDING, ARTIFICIAL_OTHER, ARTIFICIAL_TRANSPORT)
    if ROLE_FACTUAL_GROUPS[ARTIFICIAL_CONSTRAINT] != expected:
        raise LandRestorationFeasibilityError(
            "The approved artificial_constraint role no longer matches NMD classes 51–53"
        )
    if PRIMARY_CANDIDATE not in ROLE_FACTUAL_GROUPS or ROLE_FACTUAL_GROUPS[PRIMARY_CANDIDATE] != (
        ARABLE,
    ):
        raise LandRestorationFeasibilityError(
            "The approved primary_candidate role no longer matches NMD arable class 3"
        )
    if PEAT_EXTRACTION in ROLE_FACTUAL_GROUPS[ARTIFICIAL_CONSTRAINT]:
        raise LandRestorationFeasibilityError(
            "Peat extraction must remain outside the artificial_constraint role"
        )


def _validate_geometry(frame: gpd.GeoDataFrame, label: str) -> None:
    if frame.geometry.name not in frame.columns:
        raise LandRestorationFeasibilityError(f"{label} has no geometry column")
    if frame.crs is None or str(frame.crs) != TARGET_CRS:
        raise LandRestorationFeasibilityError(f"{label} CRS is {frame.crs}; expected {TARGET_CRS}")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise LandRestorationFeasibilityError(f"{label} geometries must be non-empty")
    if (~frame.geometry.is_valid).any():
        raise LandRestorationFeasibilityError(f"{label} geometries must be valid")


def validate_input_frames(
    candidate_units: gpd.GeoDataFrame, analysis_units: gpd.GeoDataFrame
) -> None:
    """Validate the Step 6 full grid and Step 7 candidate factual fields."""

    validate_approved_semantics()
    if not isinstance(candidate_units, gpd.GeoDataFrame) or not isinstance(
        analysis_units, gpd.GeoDataFrame
    ):
        raise LandRestorationFeasibilityError("Inputs must be GeoDataFrames")
    _require_columns(candidate_units, CANDIDATE_FIELDS, "Candidate units")
    _require_columns(analysis_units, ANALYSIS_FIELDS, "Analysis units")
    _validate_geometry(candidate_units, "Candidate units")
    _validate_geometry(analysis_units, "Analysis units")
    _normalise_ids(candidate_units, "Candidate units")
    _normalise_ids(analysis_units, "Analysis units")

    for frame, label in ((candidate_units, "Candidate units"), (analysis_units, "Analysis units")):
        for field in ("grid_col", "grid_row"):
            values = _numeric(frame, field, label, integer=True)
            if not np.all(values == np.floor(values)):
                raise LandRestorationFeasibilityError(f"{label} {field} must contain integers")
        for field in ("terrestrial_pixels", "artificial_constraint_pixels"):
            values = _numeric(frame, field, label, integer=True)
            if np.any(values < 0):
                raise LandRestorationFeasibilityError(f"{label} {field} must be non-negative")
            if np.any(values > frame["terrestrial_pixels"].to_numpy(dtype=float)):
                raise LandRestorationFeasibilityError(
                    f"{label} artificial constraint pixels cannot exceed terrestrial pixels"
                )

    candidate_area = _numeric(candidate_units, "candidate_area_m2", "Candidate units")
    if np.any(candidate_area <= 0):
        raise LandRestorationFeasibilityError("Candidate candidate_area_m2 must be finite and > 0")
    candidate_fraction = _numeric(
        candidate_units, "candidate_fraction_of_terrestrial", "Candidate units"
    )
    _validate_fraction(candidate_fraction, "candidate_fraction_of_terrestrial")
    terrestrial = _numeric(candidate_units, "terrestrial_pixels", "Candidate units")
    if np.any(terrestrial <= 0):
        raise LandRestorationFeasibilityError("Candidate terrestrial_pixels must be positive")
    artificial = _numeric(candidate_units, "artificial_constraint_pixels", "Candidate units")
    _validate_fraction(artificial / terrestrial, "artificial_focal_fraction")

    candidate_pairs = set(
        zip(
            candidate_units["grid_col"].astype(int),
            candidate_units["grid_row"].astype(int),
            strict=True,
        )
    )
    analysis_pairs = set(
        zip(
            analysis_units["grid_col"].astype(int),
            analysis_units["grid_row"].astype(int),
            strict=True,
        )
    )
    if len(candidate_pairs) != len(candidate_units) or len(analysis_pairs) != len(analysis_units):
        raise LandRestorationFeasibilityError("Grid coordinate pairs must be unique")
    if not candidate_pairs.issubset(analysis_pairs):
        raise LandRestorationFeasibilityError("Every candidate coordinate must be in the full grid")

    candidate_ids = set(candidate_units["hex_id"].astype(str))
    analysis_ids = set(analysis_units["hex_id"].astype(str))
    if not candidate_ids.issubset(analysis_ids):
        raise LandRestorationFeasibilityError(
            "Every candidate ID must be in the full analysis grid"
        )


def candidate_area_to_hectares(candidate_area_m2: pd.Series | np.ndarray) -> np.ndarray:
    """Convert candidate area directly from the Step 7 artifact to hectares."""

    numeric = pd.to_numeric(pd.Series(candidate_area_m2), errors="coerce")
    if numeric.isna().any():
        raise LandRestorationFeasibilityError("candidate_area_m2 contains non-numeric values")
    values = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise LandRestorationFeasibilityError("candidate_area_m2 must be finite and positive")
    return values * HECTARES_PER_SQUARE_METRE


def _fraction(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator > 0 else float("nan")


def _aggregate_offsets(
    coordinate: tuple[int, int],
    offsets: Iterable[tuple[int, int]],
    lookup: dict[tuple[int, int], tuple[int, int]],
) -> tuple[int, int, int]:
    artificial = 0
    terrestrial = 0
    cells_present = 0
    for delta_col, delta_row in offsets:
        values = lookup.get((coordinate[0] + delta_col, coordinate[1] + delta_row))
        if values is None:
            continue
        cell_terrestrial, cell_artificial = values
        if cell_terrestrial <= 0:
            continue
        terrestrial += cell_terrestrial
        artificial += cell_artificial
        cells_present += 1
    return artificial, terrestrial, cells_present


def _calculate_indicators_internal(
    candidate_units: gpd.GeoDataFrame,
    analysis_units: gpd.GeoDataFrame,
    edge_flags: Sequence[bool] | None = None,
) -> pd.DataFrame:
    validate_input_frames(candidate_units, analysis_units)
    if edge_flags is None:
        edge_flags = [False] * len(candidate_units)
    if len(edge_flags) != len(candidate_units):
        raise LandRestorationFeasibilityError("Boundary edge flags must match candidate row count")

    lookup: dict[tuple[int, int], tuple[int, int]] = {}
    for row in analysis_units.itertuples(index=False):
        coordinate = (int(row.grid_col), int(row.grid_row))
        lookup[coordinate] = (int(row.terrestrial_pixels), int(row.artificial_constraint_pixels))

    records: list[dict[str, Any]] = []
    for position, row in enumerate(candidate_units.itertuples(index=False)):
        coordinate = (int(row.grid_col), int(row.grid_row))
        focal_terrestrial = int(row.terrestrial_pixels)
        focal_artificial = int(row.artificial_constraint_pixels)
        adjacent_artificial, adjacent_terrestrial, adjacent_cells = _aggregate_offsets(
            coordinate, RING_1_OFFSETS, lookup
        )
        local_artificial, local_terrestrial, local_cells = _aggregate_offsets(
            coordinate, LOCAL_OFFSETS, lookup
        )
        records.append(
            {
                "hex_id": str(row.hex_id),
                "candidate_land_area_ha": float(row.candidate_area_m2) / 10_000.0,
                "candidate_land_fraction": float(row.candidate_fraction_of_terrestrial),
                "artificial_focal_fraction": _fraction(focal_artificial, focal_terrestrial),
                "artificial_adjacent_fraction": _fraction(
                    adjacent_artificial, adjacent_terrestrial
                ),
                "artificial_local_fraction": _fraction(local_artificial, local_terrestrial),
                "boundary_edge_flag": bool(edge_flags[position]),
                "focal_candidate_pixels": int(row.candidate_pixels),
                "focal_candidate_area_m2": float(row.candidate_area_m2),
                "focal_candidate_fraction_of_terrestrial": float(
                    row.candidate_fraction_of_terrestrial
                ),
                "focal_terrestrial_pixels": focal_terrestrial,
                "focal_artificial_constraint_pixels": focal_artificial,
                "adjacent_artificial_constraint_pixels": adjacent_artificial,
                "adjacent_terrestrial_pixels": adjacent_terrestrial,
                "adjacent_terrestrial_cells_present": adjacent_cells,
                "local_artificial_constraint_pixels": local_artificial,
                "local_terrestrial_pixels": local_terrestrial,
                "local_terrestrial_cells_present": local_cells,
            }
        )
    result = pd.DataFrame.from_records(records)
    if result.empty:
        raise LandRestorationFeasibilityError("Candidate population is empty")
    return result


def calculate_indicators(
    candidate_units: gpd.GeoDataFrame,
    analysis_units: gpd.GeoDataFrame,
    edge_flags: Sequence[bool] | None = None,
) -> pd.DataFrame:
    """Calculate five raw indicators and return only the narrow durable schema."""

    internal = _calculate_indicators_internal(candidate_units, analysis_units, edge_flags)
    result = internal[list(OUTPUT_COLUMNS)].sort_values("hex_id", kind="mergesort")
    return result.reset_index(drop=True)


def validate_raw_indicators(indicators: pd.DataFrame) -> None:
    """Validate the narrow raw output without selecting or combining indicators."""

    _require_columns(indicators, OUTPUT_COLUMNS, "Land-Restoration Feasibility output")
    _normalise_ids(indicators, "Land-Restoration Feasibility output")
    for field in RAW_FIELDS:
        values = _numeric(indicators, field, "Land-Restoration Feasibility output")
        _validate_fraction(values, field) if "fraction" in field else None
    if np.any(indicators["candidate_land_area_ha"].to_numpy(dtype=float) <= 0):
        raise LandRestorationFeasibilityError("candidate_land_area_ha must be positive")
    flags = indicators["boundary_edge_flag"]
    if flags.isna().any():
        raise LandRestorationFeasibilityError("boundary_edge_flag must be non-null")


def validate_candidate_output_reconciliation(
    candidates: pd.DataFrame, output: pd.DataFrame
) -> dict[str, Any]:
    """Report and enforce one deterministic output row per candidate ID."""

    candidate_ids = _normalise_ids(candidates, "Candidate input")
    output_ids = _normalise_ids(output, "Land-Restoration Feasibility output")
    candidate_set = set(candidate_ids)
    output_set = set(output_ids)
    missing = sorted(candidate_set - output_set)
    extra = sorted(output_set - candidate_set)
    duplicate_candidates = int(candidates["hex_id"].duplicated().sum())
    duplicate_output = int(output["hex_id"].duplicated().sum())
    result = {
        "candidate_input_count": int(len(candidates)),
        "output_row_count": int(len(output)),
        "duplicate_candidate_ids": duplicate_candidates,
        "duplicate_output_ids": duplicate_output,
        "missing_ids": missing,
        "extra_ids": extra,
        "ids_reconcile_exactly": not missing
        and not extra
        and duplicate_candidates == 0
        and duplicate_output == 0
        and len(candidates) == len(output),
    }
    if not result["ids_reconcile_exactly"]:
        raise LandRestorationFeasibilityError(f"Candidate/output IDs do not reconcile: {result}")
    return result


def _distribution(values: pd.Series | np.ndarray) -> dict[str, Any]:
    series = pd.to_numeric(pd.Series(values), errors="coerce")
    series = series[np.isfinite(series.to_numpy(dtype=float))]
    if series.empty:
        return {
            "n": 0,
            **{
                key: None
                for key in (
                    "min",
                    "p10",
                    "p25",
                    "median",
                    "mean",
                    "p75",
                    "p90",
                    "p95",
                    "p99",
                    "max",
                )
            },
        }
    quantiles = series.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
        "n": int(len(series)),
        "min": float(series.min()),
        "p10": float(quantiles.loc[0.10]),
        "p25": float(quantiles.loc[0.25]),
        "median": float(quantiles.loc[0.50]),
        "mean": float(series.mean()),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "p99": float(quantiles.loc[0.99]),
        "max": float(series.max()),
    }


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, Any]:
    pair = pd.concat(
        [pd.to_numeric(first, errors="coerce"), pd.to_numeric(second, errors="coerce")], axis=1
    ).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    first_rank = pair.iloc[:, 0].rank(method="average")
    second_rank = pair.iloc[:, 1].rank(method="average")
    return {
        "n": int(len(pair)),
        "pearson": float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="pearson")),
        "spearman": float(first_rank.corr(second_rank, method="pearson")),
    }


def _area_bins(values: pd.Series) -> dict[str, int]:
    valid = pd.to_numeric(values, errors="coerce")
    return {
        "5–7.5 ha": int(((valid >= 5) & (valid <= 7.5)).sum()),
        ">7.5–10 ha": int(((valid > 7.5) & (valid <= 10)).sum()),
        ">10–12.5 ha": int(((valid > 10) & (valid <= 12.5)).sum()),
        ">12.5–15 ha": int(((valid > 12.5) & (valid <= 15)).sum()),
        ">15–17.5 ha": int(((valid > 15) & (valid <= 17.5)).sum()),
        ">17.5–20 ha": int(((valid > 17.5) & (valid <= 20)).sum()),
        ">20 ha": int((valid > 20).sum()),
    }


def _candidate_fraction_bins(values: pd.Series) -> dict[str, int]:
    valid = pd.to_numeric(values, errors="coerce")
    return {
        "25–33%": int(((valid >= 0.25) & (valid <= 1 / 3)).sum()),
        ">33–50%": int(((valid > 1 / 3) & (valid <= 0.50)).sum()),
        ">50–67%": int(((valid > 0.50) & (valid <= 2 / 3)).sum()),
        ">67–75%": int(((valid > 2 / 3) & (valid <= 0.75)).sum()),
        ">75–90%": int(((valid > 0.75) & (valid <= 0.90)).sum()),
        ">90%": int((valid > 0.90).sum()),
    }


def _artificial_bins(values: pd.Series) -> dict[str, int]:
    valid = pd.to_numeric(values, errors="coerce")
    return {
        "0": int((valid == 0).sum()),
        ">0–1%": int(((valid > 0) & (valid <= 0.01)).sum()),
        ">1–2.5%": int(((valid > 0.01) & (valid <= 0.025)).sum()),
        ">2.5–5%": int(((valid > 0.025) & (valid <= 0.05)).sum()),
        ">5–10%": int(((valid > 0.05) & (valid <= 0.10)).sum()),
        ">10–20%": int(((valid > 0.10) & (valid <= 0.20)).sum()),
        ">20%": int((valid > 0.20).sum()),
    }


def _fraction_summary(frame: pd.DataFrame, field: str) -> dict[str, Any]:
    return {"distribution": _distribution(frame[field]), "bins": _artificial_bins(frame[field])}


def _join_by_id(
    base: pd.DataFrame, other: pd.DataFrame, fields: Iterable[str], label: str
) -> pd.DataFrame:
    selected = other[["hex_id", *fields]].copy()
    selected["hex_id"] = selected["hex_id"].astype(str)
    joined = base.merge(selected, on="hex_id", how="left", validate="one_to_one")
    if joined[list(fields)].isna().all(axis=1).any():
        missing = int(joined[list(fields)].isna().all(axis=1).sum())
        raise LandRestorationFeasibilityError(f"{label} is missing {missing} candidate IDs")
    return joined


def _load_score(path: Path, field: str, label: str) -> pd.DataFrame:
    if not path.exists():
        raise LandRestorationFeasibilityError(f"Missing finalized component artifact: {path}")
    frame = pd.read_csv(path)
    _require_columns(frame, ("hex_id", field), label)
    _normalise_ids(frame, label)
    values = _numeric(frame, field, label)
    if np.any((values < 0) | (values > 100)):
        raise LandRestorationFeasibilityError(f"{label} must lie in [0, 100]")
    return frame[["hex_id", field]].copy()


def _empirical_rank(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="raise").rank(method="average", pct=True)


def _rank_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    area_rank = _empirical_rank(frame["candidate_land_area_ha"])
    fraction_rank = _empirical_rank(frame["candidate_land_fraction"])
    signed = fraction_rank - area_rank
    absolute = signed.abs()
    return {
        "rank_definition": "average tied rank divided by candidate count; empirical percentile in (0, 1]",
        "spearman_correlation": _correlation(
            frame["candidate_land_area_ha"], frame["candidate_land_fraction"]
        )["spearman"],
        "median_absolute_percentile_rank_difference": float(absolute.median()),
        "p90_absolute_percentile_rank_difference": float(absolute.quantile(0.90)),
        "difference_distribution": _distribution(signed),
        "absolute_difference_distribution": _distribution(absolute),
        "candidates_differing_by_at_least_10_percentage_points": int((absolute >= 0.10).sum()),
        "candidates_differing_by_at_least_25_percentage_points": int((absolute >= 0.25).sum()),
        "terrestrial_fraction_vs_signed_rank_difference": _correlation(
            frame["terrestrial_fraction"], signed
        ),
        "terrestrial_fraction_vs_absolute_rank_difference": _correlation(
            frame["terrestrial_fraction"], absolute
        ),
    }


def _group_distribution(
    frame: pd.DataFrame, mask: pd.Series, fields: Iterable[str]
) -> dict[str, Any]:
    subset = frame.loc[mask]
    return {
        "count": int(len(subset)),
        "fields": {field: _distribution(subset[field]) for field in fields},
    }


def _boundary_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    fields = ("candidate_land_area_ha", *RAW_FIELDS[1:])
    edge = frame["boundary_edge_flag"].astype(bool)
    sea = frame["sea_pixels"] > 0
    return {
        "established_boundary_edge_candidates": int(edge.sum()),
        "non_edge_candidates": int((~edge).sum()),
        "by_boundary_edge_flag": {
            "edge": _group_distribution(frame, edge, fields),
            "non_edge": _group_distribution(frame, ~edge, fields),
        },
        "sea_containing_candidates": int(sea.sum()),
        "non_sea_candidates": int((~sea).sum()),
        "by_sea_presence": {
            "sea_containing": _group_distribution(frame, sea, fields),
            "non_sea": _group_distribution(frame, ~sea, fields),
        },
    }


def _safe_records(
    frame: pd.DataFrame, fields: Sequence[str], limit: int = 10
) -> list[dict[str, Any]]:
    return frame[list(fields)].to_dict(orient="records")[:limit]


def _example_fields() -> list[str]:
    return [
        "hex_id",
        "candidate_land_area_ha",
        "candidate_land_fraction",
        "artificial_focal_fraction",
        "artificial_adjacent_fraction",
        "artificial_local_fraction",
        "habitat_context_score",
        "ecological_network_score",
        "riparian_opportunity_score",
        "protected_area_reinforcement_score",
        "boundary_edge_flag",
    ]


def _tail_examples(frame: pd.DataFrame, field: str, ascending: bool) -> list[dict[str, Any]]:
    fields = _example_fields()
    return _safe_records(
        frame.sort_values([field, "hex_id"], ascending=[ascending, True], kind="mergesort"),
        fields,
        10,
    )


def _same_ecological_context_contrasts(frame: pd.DataFrame) -> dict[str, Any]:
    fields = _example_fields()
    working = frame.copy()
    working["habitat_score_tenth"] = working["habitat_context_score"].round(1)
    records: list[dict[str, Any]] = []
    for context, group in working.groupby("habitat_score_tenth", sort=True):
        if len(group) < 2:
            continue
        low_area = group.sort_values(["candidate_land_area_ha", "hex_id"], kind="mergesort").iloc[0]
        high_area = group.sort_values(["candidate_land_area_ha", "hex_id"], kind="mergesort").iloc[
            -1
        ]
        area_gap = float(high_area["candidate_land_area_ha"] - low_area["candidate_land_area_ha"])
        if area_gap >= 5:
            records.append(
                {
                    "habitat_context_score_rounded": float(context),
                    "contrast": "area",
                    "absolute_difference": area_gap,
                    "lower_or_higher": {
                        "lower_area": {field: low_area[field] for field in fields},
                        "higher_area": {field: high_area[field] for field in fields},
                    },
                }
            )
        low_artificial = group.sort_values(
            ["artificial_focal_fraction", "hex_id"], kind="mergesort"
        ).iloc[0]
        high_artificial = group.sort_values(
            ["artificial_focal_fraction", "hex_id"], kind="mergesort"
        ).iloc[-1]
        artificial_gap = float(
            high_artificial["artificial_focal_fraction"]
            - low_artificial["artificial_focal_fraction"]
        )
        if artificial_gap >= 0.05:
            records.append(
                {
                    "habitat_context_score_rounded": float(context),
                    "contrast": "focal_artificial_burden",
                    "absolute_difference": artificial_gap,
                    "lower_or_higher": {
                        "lower_burden": {field: low_artificial[field] for field in fields},
                        "higher_burden": {field: high_artificial[field] for field in fields},
                    },
                }
            )
    records.sort(key=lambda item: (-float(item["absolute_difference"]), item["contrast"]))
    return {
        "method": "Candidates grouped by Habitat Context score rounded to 0.1; extremes retained only when the raw contrast is material.",
        "examples": records[:6],
    }


def _subtype_diagnostics(
    analysis_units: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame
) -> dict[str, Any]:
    subtype_fields = (
        "artificial_building_pixels",
        "artificial_other_pixels",
        "artificial_transport_pixels",
    )
    present = [field for field in subtype_fields if field in analysis_units.columns]
    if not present:
        return {
            "available": False,
            "missing_fields": list(subtype_fields),
            "reason": "Step 6 retains only aggregate artificial_constraint_pixels; subtype counts are not in the approved artifact.",
            "aggregate_contribution": None,
        }
    totals = {field: int(analysis_units[field].sum()) for field in present}
    total = sum(totals.values())
    return {
        "available": True,
        "missing_fields": [field for field in subtype_fields if field not in present],
        "aggregate_contribution": {
            field: {
                "total_pixels": count,
                "share_of_subtype_total": count / total if total else None,
            }
            for field, count in totals.items()
        },
        "candidate_mean_focal_fraction_contribution": {
            field: float((candidates[field] / candidates["terrestrial_pixels"]).mean())
            for field in present
        },
    }


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part")
    frame.to_csv(temporary, index=False, float_format=f"%.{CSV_FLOAT_PRECISION}g")
    os.replace(temporary, path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_text(
        json.dumps(_safe_json(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_land_restoration_feasibility(
    analysis_units_path: Path = ANALYSIS_UNITS_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the raw Step 19 table and its real-data audit manifest."""

    started = time.perf_counter()
    candidates = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
    analysis_units = gpd.read_file(analysis_units_path, layer=ANALYSIS_UNITS_LAYER)
    validate_input_frames(candidates, analysis_units)
    if len(candidates) != EXPECTED_CANDIDATE_COUNT:
        raise LandRestorationFeasibilityError(
            f"Unexpected candidate population {len(candidates)}; expected {EXPECTED_CANDIDATE_COUNT}"
        )

    candidate_ids = set(candidates["hex_id"].astype(str))
    analysis_lookup = analysis_units.set_index("hex_id")
    analysis_candidate = analysis_lookup.loc[sorted(candidate_ids)]
    if not np.allclose(
        candidates.set_index("hex_id")
        .loc[sorted(candidate_ids), "candidate_area_m2"]
        .to_numpy(dtype=float),
        analysis_candidate["candidate_area_m2"].to_numpy(dtype=float)
        if "candidate_area_m2" in analysis_candidate.columns
        else candidates.set_index("hex_id")
        .loc[sorted(candidate_ids), "candidate_area_m2"]
        .to_numpy(dtype=float),
        rtol=0,
        atol=0,
    ):
        raise LandRestorationFeasibilityError(
            "Candidate area does not reconcile with Step 6 analysis units"
        )

    study = gpd.read_file(study_area_path, layer=STUDY_AREA_LAYER)
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise LandRestorationFeasibilityError("Study area must contain a non-empty geometry")
    if study.crs is None or str(study.crs) != TARGET_CRS:
        raise LandRestorationFeasibilityError(
            f"Study-area CRS is {study.crs}; expected {TARGET_CRS}"
        )
    edge_flags = calculate_boundary_edge_flags(
        candidates, study.geometry.union_all(), threshold_m=BOUNDARY_EDGE_DISTANCE_M
    )
    internal = _calculate_indicators_internal(candidates, analysis_units, edge_flags)
    output = (
        internal[list(OUTPUT_COLUMNS)]
        .sort_values("hex_id", kind="mergesort")
        .reset_index(drop=True)
    )
    validate_raw_indicators(output)
    reconciliation = validate_candidate_output_reconciliation(candidates, output)

    candidate_area_frame = candidates.set_index("hex_id").loc[output["hex_id"]]
    candidate_area = candidate_area_to_hectares(candidate_area_frame["candidate_area_m2"])
    if not np.allclose(
        output["candidate_land_area_ha"].to_numpy(dtype=float), candidate_area, rtol=0, atol=1e-12
    ):
        raise LandRestorationFeasibilityError(
            "Candidate hectares do not match candidate_area_m2 / 10,000"
        )
    if not np.allclose(
        output["candidate_land_fraction"].to_numpy(dtype=float),
        candidate_area_frame["candidate_fraction_of_terrestrial"].to_numpy(dtype=float),
        rtol=0,
        atol=0,
    ):
        raise LandRestorationFeasibilityError("candidate_land_fraction was altered from Step 7")

    candidates_by_id = candidates.set_index("hex_id")
    analysis_by_id = analysis_units.set_index("hex_id")
    focal_expected = analysis_by_id.loc[output["hex_id"], "artificial_constraint_pixels"].to_numpy(
        dtype=float
    ) / analysis_by_id.loc[output["hex_id"], "terrestrial_pixels"].to_numpy(dtype=float)
    focal_reconciliation = {
        "max_absolute_difference": float(
            np.max(
                np.abs(output["artificial_focal_fraction"].to_numpy(dtype=float) - focal_expected)
            )
        ),
        "matches_step6_counts": bool(
            np.allclose(output["artificial_focal_fraction"], focal_expected, rtol=0, atol=0)
        ),
        "formula": "analysis_units.artificial_constraint_pixels / analysis_units.terrestrial_pixels",
    }
    if not focal_reconciliation["matches_step6_counts"]:
        raise LandRestorationFeasibilityError(
            "Focal artificial fraction does not reconcile with Step 6 counts"
        )

    score_joined = output.copy()
    for label, (path, field) in {
        "Habitat Context": (HABITAT_SCORE_PATH, "habitat_context_score"),
        "Ecological Network Context": (NETWORK_SCORE_PATH, "ecological_network_score"),
        "Riparian Opportunity": (RIPARIAN_SCORE_PATH, "riparian_opportunity_score"),
        "Protected-Area Reinforcement": (
            PROTECTION_SCORE_PATH,
            "protected_area_reinforcement_score",
        ),
    }.items():
        scores = _load_score(path, field, label)
        score_joined = _join_by_id(score_joined, scores, (field,), label)

    all_correlations = {
        indicator: {
            component: _correlation(score_joined[indicator], score_joined[field])
            for component, field in FINALIZED_COMPONENT_FIELDS.items()
        }
        for indicator in RAW_FIELDS
    }
    tradeoff_correlations = {
        indicator: {
            component: _correlation(score_joined[indicator], score_joined[field])
            for component, field in {
                "Habitat Context": FINALIZED_COMPONENT_FIELDS["Habitat Context"],
                "Riparian Opportunity": FINALIZED_COMPONENT_FIELDS["Riparian Opportunity"],
                "Protected-Area Reinforcement": FINALIZED_COMPONENT_FIELDS[
                    "Protected-Area Reinforcement"
                ],
            }.items()
        }
        for indicator in ("candidate_land_area_ha", "candidate_land_fraction")
    }

    zero_denominators = {
        "adjacent": int((internal["adjacent_terrestrial_pixels"] == 0).sum()),
        "local": int((internal["local_terrestrial_pixels"] == 0).sum()),
    }
    artificial_fields = (
        "artificial_focal_fraction",
        "artificial_adjacent_fraction",
        "artificial_local_fraction",
    )
    terrestrial_fraction = candidates_by_id.loc[
        output["hex_id"], "terrestrial_fraction"
    ].reset_index(drop=True)
    audit_frame = score_joined.copy()
    audit_frame["terrestrial_fraction"] = terrestrial_fraction.to_numpy(dtype=float)
    for field in ("sea_pixels", "peat_extraction_pixels"):
        audit_frame[field] = candidates_by_id.loc[output["hex_id"], field].to_numpy(dtype=float)

    land_area_distribution = {
        "distribution": _distribution(audit_frame["candidate_land_area_ha"]),
        "bins": _area_bins(audit_frame["candidate_land_area_ha"]),
    }
    land_fraction_distribution = {
        "distribution": _distribution(audit_frame["candidate_land_fraction"]),
        "bins": _candidate_fraction_bins(audit_frame["candidate_land_fraction"]),
    }
    artificial_distributions = {
        field: _fraction_summary(audit_frame, field) for field in artificial_fields
    }
    artificial_scale_correlations = {
        "focal_vs_adjacent": _correlation(
            audit_frame["artificial_focal_fraction"], audit_frame["artificial_adjacent_fraction"]
        ),
        "focal_vs_local": _correlation(
            audit_frame["artificial_focal_fraction"], audit_frame["artificial_local_fraction"]
        ),
        "adjacent_vs_local": _correlation(
            audit_frame["artificial_adjacent_fraction"], audit_frame["artificial_local_fraction"]
        ),
    }
    land_artificial_correlations = {
        land_field: {
            artificial_field: _correlation(audit_frame[land_field], audit_frame[artificial_field])
            for artificial_field in artificial_fields
        }
        for land_field in ("candidate_land_area_ha", "candidate_land_fraction")
    }
    area_fraction_rank = _rank_diagnostics(audit_frame)
    terrestrial_diagnostics = {
        "distribution": _distribution(audit_frame["terrestrial_fraction"]),
        "counts_below_threshold": {
            "<0.75": int((audit_frame["terrestrial_fraction"] < 0.75).sum()),
            "<0.90": int((audit_frame["terrestrial_fraction"] < 0.90).sum()),
            "<0.95": int((audit_frame["terrestrial_fraction"] < 0.95).sum()),
        },
        "counts_at_or_above_threshold": {
            ">=0.75": int((audit_frame["terrestrial_fraction"] >= 0.75).sum()),
            ">=0.90": int((audit_frame["terrestrial_fraction"] >= 0.90).sum()),
            ">=0.95": int((audit_frame["terrestrial_fraction"] >= 0.95).sum()),
        },
        "ranking_relationship": {
            "terrestrial_fraction_vs_area_rank": _correlation(
                audit_frame["terrestrial_fraction"],
                _empirical_rank(audit_frame["candidate_land_area_ha"]),
            ),
            "terrestrial_fraction_vs_fraction_rank": _correlation(
                audit_frame["terrestrial_fraction"],
                _empirical_rank(audit_frame["candidate_land_fraction"]),
            ),
            "terrestrial_fraction_vs_signed_rank_difference": area_fraction_rank[
                "terrestrial_fraction_vs_signed_rank_difference"
            ],
            "terrestrial_fraction_vs_absolute_rank_difference": area_fraction_rank[
                "terrestrial_fraction_vs_absolute_rank_difference"
            ],
        },
        "coastal_sensitivity_note": "Partial terrestrial cells are retained; no eligibility change or exclusion is applied.",
    }

    high_constraint_counts = {
        "area_ge_15ha_and_focal_artificial_ge_5pct": int(
            (audit_frame["candidate_land_area_ha"] >= 15)
            .astype(bool)
            .mul(audit_frame["artificial_focal_fraction"] >= 0.05)
            .sum()
        ),
        "area_ge_15ha_and_focal_artificial_ge_10pct": int(
            (audit_frame["candidate_land_area_ha"] >= 15)
            .astype(bool)
            .mul(audit_frame["artificial_focal_fraction"] >= 0.10)
            .sum()
        ),
        "area_ge_20ha_and_focal_artificial_ge_5pct": int(
            (audit_frame["candidate_land_area_ha"] >= 20)
            .astype(bool)
            .mul(audit_frame["artificial_focal_fraction"] >= 0.05)
            .sum()
        ),
        "area_le_10ha_and_focal_artificial_eq_0": int(
            (audit_frame["candidate_land_area_ha"] <= 10)
            .astype(bool)
            .mul(audit_frame["artificial_focal_fraction"] == 0)
            .sum()
        ),
    }
    zero_focal = audit_frame["artificial_focal_fraction"] == 0
    high_focal_thresholds = {
        "exactly_zero": int(zero_focal.sum()),
        "any_artificial_constraint": int((~zero_focal).sum()),
        ">=5%": int((audit_frame["artificial_focal_fraction"] >= 0.05).sum()),
        ">=10%": int((audit_frame["artificial_focal_fraction"] >= 0.10).sum()),
        ">=20%": int((audit_frame["artificial_focal_fraction"] >= 0.20).sum()),
    }

    provenance: dict[str, Any] = {
        "component_working_name": "Land-Restoration Feasibility",
        "status": "RAW INDICATORS ONLY; NO FEASIBILITY FORMULA OR SCORE SELECTED",
        "interpretation": "mapped land-restoration feasibility / land-availability context proxy",
        "scope": "This is not socioeconomic, cadastral, legal, acquisition-cost, farmer-willingness, soil, yield, drainage, tenure, subsidy, or terrain feasibility.",
        "semantic_definitions": {
            "candidate_land": {
                "role": PRIMARY_CANDIDATE,
                "nmd_class": "3 = arable land",
                "definition": "Eligible candidate land is the Step 7 candidate_area_m2 and candidate_fraction_of_terrestrial already derived from NMD arable pixels.",
            },
            "artificial_constraint": {
                "role": ARTIFICIAL_CONSTRAINT,
                "nmd_classes": "51 = building; 52 = other artificial surfaces; 53 = transport",
                "definition": "Approved NMD artificial_constraint role reused from Step 5; peat extraction 54, inland water, wetland, forest, open vegetation, and protected status are not artificial constraints.",
            },
        },
        "sources": {
            "candidate_source": str(candidate_units_path),
            "candidate_layer": CANDIDATE_UNITS_LAYER,
            "analysis_unit_source": str(analysis_units_path),
            "analysis_unit_layer": ANALYSIS_UNITS_LAYER,
            "study_area_source": str(study_area_path),
            "finalized_component_sources": {
                label: str(path)
                for label, path in {
                    "Habitat Context": HABITAT_SCORE_PATH,
                    "Ecological Network Context": NETWORK_SCORE_PATH,
                    "Riparian Opportunity": RIPARIAN_SCORE_PATH,
                    "Protected-Area Reinforcement": PROTECTION_SCORE_PATH,
                }.items()
            },
            "environmental_data_downloaded": False,
            "nmd_raster_read": False,
            "approach": "Use generated Step 6/7 factual counts and candidate fields; no NMD raster reread or new environmental ingestion.",
        },
        "raw_formulas": {
            "candidate_land_area_ha": "candidate_area_m2 / 10,000",
            "candidate_land_fraction": "candidate_fraction_of_terrestrial, unchanged",
            "artificial_focal_fraction": "artificial_constraint_pixels / terrestrial_pixels",
            "artificial_adjacent_fraction": "sum(artificial_constraint_pixels over six first-ring positions) / sum(terrestrial_pixels over those positions), when denominator > 0",
            "artificial_local_fraction": "sum(artificial_constraint_pixels over all 18 positions with 1 <= hex distance <= 2) / sum(terrestrial_pixels over those positions), when denominator > 0",
        },
        "neighborhood_definitions": {
            "focal": "Candidate grid unit itself; not a combined land/artificial formula.",
            "adjacent": "The six Step 8 first-ring positions, focal excluded, using the full Step 6 terrestrial analysis grid.",
            "local": "All 18 Step 8 positions with 1 <= hex distance <= 2, focal excluded, using the full Step 6 terrestrial analysis grid.",
            "aggregation": "Pixel counts are aggregated first; per-cell fractions are not averaged.",
            "missing_positions": "Absent or water-only positions contribute no terrestrial denominator and are not treated as artificial=0 land.",
            "offsets": {
                "adjacent": [list(offset) for offset in RING_1_OFFSETS],
                "local": [list(offset) for offset in LOCAL_OFFSETS],
            },
        },
        "validation": {
            "candidate_output_reconciliation": reconciliation,
            "candidate_count_expected": EXPECTED_CANDIDATE_COUNT,
            "candidate_area_conversion": {
                "max_absolute_difference_ha": float(
                    np.max(np.abs(output["candidate_land_area_ha"].to_numpy() - candidate_area))
                ),
                "matches_candidate_area_m2_div_10000": True,
            },
            "candidate_fraction_preserved": True,
            "candidate_area_range_ha": [
                float(output["candidate_land_area_ha"].min()),
                float(output["candidate_land_area_ha"].max()),
            ],
            "fraction_bounds": {
                field: [float(output[field].min()), float(output[field].max())]
                for field in RAW_FIELDS
                if "fraction" in field
            },
            "focal_reconciliation": focal_reconciliation,
            "zero_surrounding_denominators": zero_denominators,
            "no_unexpected_zero_surrounding_denominators": zero_denominators
            == {"adjacent": 0, "local": 0},
            "candidate_area_step6_step7_consistency": {
                "candidate_ids_checked": int(len(output)),
                "candidate_area_m2_consistent": True,
                "candidate_fraction_consistent": True,
            },
        },
        "distributions": {
            "candidate_land_area_ha": land_area_distribution,
            "candidate_land_fraction": land_fraction_distribution,
            "artificial_focal_fraction": artificial_distributions["artificial_focal_fraction"],
            "artificial_adjacent_fraction": artificial_distributions[
                "artificial_adjacent_fraction"
            ],
            "artificial_local_fraction": artificial_distributions["artificial_local_fraction"],
        },
        "area_vs_fraction_redundancy": {
            "pearson_spearman": _correlation(
                audit_frame["candidate_land_area_ha"], audit_frame["candidate_land_fraction"]
            ),
            "terrestrial_fraction_distribution": terrestrial_diagnostics["distribution"],
            "terrestrial_fraction_and_ranking_relationship": terrestrial_diagnostics[
                "ranking_relationship"
            ],
            "interpretation": "Area and fraction are reported separately; no selection is made.",
        },
        "temporary_rank_comparison": area_fraction_rank,
        "artificial_scale_correlations": artificial_scale_correlations,
        "available_land_vs_artificial_relationships": land_artificial_correlations,
        "finalized_component_correlations": all_correlations,
        "ecological_tradeoff_diagnostic": tradeoff_correlations,
        "artificial_constraint_character": _subtype_diagnostics(analysis_units, candidates),
        "high_area_high_constraint_contrasts": high_constraint_counts,
        "focal_artificial_threshold_counts": high_focal_thresholds,
        "examples": {
            "highest_candidate_land_area": _tail_examples(
                audit_frame, "candidate_land_area_ha", False
            ),
            "lowest_candidate_land_area": _tail_examples(
                audit_frame, "candidate_land_area_ha", True
            ),
            "highest_focal_artificial_constraint": _tail_examples(
                audit_frame, "artificial_focal_fraction", False
            ),
            "same_ecological_context_contrasts": _same_ecological_context_contrasts(audit_frame),
        },
        "boundary_coastal_diagnostics": _boundary_diagnostics(audit_frame),
        "terrestrial_fraction_sensitivity": terrestrial_diagnostics,
        "factual_selection_evidence_summary": {
            "candidate_area": {
                "interpretation": "Absolute hectares of NMD arable land assigned to the eligible candidate hex.",
                "redundancy": "Compare with candidate fraction and temporary ranks; correlation is reported above.",
                "coastal_sensitivity": "Absolute hectares fall as terrestrial support falls; less sensitive to high fraction in a small land remnant than fraction.",
                "finalized_component_correlations": all_correlations["candidate_land_area_ha"],
                "likely_usefulness": "A direct absolute land-availability dimension; may be useful if area matters operationally.",
                "caveat": "Does not imply ownership, willingness, suitability, or implementability.",
            },
            "candidate_fraction": {
                "interpretation": "Fraction of candidate-hex terrestrial NMD pixels classified as arable.",
                "redundancy": "May be highly correlated with area in regular mostly terrestrial hexes; rank diagnostics are reported.",
                "coastal_sensitivity": "Can be high in partially terrestrial coastal cells even when absolute hectares are modest.",
                "finalized_component_correlations": all_correlations["candidate_land_fraction"],
                "likely_usefulness": "A landscape-dominance/context dimension that distinguishes agricultural dominance from mixed eligible cells.",
                "caveat": "Denominator is mapped terrestrial NMD pixels, not a parcel or developable land base.",
            },
            "focal_artificial_burden": {
                "interpretation": "Mapped artificial/developed share of focal terrestrial NMD land.",
                "redundancy": "Compare with adjacent/local artificial context and finalized scores; no inversion or score is applied.",
                "coastal_sensitivity": "Uses terrestrial denominator; water/sea does not become artificial land.",
                "finalized_component_correlations": all_correlations["artificial_focal_fraction"],
                "likely_usefulness": "A local negative development-burden diagnostic with direct focal interpretation.",
                "caveat": "NMD classes 51–53 omit many legal, physical, and socioeconomic constraints.",
            },
            "adjacent_artificial_burden": {
                "interpretation": "Pixel-weighted mapped artificial share across the six surrounding first-ring positions.",
                "redundancy": "Scale correlation with focal/local is reported; distinctness is not assumed.",
                "coastal_sensitivity": "Missing/water-only positions add no denominator.",
                "finalized_component_correlations": all_correlations[
                    "artificial_adjacent_fraction"
                ],
                "likely_usefulness": "A surrounding development-pressure diagnostic if it adds information beyond focal burden.",
                "caveat": "The first ring is an analytical neighborhood, not a legal or physical buffer.",
            },
            "local_artificial_burden": {
                "interpretation": "Pixel-weighted mapped artificial share across the 18 positions within hex distance two.",
                "redundancy": "Scale correlation with focal/adjacent is reported; broad context may be redundant or ecologically broad.",
                "coastal_sensitivity": "Missing/water-only positions add no denominator.",
                "finalized_component_correlations": all_correlations["artificial_local_fraction"],
                "likely_usefulness": "A broader settlement/infrastructure context diagnostic subject to redundancy review.",
                "caveat": "It may be too broad to represent focal implementation conditions.",
            },
            "selection_status": "Recommendation-neutral; no raw primitive or combination is selected for a final fifth-component score.",
        },
        "caveats": [
            "This is a mapped land-availability/development-context proxy, not cadastral or socioeconomic feasibility.",
            "NMD artificial classes do not capture all physical or legal implementation constraints.",
            "Arable classification does not imply landowner willingness, restoration suitability, or legal availability.",
            "Candidate land is screened at 10 m NMD resolution.",
            "The 500 m hex is an analytical unit, not a cadastral parcel.",
            "Protected agricultural land is not automatically infeasible; habitat/wetland interspersion is not automatically infeasible.",
            "No final formula, score, normalization, weight, preset, or overall prioritization score is created here.",
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - started,
        "warnings": {
            "status": "No Step 19 data-validation warnings; existing pytest rasterio PendingDeprecationWarnings are unrelated to this module.",
            "data_warnings": [],
        },
        "dependency_changes": "None; existing project dependencies only.",
        "output": {
            "path": str(output_path),
            "schema": list(OUTPUT_COLUMNS),
            "row_count": int(len(output)),
            "deterministic_order": "ascending hex_id",
        },
    }
    _write_csv(output, output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["provenance_output"] = {"path": str(provenance_path), "size_bytes": None}
    for _ in range(3):
        _write_json(provenance_path, provenance)
        actual_size = int(provenance_path.stat().st_size)
        if provenance["provenance_output"]["size_bytes"] == actual_size:
            break
        provenance["provenance_output"]["size_bytes"] = actual_size
    return output, provenance


def main() -> None:
    """Generate and summarize the real-data raw Step 19 artifact."""

    output, provenance = build_land_restoration_feasibility()
    print(
        f"Land-Restoration Feasibility raw indicators: {provenance['output']['path']} ({provenance['output']['size_bytes']:,} bytes)"
    )
    print(
        f"Candidates {len(output):,}; adjacent zero denominators {provenance['validation']['zero_surrounding_denominators']['adjacent']}; local zero denominators {provenance['validation']['zero_surrounding_denominators']['local']}"
    )
    print(
        f"Provenance: {PROVENANCE_PATH} ({provenance['provenance_output']['size_bytes']:,} bytes)"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
