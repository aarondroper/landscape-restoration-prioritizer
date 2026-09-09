"""Score and audit Restoration Land Availability for the MVP.

The historical component name is retained in paths for continuity, but the
operational measure is deliberately narrower: the relative amount of mapped
eligible arable land in each eligible 500 m analysis unit.  Artificial burden
is retained as explanation context and never enters the score.
"""

from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

RAW_INDICATOR_PATH = Path("data/processed/indicators/land_restoration_feasibility.csv")
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
OUTPUT_PATH = Path("data/processed/components/land_restoration_feasibility.csv")
PROVENANCE_PATH = Path("data/processed/components/land_restoration_feasibility.provenance.json")

SCORING_INPUT = "candidate_land_area_ha"
SCORE_FIELD = "restoration_land_availability_score"
BOUNDARY_FLAG = "boundary_edge_flag"
REQUIRED_RAW_FIELDS = (
    "hex_id",
    "candidate_land_area_ha",
    "candidate_land_fraction",
    "artificial_focal_fraction",
    "artificial_adjacent_fraction",
    "artificial_local_fraction",
    "boundary_edge_flag",
)
OUTPUT_COLUMNS = (
    "hex_id",
    SCORING_INPUT,
    SCORE_FIELD,
    "artificial_focal_fraction",
    BOUNDARY_FLAG,
)
EXPECTED_CANDIDATE_COUNT = 26_395
AREA_TOLERANCE_HA = 1e-9
CSV_FLOAT_PRECISION = 10

COMPONENT_SCORES = OrderedDict(
    (
        (
            "Habitat Context",
            ("data/processed/components/habitat_context.csv", "habitat_context_score"),
        ),
        (
            "Ecological Network Context",
            ("data/processed/components/ecological_network.csv", "ecological_network_score"),
        ),
        (
            "Riparian Opportunity",
            ("data/processed/components/riparian_opportunity.csv", "riparian_opportunity_score"),
        ),
        (
            "Protected-Area Reinforcement",
            (
                "data/processed/components/protected_area_reinforcement.csv",
                "protected_area_reinforcement_score",
            ),
        ),
        ("Restoration Land Availability", (str(OUTPUT_PATH), SCORE_FIELD)),
    )
)
ECOLOGICAL_SCORE_FIELDS = tuple(
    field
    for name, (_, field) in COMPONENT_SCORES.items()
    if name != "Restoration Land Availability"
)


class RestorationLandAvailabilityError(ValueError):
    """Raised when the final land-availability scoring contract is malformed."""


def _require_columns(frame: pd.DataFrame, fields: Iterable[str], label: str) -> None:
    missing = sorted(set(fields).difference(frame.columns))
    if missing:
        raise RestorationLandAvailabilityError(f"{label} is missing required fields: {missing}")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    _require_columns(frame, ("hex_id",), label)
    if frame["hex_id"].isna().any():
        raise RestorationLandAvailabilityError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if ids.str.strip().eq("").any():
        raise RestorationLandAvailabilityError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise RestorationLandAvailabilityError(f"{label} hex_id values must be unique")
    return ids


def _numeric(values: pd.Series, field: str, label: str) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any():
        raise RestorationLandAvailabilityError(
            f"{label} {field} contains missing/non-numeric values"
        )
    array = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(array)):
        raise RestorationLandAvailabilityError(f"{label} {field} contains non-finite values")
    return array


def _validate_fraction(values: pd.Series, field: str, *, allow_missing: bool = False) -> None:
    numeric = pd.to_numeric(values, errors="coerce")
    if not allow_missing and numeric.isna().any():
        raise RestorationLandAvailabilityError(f"{field} contains missing/non-numeric values")
    valid = numeric.dropna().to_numpy(dtype=float)
    if not np.all(np.isfinite(valid)):
        raise RestorationLandAvailabilityError(f"{field} contains non-finite values")
    if np.any((valid < 0) | (valid > 1)):
        raise RestorationLandAvailabilityError(f"{field} must lie in [0, 1]")


def _normalise_boundary_flags(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise RestorationLandAvailabilityError(f"{BOUNDARY_FLAG} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise RestorationLandAvailabilityError(
                f"{BOUNDARY_FLAG} must contain only true/false values"
            )
        return values.astype(bool)
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin(["true", "false"]).all():
        raise RestorationLandAvailabilityError(
            f"{BOUNDARY_FLAG} must contain only true/false values"
        )
    return normalised.eq("true")


def validate_raw_indicators(indicators: pd.DataFrame) -> None:
    """Validate the Step 19 raw artifact, including all supporting fields."""

    if not isinstance(indicators, pd.DataFrame):
        raise RestorationLandAvailabilityError("Raw input must be a DataFrame")
    _require_columns(indicators, REQUIRED_RAW_FIELDS, "Raw land-restoration feasibility input")
    _normalise_ids(indicators, "Raw land-restoration feasibility input")
    area = _numeric(indicators[SCORING_INPUT], SCORING_INPUT, "Raw input")
    if np.any(area <= 0):
        raise RestorationLandAvailabilityError(f"{SCORING_INPUT} must be finite and > 0")
    _validate_fraction(indicators["candidate_land_fraction"], "candidate_land_fraction")
    _validate_fraction(indicators["artificial_focal_fraction"], "artificial_focal_fraction")
    _validate_fraction(
        indicators["artificial_adjacent_fraction"],
        "artificial_adjacent_fraction",
        allow_missing=True,
    )
    _validate_fraction(
        indicators["artificial_local_fraction"], "artificial_local_fraction", allow_missing=True
    )
    _normalise_boundary_flags(indicators[BOUNDARY_FLAG])
    if indicators.empty:
        raise RestorationLandAvailabilityError("The eligible candidate population is empty")


def validate_candidate_reconciliation(
    indicators: pd.DataFrame, candidate_units: pd.DataFrame
) -> dict[str, Any]:
    """Require raw indicator IDs and candidate IDs to reconcile exactly."""

    raw_ids = _normalise_ids(indicators, "Raw land-restoration feasibility input")
    candidate_ids = _normalise_ids(candidate_units, "Candidate units")
    raw_set = set(raw_ids)
    candidate_set = set(candidate_ids)
    missing = sorted(candidate_set - raw_set)
    extra = sorted(raw_set - candidate_set)
    result = {
        "raw_indicator_rows": int(len(indicators)),
        "candidate_rows": int(len(candidate_units)),
        "raw_duplicate_ids": int(indicators["hex_id"].duplicated().sum()),
        "candidate_duplicate_ids": int(candidate_units["hex_id"].duplicated().sum()),
        "missing_ids": missing,
        "extra_ids": extra,
        "missing_id_count": len(missing),
        "extra_id_count": len(extra),
        "ids_reconcile_exactly": not missing
        and not extra
        and len(indicators) == len(candidate_units),
    }
    if not result["ids_reconcile_exactly"]:
        raise RestorationLandAvailabilityError(
            "Raw land-restoration feasibility and candidate IDs do not reconcile exactly: "
            f"missing={len(missing)}, extra={len(extra)}, "
            f"raw_rows={len(indicators)}, candidate_rows={len(candidate_units)}"
        )
    return result


def validate_candidate_area_reconciliation(
    indicators: pd.DataFrame, candidate_units: pd.DataFrame, tolerance_ha: float = AREA_TOLERANCE_HA
) -> dict[str, Any]:
    """Verify raw hectares equal the candidate source's square metres / 10,000."""

    _require_columns(candidate_units, ("hex_id", "candidate_area_m2"), "Candidate units")
    source_area = _numeric(
        candidate_units["candidate_area_m2"], "candidate_area_m2", "Candidate units"
    )
    if np.any(source_area <= 0):
        raise RestorationLandAvailabilityError("Candidate candidate_area_m2 must be finite and > 0")
    indexed = candidate_units[["hex_id", "candidate_area_m2"]].copy()
    indexed["hex_id"] = indexed["hex_id"].astype(str)
    indexed["source_candidate_land_area_ha"] = source_area / 10_000.0
    merged = indicators[["hex_id", SCORING_INPUT]].copy()
    merged["hex_id"] = merged["hex_id"].astype(str)
    merged = merged.merge(
        indexed[["hex_id", "source_candidate_land_area_ha"]],
        on="hex_id",
        how="inner",
        validate="one_to_one",
    )
    difference = (merged[SCORING_INPUT] - merged["source_candidate_land_area_ha"]).abs()
    max_difference = float(difference.max()) if not difference.empty else None
    result = {
        "formula": "candidate_land_area_ha = candidate_area_m2 / 10,000",
        "tolerance_ha": tolerance_ha,
        "maximum_absolute_difference_ha": max_difference,
        "consistent_with_candidate_source": bool(
            max_difference is not None and max_difference <= tolerance_ha
        ),
    }
    if not result["consistent_with_candidate_source"]:
        raise RestorationLandAvailabilityError(
            "candidate_land_area_ha does not reconcile with candidate_area_m2 / 10,000 "
            f"within {tolerance_ha} ha (maximum difference={max_difference})"
        )
    return result


def percentile_scores(values: pd.Series | np.ndarray | list[float]) -> np.ndarray:
    """Return 0–100 empirical percentile scores using ascending average ranks."""

    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.empty:
        raise RestorationLandAvailabilityError("At least two eligible candidates are required")
    if numeric.isna().any():
        raise RestorationLandAvailabilityError("Scoring input contains missing/non-numeric values")
    array = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(array)) or np.any(array <= 0):
        raise RestorationLandAvailabilityError("Scoring input must be finite and > 0")
    n = len(array)
    if n < 2:
        raise RestorationLandAvailabilityError("At least two eligible candidates are required")
    ranks = pd.Series(array).rank(method="average", ascending=True).to_numpy(dtype=float)
    scores = 100.0 * (ranks - 1.0) / (n - 1.0)
    if not np.all(np.isfinite(scores)) or np.any((scores < 0) | (scores > 100)):
        raise RestorationLandAvailabilityError(
            "Calculated availability scores must lie in [0, 100]"
        )
    return scores


def calculate_scores(indicators: pd.DataFrame) -> pd.DataFrame:
    """Calculate the final component score with a narrow deterministic schema."""

    validate_raw_indicators(indicators)
    scores = percentile_scores(indicators[SCORING_INPUT])
    result = pd.DataFrame(
        {
            "hex_id": indicators["hex_id"].astype(str),
            SCORING_INPUT: pd.to_numeric(indicators[SCORING_INPUT], errors="raise"),
            SCORE_FIELD: scores,
            "artificial_focal_fraction": pd.to_numeric(
                indicators["artificial_focal_fraction"], errors="raise"
            ),
            BOUNDARY_FLAG: _normalise_boundary_flags(indicators[BOUNDARY_FLAG]).to_numpy(),
        }
    )
    return result.sort_values("hex_id", kind="mergesort").reset_index(drop=True)[
        list(OUTPUT_COLUMNS)
    ]


def _distribution(values: pd.Series | np.ndarray, *, include_n: bool = True) -> dict[str, Any]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if numeric.empty:
        result = {
            key: None
            for key in ("min", "p10", "p25", "median", "mean", "p75", "p90", "p95", "p99", "max")
        }
        if include_n:
            result = {"n": 0, **result}
        return result
    quantiles = numeric.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    result = {
        "min": float(numeric.min()),
        "p10": float(quantiles.loc[0.10]),
        "p25": float(quantiles.loc[0.25]),
        "median": float(quantiles.loc[0.50]),
        "mean": float(numeric.mean()),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "p99": float(quantiles.loc[0.99]),
        "max": float(numeric.max()),
    }
    return {"n": int(len(numeric)), **result} if include_n else result


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, Any]:
    pair = pd.concat([first, second], axis=1).apply(pd.to_numeric, errors="coerce").dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    return {
        "n": int(len(pair)),
        "pearson": float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="pearson")),
        "spearman": float(
            pair.iloc[:, 0]
            .rank(method="average")
            .corr(pair.iloc[:, 1].rank(method="average"), method="pearson")
        ),
    }


def _score_bins(values: pd.Series) -> dict[str, int]:
    score = pd.to_numeric(values, errors="coerce")
    return {
        "0": int((score == 0).sum()),
        ">0–10": int(((score > 0) & (score <= 10)).sum()),
        ">10–25": int(((score > 10) & (score <= 25)).sum()),
        ">25–50": int(((score > 25) & (score <= 50)).sum()),
        ">50–75": int(((score > 50) & (score <= 75)).sum()),
        ">75–90": int(((score > 75) & (score <= 90)).sum()),
        ">90": int((score > 90).sum()),
    }


def _area_bins(values: pd.Series) -> dict[str, int]:
    area = pd.to_numeric(values, errors="coerce")
    return {
        "5–7.5 ha": int(((area >= 5) & (area <= 7.5)).sum()),
        ">7.5–10 ha": int(((area > 7.5) & (area <= 10)).sum()),
        ">10–12.5 ha": int(((area > 10) & (area <= 12.5)).sum()),
        ">12.5–15 ha": int(((area > 12.5) & (area <= 15)).sum()),
        ">15–17.5 ha": int(((area > 15) & (area <= 17.5)).sum()),
        ">17.5–20 ha": int(((area > 17.5) & (area <= 20)).sum()),
        ">20 ha": int((area > 20).sum()),
    }


def _rank_difference_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    area_rank = frame[SCORING_INPUT].rank(method="average", ascending=True, pct=True)
    fraction_rank = frame["candidate_land_fraction"].rank(
        method="average", ascending=True, pct=True
    )
    absolute = (fraction_rank - area_rank).abs()
    return {
        "area_rank_definition": "average ascending rank divided by N",
        "fraction_rank_definition": "average ascending rank divided by N",
        "median_absolute_percentile_difference": float(absolute.median()),
        "p90_absolute_percentile_difference": float(absolute.quantile(0.90)),
        "candidates_differing_by_at_least_10_percentage_points": int((absolute >= 0.10).sum()),
        "candidates_differing_by_at_least_25_percentage_points": int((absolute >= 0.25).sum()),
        "signed_percentile_difference_distribution": _distribution(fraction_rank - area_rank),
        "absolute_percentile_difference_distribution": _distribution(absolute),
    }


def _tie_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    counts = frame[SCORING_INPUT].value_counts()
    tied = counts[counts > 1]
    largest = int(tied.max()) if not tied.empty else 1
    return {
        "unique_candidate_area_values": int(frame[SCORING_INPUT].nunique()),
        "tie_group_count": int(len(tied)),
        "candidates_participating_in_ties": int(tied.sum()),
        "candidates_participating_in_ties_percent": float(100 * tied.sum() / len(frame)),
        "largest_tie_group": largest,
        "largest_tie_group_percentile_span": float(100 * max(largest - 1, 0) / (len(frame) - 1)),
        "ties_materially_affect_percentile_interpretation": bool(
            100 * max(largest - 1, 0) / (len(frame) - 1) > 1
        ),
        "tie_handling": "average ascending rank; no jitter",
    }


def _score_anchors(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for threshold in (25, 50, 75, 90, 95):
        selected = frame.loc[frame[SCORE_FIELD] >= threshold, SCORING_INPUT]
        result[str(threshold)] = {
            "score_threshold": threshold,
            "minimum_candidate_land_area_ha": float(selected.min()) if not selected.empty else None,
            "candidate_count": int(len(selected)),
        }
    return result


def _join_component(
    base: pd.DataFrame, path: Path, score_field: str, name: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise RestorationLandAvailabilityError(f"Missing finalized component artifact: {path}")
    component = pd.read_csv(path)
    _require_columns(component, ("hex_id", score_field), name)
    _normalise_ids(component, name)
    score = _numeric(component[score_field], score_field, name)
    if np.any((score < 0) | (score > 100)):
        raise RestorationLandAvailabilityError(f"{name} scores must lie in [0, 100]")
    base_ids = set(base["hex_id"])
    component_ids = set(component["hex_id"].astype(str))
    missing = sorted(base_ids - component_ids)
    extra = sorted(component_ids - base_ids)
    if missing or extra or len(component) != len(base):
        raise RestorationLandAvailabilityError(
            f"{name} IDs do not reconcile with availability candidates: "
            f"missing={len(missing)}, extra={len(extra)}"
        )
    selected = component[["hex_id", score_field]].copy()
    selected["hex_id"] = selected["hex_id"].astype(str)
    joined = base.merge(selected, on="hex_id", how="left", validate="one_to_one")
    return joined, {
        "path": str(path),
        "field": score_field,
        "rows": int(len(component)),
        "missing_ids": missing,
        "extra_ids": extra,
        "ids_reconcile_exactly": True,
    }


def _matrix(
    frame: pd.DataFrame, fields: list[str], labels: list[str], method: str
) -> dict[str, dict[str, float | None]]:
    result: dict[str, dict[str, float | None]] = {}
    ranked = frame[fields].rank(method="average") if method == "spearman" else frame[fields]
    correlations = ranked.corr(method="pearson")
    for row_label, row_field in zip(labels, fields, strict=True):
        result[row_label] = {}
        for col_label, col_field in zip(labels, fields, strict=True):
            value = correlations.loc[row_field, col_field]
            result[row_label][col_label] = float(value) if pd.notna(value) else None
    return result


def _median(frame: pd.DataFrame, field: str) -> float | None:
    values = pd.to_numeric(frame[field], errors="coerce").dropna()
    return float(values.median()) if not values.empty else None


def _band_masks(scores: pd.Series) -> dict[str, pd.Series]:
    return {
        "0–25": scores <= 25,
        ">25–50": (scores > 25) & (scores <= 50),
        ">50–75": (scores > 50) & (scores <= 75),
        ">75–100": scores > 75,
    }


def _artificial_score_band_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    result = {}
    for label, mask in _band_masks(frame[SCORE_FIELD]).items():
        subset = frame.loc[mask, "artificial_focal_fraction"]
        result[label] = {
            "count": int(mask.sum()),
            "artificial_focal_fraction_distribution": _distribution(subset),
        }
    return result


def _high_availability_artificial(frame: pd.DataFrame) -> dict[str, Any]:
    result = {}
    for threshold in (75, 90):
        mask = frame[SCORE_FIELD] >= threshold
        subset = frame.loc[mask]
        result[str(threshold)] = {
            "availability_score_threshold": threshold,
            "candidate_count": int(mask.sum()),
            "focal_artificial_at_least_5_percent": int(
                (subset["artificial_focal_fraction"] >= 0.05).sum()
            ),
            "focal_artificial_at_least_10_percent": int(
                (subset["artificial_focal_fraction"] >= 0.10).sum()
            ),
            "focal_artificial_at_least_20_percent": int(
                (subset["artificial_focal_fraction"] >= 0.20).sum()
            ),
            "median_focal_artificial_fraction": _median(subset, "artificial_focal_fraction"),
        }
    return result


def _tradeoff_counts(frame: pd.DataFrame) -> dict[str, int]:
    availability_high = frame[SCORE_FIELD] >= 75
    availability_low = frame[SCORE_FIELD] <= 25
    return {
        "high_availability_and_high_habitat": int(
            (availability_high & (frame["habitat_context_score"] >= 75)).sum()
        ),
        "high_availability_and_low_habitat": int(
            (availability_high & (frame["habitat_context_score"] <= 25)).sum()
        ),
        "low_availability_and_high_habitat": int(
            (availability_low & (frame["habitat_context_score"] >= 75)).sum()
        ),
        "high_availability_and_high_riparian": int(
            (availability_high & (frame["riparian_opportunity_score"] >= 75)).sum()
        ),
        "high_availability_and_high_protection": int(
            (availability_high & (frame["protected_area_reinforcement_score"] >= 75)).sum()
        ),
        "high_availability_and_high_network": int(
            (availability_high & (frame["ecological_network_score"] >= 75)).sum()
        ),
        "high_availability_and_all_four_ecological_at_least_50": int(
            (availability_high & (frame[list(ECOLOGICAL_SCORE_FIELDS)] >= 50).all(axis=1)).sum()
        ),
        "low_availability_and_all_four_ecological_at_least_50": int(
            (availability_low & (frame[list(ECOLOGICAL_SCORE_FIELDS)] >= 50).all(axis=1)).sum()
        ),
    }


def _boundary_coastal_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    fields = [SCORING_INPUT, SCORE_FIELD]
    edge = frame[BOUNDARY_FLAG].astype(bool)
    sea = frame["sea_pixels"] > 0
    result = {}
    for label, mask in (
        ("boundary_edge", edge),
        ("non_edge", ~edge),
        ("sea_containing", sea),
        ("non_sea", ~sea),
    ):
        subset = frame.loc[mask]
        result[label] = {
            "candidate_count": int(mask.sum()),
            "raw_area_distribution": _distribution(subset[fields[0]]),
            "score_distribution": _distribution(subset[fields[1]]),
        }
    result["edge_candidate_count"] = int(edge.sum())
    result["non_edge_candidate_count"] = int((~edge).sum())
    result["sea_containing_candidate_count"] = int(sea.sum())
    result["non_sea_candidate_count"] = int((~sea).sum())
    return result


def _top_bottom_examples(frame: pd.DataFrame, ascending: bool) -> list[dict[str, Any]]:
    fields = [
        "hex_id",
        SCORING_INPUT,
        SCORE_FIELD,
        "candidate_land_fraction",
        "artificial_focal_fraction",
        "habitat_context_score",
        "ecological_network_score",
        "riparian_opportunity_score",
        "protected_area_reinforcement_score",
        BOUNDARY_FLAG,
    ]
    return (
        frame.sort_values([SCORE_FIELD, "hex_id"], ascending=[ascending, True], kind="mergesort")[
            fields
        ]
        .head(10)
        .to_dict(orient="records")
    )


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format=f"%.{CSV_FLOAT_PRECISION}f")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_safe_json(value), indent=2, ensure_ascii=False) + "\n")


def _final_status_summary() -> list[dict[str, str]]:
    return [
        {
            "component": "Habitat Context",
            "raw_input": "habitat_context_local_fraction",
            "transformation": "empirical percentile",
            "meaning": "surrounding habitat amount",
        },
        {
            "component": "Ecological Network Context",
            "raw_input": "opposing_balance_ratio",
            "transformation": "direct ×100",
            "meaning": "opposing-side habitat configuration",
        },
        {
            "component": "Riparian Opportunity",
            "raw_input": "riparian_focal_fraction",
            "transformation": "zero-anchored positive percentile",
            "meaning": "focal wetland/inland-water context",
        },
        {
            "component": "Protected-Area Reinforcement",
            "raw_input": "nearest_protected_hex_steps",
            "transformation": "overlap=100 + reverse empirical rank for non-overlap",
            "meaning": "proximity to terrestrial formal protection network",
        },
        {
            "component": "Restoration Land Availability",
            "raw_input": "candidate_land_area_ha",
            "transformation": "empirical percentile",
            "meaning": "amount of mapped eligible arable candidate land",
        },
    ]


def build_restoration_land_availability_score(
    raw_indicator_path: Path = RAW_INDICATOR_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the fifth component and its complete audit provenance."""

    started = time.perf_counter()
    if not raw_indicator_path.exists():
        raise RestorationLandAvailabilityError(
            f"Missing raw indicator artifact: {raw_indicator_path}"
        )
    if not candidate_units_path.exists():
        raise RestorationLandAvailabilityError(f"Missing candidate source: {candidate_units_path}")

    raw = pd.read_csv(raw_indicator_path)
    validate_raw_indicators(raw)
    candidate_units = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
    validate_reconciliation = validate_candidate_reconciliation(raw, candidate_units)
    area_reconciliation = validate_candidate_area_reconciliation(raw, candidate_units)
    if len(raw) != EXPECTED_CANDIDATE_COUNT:
        raise RestorationLandAvailabilityError(
            f"Expected {EXPECTED_CANDIDATE_COUNT} eligible candidates, found {len(raw)}"
        )
    _require_columns(candidate_units, ("sea_pixels",), "Candidate units")
    sea_pixels = _numeric(candidate_units["sea_pixels"], "sea_pixels", "Candidate units")
    if np.any(sea_pixels < 0):
        raise RestorationLandAvailabilityError("Candidate sea_pixels must be non-negative")

    scored = calculate_scores(raw)
    raw_support = raw[
        [
            "hex_id",
            "candidate_land_fraction",
            "artificial_adjacent_fraction",
            "artificial_local_fraction",
        ]
    ].copy()
    raw_support["hex_id"] = raw_support["hex_id"].astype(str)
    candidate_meta = candidate_units[["hex_id", "candidate_area_m2", "sea_pixels"]].copy()
    candidate_meta["hex_id"] = candidate_meta["hex_id"].astype(str)
    scored["hex_id"] = scored["hex_id"].astype(str)
    joined = scored.merge(raw_support, on="hex_id", how="left", validate="one_to_one")
    joined = joined.merge(candidate_meta, on="hex_id", how="left", validate="one_to_one")

    component_reconciliation: dict[str, Any] = {}
    for name, (path_string, field) in COMPONENT_SCORES.items():
        if name == "Restoration Land Availability":
            continue
        joined, component_reconciliation[name] = _join_component(
            joined, Path(path_string), field, name
        )
        component_reconciliation[name]["provenance_path"] = str(
            Path(path_string).with_suffix(".provenance.json")
        )

    for field in [*ECOLOGICAL_SCORE_FIELDS, SCORE_FIELD]:
        values = _numeric(joined[field], field, "Joined components")
        if np.any((values < 0) | (values > 100)):
            raise RestorationLandAvailabilityError(f"{field} must lie in [0, 100]")

    output = scored.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    _write_csv(output[list(OUTPUT_COLUMNS)], output_path)

    frame = joined.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    score = frame[SCORE_FIELD]
    area = frame[SCORING_INPUT]
    fraction_correlation = _correlation(score, frame["candidate_land_fraction"])
    provenance: dict[str, Any] = {
        "component_name": "Restoration Land Availability",
        "historical_component_name": "Land-Restoration Feasibility",
        "status": "IMPLEMENTED FOR MVP",
        "interpretation": "relative amount of mapped eligible arable land available within each 500 m analysis unit",
        "not_claimed": [
            "full implementation feasibility",
            "landowner willingness",
            "acquisition feasibility",
            "implementation cost",
            "agricultural productivity",
            "cadastral fragmentation",
            "soil suitability",
            "drainage-removal feasibility",
            "legal permission",
            "subsidy availability",
            "socioeconomic feasibility",
        ],
        "source": {
            "raw_indicator_path": str(raw_indicator_path),
            "raw_indicator_provenance_path": str(
                raw_indicator_path.with_suffix(".provenance.json")
            ),
            "candidate_source_path": str(candidate_units_path),
            "candidate_source_layer": CANDIDATE_UNITS_LAYER,
            "candidate_area_source_field": "candidate_area_m2",
            "no_new_environmental_data_ingested": True,
        },
        "selected_raw_input": {
            "field": SCORING_INPUT,
            "sole_scoring_input": True,
            "definition": "hectares of NMD class 3 arable land already retained by the approved candidate eligibility rule",
            "conversion": "candidate_area_m2 / 10,000",
            "direction": "higher is better",
        },
        "rejected_scoring_inputs": {
            "candidate_land_fraction": "Rejected because it is nearly redundant with candidate land area and has minor denominator sensitivity in coastal or partially terrestrial cells.",
            "artificial_focal_fraction": "Supporting diagnostic only; candidate hectares already exclude mutually exclusive artificial classes, so penalizing again would partly double-count land-availability limitation.",
            "artificial_adjacent_fraction": "Supporting diagnostic only; surrounding burden is less directly connected to whether mapped arable hectares themselves are available.",
            "artificial_local_fraction": "Supporting diagnostic only; surrounding burden is less directly connected to whether mapped arable hectares themselves are available.",
        },
        "scoring": {
            "formula": "restoration_land_availability_score = 100 * (average ascending rank of x_i - 1) / (N - 1)",
            "input": "x_i = candidate_land_area_ha",
            "population": "all eligible candidates",
            "candidate_count": int(len(raw)),
            "tie_handling": "average ascending ranks for equal candidate areas",
            "lowest_observation_score": 0,
            "highest_observation_score": 100,
            "interpretation": "A score of 90 means the unit contains more mapped eligible arable land than roughly 90% of the eligible candidate population.",
            "does_not_mean": [
                "90% of the cell is restorable",
                "90% implementation feasibility",
                "90% chance of restoration",
                "90 hectares",
                "90% landowner willingness",
            ],
            "why_empirical_percentile": [
                "candidate eligibility already imposes an absolute minimum of at least 5 ha",
                "all observations are plausible screening candidates within the approved population",
                "relative ranking distinguishes available land without claiming linear feasibility between hectares",
                "the ranking creates the common 0–100 decision-support scale",
                "hectares remain available for direct interpretation alongside the score",
            ],
            "not_used": [
                "direct hectares times a constant",
                "min-max against observed maximum",
                "candidate fraction times 100",
                "theoretical hex-area scaling",
                "artificial penalties",
                "hard area categories",
            ],
        },
        "candidate_population_reconciliation": {
            **validate_reconciliation,
            "output_rows": int(len(output)),
            "output_duplicate_ids": int(output["hex_id"].duplicated().sum()),
            "output_missing_ids": sorted(set(raw["hex_id"]) - set(output["hex_id"])),
            "output_extra_ids": sorted(set(output["hex_id"]) - set(raw["hex_id"])),
        },
        "candidate_area_reconciliation": area_reconciliation,
        "raw_area_distribution": {"distribution": _distribution(area), "bins": _area_bins(area)},
        "score_distribution": {"distribution": _distribution(score), "bins": _score_bins(score)},
        "transformation_validation": {
            "lowest_raw_area_maps_to_zero": bool(
                frame.loc[frame[SCORING_INPUT].idxmin(), SCORE_FIELD] == 0
            ),
            "highest_raw_area_unique_maps_to_100": bool(
                frame[SCORING_INPUT].eq(frame[SCORING_INPUT].max()).sum() == 1
                and frame.loc[frame[SCORING_INPUT].idxmax(), SCORE_FIELD] == 100
            ),
            "highest_raw_area_maps_to_100": bool(
                frame.loc[frame[SCORING_INPUT].idxmax(), SCORE_FIELD] == 100
            ),
            "highest_raw_area_score": float(frame.loc[frame[SCORING_INPUT].idxmax(), SCORE_FIELD]),
            "monotonicity": bool(
                frame[[SCORING_INPUT, SCORE_FIELD]]
                .sort_values(SCORING_INPUT)[SCORE_FIELD]
                .is_monotonic_increasing
            ),
            "equal_raw_areas_have_equal_scores": bool(
                frame.groupby(SCORING_INPUT, sort=False)[SCORE_FIELD].nunique().max() == 1
            ),
            "scores_in_0_100": bool(np.all((score >= 0) & (score <= 100) & np.isfinite(score))),
            "row_order_independent": bool(
                np.allclose(
                    calculate_scores(raw.sample(frac=1, random_state=20))
                    .set_index("hex_id")
                    .loc[output["hex_id"], SCORE_FIELD]
                    .to_numpy(),
                    output[SCORE_FIELD].to_numpy(),
                )
            ),
        },
        "tie_diagnostics": _tie_diagnostics(frame),
        "hectare_to_score_anchors": _score_anchors(frame),
        "candidate_fraction_redundancy_evidence": {
            "raw_area_vs_candidate_fraction": _correlation(area, frame["candidate_land_fraction"]),
            "final_score_vs_candidate_fraction": fraction_correlation,
            "temporary_percentile_rank_differences": _rank_difference_diagnostics(frame),
            "decision": "candidate fraction is rejected from scoring because it is nearly redundant with candidate area; area is preferred for direct absolute-land interpretation and reduced denominator sensitivity.",
        },
        "artificial_burden_decision": {
            "scored": False,
            "reason": "candidate_land_area_ha already counts only NMD arable pixels and artificial classes 51–53 are mutually exclusive with candidate/arable land; no defensible penalty form or weight was established.",
            "artificial_focal_fraction": "diagnostic only",
            "artificial_adjacent_fraction": "diagnostic only",
            "artificial_local_fraction": "diagnostic only",
        },
        "artificial_burden_relationship": {
            field: {
                "score_correlation": _correlation(score, frame[field]),
                "distribution": _distribution(frame[field]),
            }
            for field in (
                "artificial_focal_fraction",
                "artificial_adjacent_fraction",
                "artificial_local_fraction",
            )
        },
        "artificial_focal_by_availability_score_band": _artificial_score_band_diagnostics(frame),
        "high_availability_high_artificial": _high_availability_artificial(frame),
        "five_component_pearson_matrix": _matrix(
            frame,
            [field for _, field in COMPONENT_SCORES.values()],
            list(COMPONENT_SCORES),
            "pearson",
        ),
        "five_component_spearman_matrix": _matrix(
            frame,
            [field for _, field in COMPONENT_SCORES.values()],
            list(COMPONENT_SCORES),
            "spearman",
        ),
        "component_tradeoff_counts": _tradeoff_counts(frame),
        "ecological_profile_of_availability_tails": {
            label: {
                "candidate_count": int(mask.sum()),
                "median_habitat_score": _median(frame.loc[mask], "habitat_context_score"),
                "median_network_score": _median(frame.loc[mask], "ecological_network_score"),
                "median_riparian_score": _median(frame.loc[mask], "riparian_opportunity_score"),
                "median_protection_score": _median(
                    frame.loc[mask], "protected_area_reinforcement_score"
                ),
            }
            for label, mask in {
                "availability_<=25": frame[SCORE_FIELD] <= 25,
                "availability_>=75": frame[SCORE_FIELD] >= 75,
                "availability_>=90": frame[SCORE_FIELD] >= 90,
            }.items()
        },
        "boundary_coastal_diagnostics": _boundary_coastal_diagnostics(frame),
        "artificial_diagnostic_value": {
            "availability_>=75_and_focal_artificial_>=10_percent": int(
                ((frame[SCORE_FIELD] >= 75) & (frame["artificial_focal_fraction"] >= 0.10)).sum()
            ),
            "availability_<=25_and_focal_artificial_==0": int(
                ((frame[SCORE_FIELD] <= 25) & (frame["artificial_focal_fraction"] == 0)).sum()
            ),
            "interpretation": "Availability and focal artificial burden describe different aspects of the screening context; the diagnostic is explanatory and does not alter the score.",
        },
        "top_10_availability_examples": _top_bottom_examples(frame, ascending=False),
        "bottom_10_availability_examples": _top_bottom_examples(frame, ascending=True),
        "component_reconciliation": component_reconciliation,
        "final_five_component_status": _final_status_summary(),
        "caveats": [
            "The score is population-relative within the already eligible candidate population.",
            "Candidate eligibility guarantees at least 5 ha and at least 25% terrestrial candidate fraction; this component does not alter eligibility.",
            "A 500 m hex is an analytical screening unit, not a property parcel.",
            "No socioeconomic, legal, ownership, cadastral, cost, soil, drainage, or subsidy data are present.",
            "Mapped candidate land does not establish restoration suitability or implementation feasibility.",
            "Expected score tradeoffs with Habitat Context, Riparian Opportunity, and Protected-Area Reinforcement are descriptive relationships, not model defects by themselves.",
        ],
        "output": {
            "path": str(output_path),
            "columns": list(OUTPUT_COLUMNS),
            "rows": int(len(output)),
            "deterministic_order": "ascending hex_id",
            "geometry_included": False,
            "overall_score_included": False,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environmental_datasets_downloaded": False,
        "dependency_changes": [],
        "warnings": [],
        "runtime_seconds": time.perf_counter() - started,
        "provenance_output": str(provenance_path),
    }
    _write_json(provenance_path, provenance)
    provenance["output"]["bytes"] = output_path.stat().st_size
    _write_json(provenance_path, provenance)
    return output, provenance


def main() -> None:
    output, provenance = build_restoration_land_availability_score()
    print(
        f"Wrote {len(output):,} Restoration Land Availability scores to {OUTPUT_PATH} "
        f"and provenance to {PROVENANCE_PATH} ({os.path.getsize(PROVENANCE_PATH):,} bytes)."
    )
    print(
        f"Score range: {output[SCORE_FIELD].min():.6f}–{output[SCORE_FIELD].max():.6f}; "
        f"candidate count: {provenance['scoring']['candidate_count']:,}."
    )


if __name__ == "__main__":
    main()
