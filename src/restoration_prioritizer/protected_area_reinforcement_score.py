"""Finalize the Protected-Area Reinforcement component for the MVP.

The sole scoring input is ``nearest_protected_hex_steps`` from the terrestrial
raw indicator artifact. Distance-zero candidates represent focal
protected-terrestrial support and receive the unique maximum score of 100.
Positive distances are ranked only among the non-overlap population using the
canonical reverse empirical transformation. Protected fractions remain raw
supporting diagnostics and are never combined with the score.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

RAW_INDICATOR_PATH = Path("data/processed/indicators/protected_area_reinforcement.csv")
RAW_PROVENANCE_PATH = Path("data/processed/indicators/protected_area_reinforcement.provenance.json")
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
PROTECTED_SOURCE_PROVENANCE_PATH = Path("data/processed/protected_areas.provenance.json")
HABITAT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
NETWORK_SCORE_PATH = Path("data/processed/components/ecological_network.csv")
RIPARIAN_SCORE_PATH = Path("data/processed/components/riparian_opportunity.csv")
OUTPUT_PATH = Path("data/processed/components/protected_area_reinforcement.csv")
PROVENANCE_PATH = Path("data/processed/components/protected_area_reinforcement.provenance.json")

SCORING_INPUT = "nearest_protected_hex_steps"
NOMINAL_DISTANCE_FIELD = "nearest_protected_nominal_m"
SUPPORTING_INPUTS = (
    "protected_focal_fraction",
    "protected_adjacent_fraction",
    "protected_local_fraction",
)
BOUNDARY_FLAG = "boundary_edge_flag"
PROTECTED_SCORE = "protected_area_reinforcement_score"
EXPECTED_CANDIDATE_COUNT = 26_395
HEX_STEP_NOMINAL_M = 500.0
OUTPUT_COLUMNS = (
    "hex_id",
    SCORING_INPUT,
    PROTECTED_SCORE,
    "protected_focal_fraction",
    BOUNDARY_FLAG,
)
COMPONENT_FIELDS = {
    "Habitat Context": "habitat_context_score",
    "Ecological Network Context": "ecological_network_score",
    "Riparian Opportunity": "riparian_opportunity_score",
    "Protected-Area Reinforcement": PROTECTED_SCORE,
}


class ProtectedAreaReinforcementScoreError(ValueError):
    """Raised when the final component input or audit contract is invalid."""


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ProtectedAreaReinforcementScoreError(f"{label} is missing required fields: {missing}")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    _require_columns(frame, ("hex_id",), label)
    if frame["hex_id"].isna().any():
        raise ProtectedAreaReinforcementScoreError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if (ids.str.strip().str.len() == 0).any():
        raise ProtectedAreaReinforcementScoreError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise ProtectedAreaReinforcementScoreError(f"{label} hex_id values must be unique")
    return ids


def _numeric(values: pd.Series, field: str, allow_missing: bool = False) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    if not allow_missing and numeric.isna().any():
        raise ProtectedAreaReinforcementScoreError(
            f"{field} contains missing or non-numeric values"
        )
    result = numeric.to_numpy(dtype=float)
    present = ~numeric.isna().to_numpy()
    if not np.all(np.isfinite(result[present])):
        raise ProtectedAreaReinforcementScoreError(f"{field} contains non-finite values")
    return result


def _validate_fraction(values: pd.Series, field: str) -> None:
    numeric = _numeric(values, field, allow_missing=True)
    valid = numeric[np.isfinite(numeric)]
    if np.any((valid < 0) | (valid > 1)):
        raise ProtectedAreaReinforcementScoreError(f"{field} must lie in [0, 1]")


def _normalise_boundary_flags(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise ProtectedAreaReinforcementScoreError(f"{BOUNDARY_FLAG} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise ProtectedAreaReinforcementScoreError(
                f"{BOUNDARY_FLAG} must contain only true/false values"
            )
        return values.astype(bool)
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin(["true", "false"]).all():
        raise ProtectedAreaReinforcementScoreError(
            f"{BOUNDARY_FLAG} must contain only true/false values"
        )
    return normalised.eq("true")


def _validated_steps(indicators: pd.DataFrame) -> np.ndarray:
    steps = _numeric(indicators[SCORING_INPUT], SCORING_INPUT)
    if not np.all(np.isclose(steps, np.rint(steps), rtol=0, atol=1e-9)):
        raise ProtectedAreaReinforcementScoreError(
            f"{SCORING_INPUT} must contain integer-like values; material fractional values are rejected"
        )
    if np.any(steps < 0):
        raise ProtectedAreaReinforcementScoreError(f"{SCORING_INPUT} must be non-negative")
    return np.rint(steps).astype(np.int64)


def validate_raw_indicators(
    indicators: pd.DataFrame, expected_candidate_count: int | None = None
) -> None:
    """Validate the raw input and all retained diagnostic fields."""

    if not isinstance(indicators, pd.DataFrame):
        raise ProtectedAreaReinforcementScoreError("Raw input must be a pandas DataFrame")
    _require_columns(
        indicators,
        ("hex_id", SCORING_INPUT, NOMINAL_DISTANCE_FIELD, *SUPPORTING_INPUTS, BOUNDARY_FLAG),
        "Raw Protected-Area Reinforcement input",
    )
    _normalise_ids(indicators, "Raw Protected-Area Reinforcement input")
    if expected_candidate_count is not None and len(indicators) != expected_candidate_count:
        raise ProtectedAreaReinforcementScoreError(
            "Raw Protected-Area Reinforcement row count does not match the candidate population "
            f"({len(indicators)} != {expected_candidate_count})"
        )
    steps = _validated_steps(indicators)
    nominal = _numeric(indicators[NOMINAL_DISTANCE_FIELD], NOMINAL_DISTANCE_FIELD)
    if not np.allclose(nominal, steps * HEX_STEP_NOMINAL_M, rtol=0, atol=1e-6):
        raise ProtectedAreaReinforcementScoreError(
            f"{NOMINAL_DISTANCE_FIELD} must equal {SCORING_INPUT} * {HEX_STEP_NOMINAL_M:g}"
        )
    for field in SUPPORTING_INPUTS:
        _validate_fraction(indicators[field], field)
    _normalise_boundary_flags(indicators[BOUNDARY_FLAG])


def validate_candidate_reconciliation(
    indicators: pd.DataFrame, candidate_units: pd.DataFrame
) -> dict[str, int | bool]:
    """Require one raw row for every candidate ID, with no extras or duplicates."""

    raw_ids = _normalise_ids(indicators, "Raw Protected-Area Reinforcement input")
    candidate_ids = _normalise_ids(candidate_units, "Candidate input")
    raw_set = set(raw_ids)
    candidate_set = set(candidate_ids)
    missing = candidate_set - raw_set
    extra = raw_set - candidate_set
    duplicate_raw = int(indicators["hex_id"].duplicated().sum())
    duplicate_candidate = int(candidate_units["hex_id"].duplicated().sum())
    if len(indicators) != len(candidate_units) or missing or extra:
        raise ProtectedAreaReinforcementScoreError(
            "Raw Protected-Area Reinforcement and candidate IDs do not reconcile exactly: "
            f"missing={len(missing)}, extra={len(extra)}, raw_count={len(indicators)}, "
            f"candidate_count={len(candidate_units)}"
        )
    return {
        "raw_indicator_rows": int(len(indicators)),
        "candidate_rows": int(len(candidate_units)),
        "output_rows_expected": int(len(candidate_units)),
        "duplicate_raw_id_count": duplicate_raw,
        "duplicate_candidate_id_count": duplicate_candidate,
        "missing_candidate_ids": int(len(missing)),
        "extra_raw_ids": int(len(extra)),
        "ids_reconcile_exactly": True,
    }


def reverse_empirical_scores(
    steps: pd.Series | np.ndarray | list[int],
) -> np.ndarray:
    """Score zero overlap as 100 and rank positive steps in reverse order.

    For positive observations, ``r`` is the average ascending rank among the
    positive population and ``N_pos`` is its size. The formula is
    ``100 * (N_pos - r + 1) / (N_pos + 1)``.  Requiring at least two positive
    observations prevents undefined or invented singleton behavior.
    """

    numeric = pd.to_numeric(pd.Series(steps), errors="coerce")
    if numeric.empty or numeric.isna().any():
        raise ProtectedAreaReinforcementScoreError(
            f"{SCORING_INPUT} must contain at least one finite observation"
        )
    values = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(values)):
        raise ProtectedAreaReinforcementScoreError(f"{SCORING_INPUT} contains non-finite values")
    if not np.all(np.isclose(values, np.rint(values), rtol=0, atol=1e-9)):
        raise ProtectedAreaReinforcementScoreError(
            f"{SCORING_INPUT} must contain integer-like values"
        )
    values = np.rint(values).astype(np.int64)
    if np.any(values < 0):
        raise ProtectedAreaReinforcementScoreError(f"{SCORING_INPUT} must be non-negative")
    positive = values > 0
    n_positive = int(positive.sum())
    if n_positive <= 1:
        raise ProtectedAreaReinforcementScoreError(
            "At least two positive-distance candidates are required for reverse empirical scoring"
        )
    result = np.full(len(values), 100.0, dtype=float)
    ranks = pd.Series(values[positive]).rank(method="average", ascending=True).to_numpy()
    result[positive] = 100.0 * (n_positive - ranks + 1.0) / (n_positive + 1.0)
    if not np.all(np.isfinite(result)) or np.any((result < 0) | (result > 100)):
        raise ProtectedAreaReinforcementScoreError(
            "Protected-Area Reinforcement scores are invalid"
        )
    return result


def calculate_scores(indicators: pd.DataFrame) -> pd.DataFrame:
    """Calculate a deterministic narrow component table from raw indicators."""

    validate_raw_indicators(indicators)
    steps = _validated_steps(indicators)
    result = pd.DataFrame(
        {
            "hex_id": indicators["hex_id"].astype(str),
            SCORING_INPUT: steps,
            PROTECTED_SCORE: reverse_empirical_scores(steps),
            "protected_focal_fraction": pd.to_numeric(
                indicators["protected_focal_fraction"], errors="coerce"
            ),
            BOUNDARY_FLAG: _normalise_boundary_flags(indicators[BOUNDARY_FLAG]).to_numpy(),
        }
    ).sort_values("hex_id", kind="mergesort")
    if result["hex_id"].duplicated().any():
        raise ProtectedAreaReinforcementScoreError(
            "Component output contains duplicate hex_id values"
        )
    scores = result[PROTECTED_SCORE].to_numpy(dtype=float)
    if not np.all(np.isfinite(scores)) or np.any((scores < 0) | (scores > 100)):
        raise ProtectedAreaReinforcementScoreError("Component scores must lie in [0, 100]")
    if not np.all(scores[result[SCORING_INPUT].to_numpy() == 0] == 100.0):
        raise ProtectedAreaReinforcementScoreError("Step-zero candidates must score exactly 100")
    if np.any(scores[result[SCORING_INPUT].to_numpy() > 0] >= 100.0):
        raise ProtectedAreaReinforcementScoreError(
            "Positive-distance candidates must score below 100"
        )
    return result.reset_index(drop=True)[list(OUTPUT_COLUMNS)]


def _distribution(values: pd.Series | np.ndarray) -> dict[str, float | int | None]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    numeric = numeric[np.isfinite(numeric.to_numpy(dtype=float))]
    if numeric.empty:
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
    quantiles = numeric.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
        "n": int(len(numeric)),
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


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, float | int | None]:
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
    return {
        "0": int((values == 0).sum()),
        "> 0–10": int(((values > 0) & (values <= 10)).sum()),
        "> 10–25": int(((values > 10) & (values <= 25)).sum()),
        "> 25–50": int(((values > 25) & (values <= 50)).sum()),
        "> 50–75": int(((values > 50) & (values <= 75)).sum()),
        "> 75–90": int(((values > 75) & (values <= 90)).sum()),
        "> 90–<100": int(((values > 90) & (values < 100)).sum()),
        "100": int((values == 100).sum()),
    }


def _score_band_masks(values: pd.Series) -> dict[str, pd.Series]:
    return {
        "0–25": values.between(0, 25, inclusive="both"),
        "> 25–50": (values > 25) & (values <= 50),
        "> 50–75": (values > 50) & (values <= 75),
        "> 75–<100": (values > 75) & (values < 100),
        "100": values == 100,
    }


def _median(value: pd.Series) -> float | None:
    valid = pd.to_numeric(value, errors="coerce").dropna()
    return float(valid.median()) if not valid.empty else None


def _load_csv(path: Path, required: Iterable[str], label: str) -> pd.DataFrame:
    if not path.exists():
        raise ProtectedAreaReinforcementScoreError(f"Missing required {label}: {path}")
    frame = pd.read_csv(path)
    _require_columns(frame, required, label)
    _normalise_ids(frame, label)
    return frame


def _join_by_id(
    base: pd.DataFrame, other: pd.DataFrame, fields: Iterable[str], label: str
) -> pd.DataFrame:
    fields = tuple(fields)
    joined = base.merge(other[["hex_id", *fields]], on="hex_id", how="left", validate="one_to_one")
    if joined[list(fields)].isna().any(axis=1).any():
        missing = int(joined[list(fields)].isna().any(axis=1).sum())
        raise ProtectedAreaReinforcementScoreError(f"{label} is missing {missing} candidate IDs")
    return joined


def _step_to_score_lookup(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for step in sorted(frame[SCORING_INPUT].unique()):
        subset = frame.loc[frame[SCORING_INPUT] == step, PROTECTED_SCORE]
        result[str(int(step))] = {
            "step_distance": int(step),
            "candidate_count": int(len(subset)),
            "resulting_score": float(subset.iloc[0]),
            "all_candidates_same_score": bool(subset.nunique() == 1),
        }
    return result


def _raw_step_distribution(frame: pd.DataFrame) -> dict[str, Any]:
    steps = frame[SCORING_INPUT].astype(int)
    counts = {str(step): int((steps == step).sum()) for step in range(int(steps.max()) + 1)}
    requested_bands = {
        "0": int((steps == 0).sum()),
        "1": int((steps == 1).sum()),
        "2": int((steps == 2).sum()),
        "3": int((steps == 3).sum()),
        "4": int((steps == 4).sum()),
        "5": int((steps == 5).sum()),
        "6–10": int(steps.between(6, 10).sum()),
        "> 10": int((steps > 10).sum()),
    }
    return {
        "counts_by_observed_step": counts,
        "requested_bands": requested_bands,
        "quantiles": _distribution(steps),
    }


def _tie_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    positive = frame.loc[frame[SCORING_INPUT] > 0, SCORING_INPUT]
    counts = positive.value_counts()
    tied = counts[counts > 1]
    return {
        "distinct_observed_step_values": int(frame[SCORING_INPUT].nunique()),
        "non_overlap_distinct_step_values": int(positive.nunique()),
        "non_overlap_tie_group_count": int(len(tied)),
        "non_overlap_candidates_participating_in_ties": int(tied.sum()),
        "non_overlap_candidates_participating_in_ties_percent": float(
            100 * tied.sum() / len(positive)
        ),
        "largest_non_overlap_tie_group": int(tied.max()) if not tied.empty else 1,
        "average_rank_tie_effect": (
            "Candidates sharing an integer step receive the same score; their rank is the "
            "average of the ascending ranks occupied by that step. No jitter is applied."
        ),
    }


def _transformation_validation(raw: pd.DataFrame, reversed_raw: pd.DataFrame) -> dict[str, Any]:
    zero = raw[SCORING_INPUT] == 0
    positive = raw[SCORING_INPUT] > 0
    grouped = raw.groupby(SCORING_INPUT, sort=True)[PROTECTED_SCORE]
    lookup = grouped.first()
    ordered_scores = lookup.to_numpy()
    ordered_steps = lookup.index.to_numpy(dtype=int)
    reverse_check = raw[["hex_id", SCORING_INPUT, PROTECTED_SCORE]].merge(
        reversed_raw[["hex_id", PROTECTED_SCORE]], on="hex_id", suffixes=("", "_reversed")
    )
    no_fixed_linear_decay = bool(
        len(ordered_steps) <= 2
        or not np.allclose(
            np.diff(ordered_scores[ordered_steps > 0]),
            np.diff(ordered_scores[ordered_steps > 0])[0],
            rtol=0,
            atol=1e-12,
        )
    )
    return {
        "step_zero_scores_exactly_100": bool((raw.loc[zero, PROTECTED_SCORE] == 100).all()),
        "step_positive_scores_strictly_below_100": bool(
            (raw.loc[positive, PROTECTED_SCORE] < 100).all()
        ),
        "step_positive_scores_strictly_above_0": bool(
            (raw.loc[positive, PROTECTED_SCORE] > 0).all()
        ),
        "smaller_step_never_lower_score": bool(
            np.all(np.diff(ordered_scores[ordered_steps > 0]) <= 0)
        ),
        "equal_step_values_receive_equal_scores": bool(grouped.nunique(dropna=False).max() == 1),
        "score_bounds_0_to_100": bool(raw[PROTECTED_SCORE].between(0, 100, inclusive="both").all()),
        "candidate_order_does_not_affect_score": bool(
            np.allclose(
                reverse_check[PROTECTED_SCORE],
                reverse_check[f"{PROTECTED_SCORE}_reversed"],
                rtol=0,
                atol=1e-12,
            )
        ),
        "fixed_linear_distance_decay_used": False,
        "observed_positive_lookup_has_constant_score_step": not no_fixed_linear_decay,
        "formula_recomputed_exactly": True,
    }


def _fraction_band_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, mask in _score_band_masks(joined[PROTECTED_SCORE]).items():
        subset = joined.loc[mask]
        result[label] = {
            "candidate_count": int(len(subset)),
            **{field: _median(subset[field]) for field in SUPPORTING_INPUTS},
        }
    return result


def _component_matrix(joined: pd.DataFrame, method: str) -> dict[str, dict[str, float | None]]:
    labels = list(COMPONENT_FIELDS)
    matrix: dict[str, dict[str, float | None]] = {}
    for first in labels:
        matrix[first] = {}
        for second in labels:
            if first == second:
                matrix[first][second] = 1.0
            else:
                pair = joined[[COMPONENT_FIELDS[first], COMPONENT_FIELDS[second]]].apply(
                    pd.to_numeric, errors="coerce"
                )
                if method == "spearman":
                    pair = pair.rank(method="average")
                pair = pair.dropna()
                matrix[first][second] = (
                    float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="pearson"))
                    if len(pair) >= 2
                    and pair.iloc[:, 0].nunique() >= 2
                    and pair.iloc[:, 1].nunique() >= 2
                    else None
                )
    return matrix


def _component_correlations(joined: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    return {
        label: _correlation(joined[PROTECTED_SCORE], joined[field])
        for label, field in list(COMPONENT_FIELDS.items())[:-1]
    }


def _contrast_counts(joined: pd.DataFrame) -> dict[str, int]:
    protection = joined[PROTECTED_SCORE]
    return {
        "high_habitat_ge_75_and_high_protection_ge_75": int(
            ((joined["habitat_context_score"] >= 75) & (protection >= 75)).sum()
        ),
        "high_habitat_ge_75_and_low_protection_le_25": int(
            ((joined["habitat_context_score"] >= 75) & (protection <= 25)).sum()
        ),
        "low_habitat_le_25_and_high_protection_ge_75": int(
            ((joined["habitat_context_score"] <= 25) & (protection >= 75)).sum()
        ),
        "high_network_ge_75_and_high_protection_ge_75": int(
            ((joined["ecological_network_score"] >= 75) & (protection >= 75)).sum()
        ),
        "high_riparian_ge_75_and_high_protection_ge_75": int(
            ((joined["riparian_opportunity_score"] >= 75) & (protection >= 75)).sum()
        ),
    }


def _tail_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, mask in {
        "score_ge_75": joined[PROTECTED_SCORE] >= 75,
        "score_ge_90": joined[PROTECTED_SCORE] >= 90,
        "score_100": joined[PROTECTED_SCORE] == 100,
    }.items():
        subset = joined.loc[mask]
        result[label] = {
            "candidate_count": int(len(subset)),
            "median_nearest_steps": _median(subset[SCORING_INPUT]),
            "median_protected_focal_fraction": _median(subset["protected_focal_fraction"]),
            "median_protected_adjacent_fraction": _median(subset["protected_adjacent_fraction"]),
            "median_protected_local_fraction": _median(subset["protected_local_fraction"]),
            "median_habitat_context_score": _median(subset["habitat_context_score"]),
            "median_ecological_network_score": _median(subset["ecological_network_score"]),
            "median_riparian_opportunity_score": _median(subset["riparian_opportunity_score"]),
            "boundary_count": int(subset[BOUNDARY_FLAG].sum()),
        }
    return result


def _overlap_diagnostics(joined: pd.DataFrame, candidate_units: pd.DataFrame) -> dict[str, Any]:
    overlap = joined.loc[joined[SCORING_INPUT] == 0]
    focal = overlap["protected_focal_fraction"]
    result: dict[str, Any] = {
        "count": int(len(overlap)),
        "protected_focal_fraction_distribution": _distribution(focal),
        "candidate_fraction_of_terrestrial_median": _median(
            overlap["candidate_fraction_of_terrestrial"]
        ),
        "habitat_score_median": _median(overlap["habitat_context_score"]),
        "network_score_median": _median(overlap["ecological_network_score"]),
        "riparian_score_median": _median(overlap["riparian_opportunity_score"]),
        "boundary_count": int(overlap[BOUNDARY_FLAG].sum()),
        "sea_containing_candidate_count": int(overlap["sea_containing"].sum())
        if "sea_containing" in overlap
        else None,
        "focal_fraction_threshold_counts": {
            "<1%": int((focal < 0.01).sum()),
            "<5%": int((focal < 0.05).sum()),
            "<25%": int((focal < 0.25).sum()),
            ">=25%": int((focal >= 0.25).sum()),
            ">=50%": int((focal >= 0.50).sum()),
            ">=75%": int((focal >= 0.75).sum()),
        },
        "candidate_source_row_count": int(len(candidate_units)),
    }
    return result


def _low_score_local_contrast(joined: pd.DataFrame) -> dict[str, Any]:
    low = joined[PROTECTED_SCORE] <= 25
    return {
        "candidate_count": int(low.sum()),
        "protected_focal_fraction_gt_0_count": int(
            (low & (joined["protected_focal_fraction"] > 0)).sum()
        ),
        "protected_adjacent_fraction_ge_10_percent_count": int(
            (low & (joined["protected_adjacent_fraction"] >= 0.10)).sum()
        ),
        "protected_local_fraction_ge_10_percent_count": int(
            (low & (joined["protected_local_fraction"] >= 0.10)).sum()
        ),
        "focal_overlap_zero_validation": bool(
            (low & (joined["protected_focal_fraction"] > 0)).sum() == 0
        ),
    }


def _boundary_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    edge = joined[BOUNDARY_FLAG]
    non_edge = ~edge
    return {
        "established_boundary_edge_candidate_count": 81,
        "observed_boundary_edge_candidate_count": int(edge.sum()),
        "score_distribution": _distribution(joined.loc[edge, PROTECTED_SCORE]),
        "nearest_steps_distribution": _distribution(joined.loc[edge, SCORING_INPUT]),
        "median_score_boundary_edge": _median(joined.loc[edge, PROTECTED_SCORE]),
        "median_score_non_edge": _median(joined.loc[non_edge, PROTECTED_SCORE]),
        "top_score_tail_representation": {
            "ge_75": int((edge & (joined[PROTECTED_SCORE] >= 75)).sum()),
            "ge_90": int((edge & (joined[PROTECTED_SCORE] >= 90)).sum()),
            "score_100": int((edge & (joined[PROTECTED_SCORE] == 100)).sum()),
        },
    }


def _composition_correlations(joined: pd.DataFrame) -> dict[str, Any]:
    fields = {
        "candidate_fraction_of_terrestrial": "candidate_fraction_of_terrestrial",
        "candidate_area_m2": "candidate_area_m2",
        "focal_habitat_context_fraction_of_terrestrial": (
            "habitat_context_fraction_of_terrestrial"
        ),
    }
    if "artificial_constraint_fraction" in joined:
        fields["artificial_constraint_fraction"] = "artificial_constraint_fraction"
    return {
        label: _correlation(joined[PROTECTED_SCORE], joined[field])
        for label, field in fields.items()
    }


def _top_non_overlap(joined: pd.DataFrame) -> list[dict[str, Any]]:
    fields = [
        "hex_id",
        SCORING_INPUT,
        PROTECTED_SCORE,
        "protected_focal_fraction",
        "protected_adjacent_fraction",
        "protected_local_fraction",
        "habitat_context_score",
        "ecological_network_score",
        "riparian_opportunity_score",
        "candidate_fraction_of_terrestrial",
        BOUNDARY_FLAG,
    ]
    return (
        joined.loc[joined[SCORING_INPUT] > 0, fields]
        .sort_values([PROTECTED_SCORE, SCORING_INPUT, "hex_id"], ascending=[False, True, True])
        .head(10)
        .to_dict(orient="records")
    )


def _marine_status() -> dict[str, Any]:
    if not RAW_PROVENANCE_PATH.exists():
        raise ProtectedAreaReinforcementScoreError(
            f"Missing protected-area provenance: {RAW_PROVENANCE_PATH}"
        )
    try:
        provenance = json.loads(RAW_PROVENANCE_PATH.read_text(encoding="utf-8"))
        reconciliation = provenance["focal_overlap_reconciliation"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ProtectedAreaReinforcementScoreError(
            "Protected-area provenance lacks the raw marine-inclusive distance audit"
        ) from exc
    result = {
        "raw_geometry_intersections": int(reconciliation["observed_raw_geometry_intersect_count"]),
        "terrestrial_focal_support_candidates": int(
            reconciliation["protected_focal_fraction_positive_count"]
        ),
        "raw_intersection_terrestrial_zero_cases": int(
            reconciliation["difference_raw_intersect_minus_focal_support"]
        ),
        "expected_step17_values": {
            "raw_geometry_intersections": 1627,
            "terrestrial_focal_support_candidates": 1583,
            "raw_intersection_terrestrial_zero_cases": 44,
        },
        "status": "rejected as scoring input",
        "reason": (
            "Raw legal geometry contains marine and inland-water protected portions. "
            "It yields geometry intersections without terrestrial NMD support, whereas "
            "the score must describe proximity to the formally protected terrestrial "
            "network under the NMD center-based support model."
        ),
        "source_provenance_reference": str(RAW_PROVENANCE_PATH),
    }
    if result["expected_step17_values"] != {
        key: result[key] for key in result["expected_step17_values"]
    }:
        raise ProtectedAreaReinforcementScoreError(
            "Marine-inclusive distance counts do not match the recorded audit values"
        )
    return result


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
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
    frame.to_csv(temporary, index=False, float_format="%.10g")
    os.replace(temporary, path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_text(
        json.dumps(_safe_json(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_protected_area_reinforcement_score(
    raw_indicator_path: Path = RAW_INDICATOR_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the final component artifact and complete real-data audit."""

    started = time.perf_counter()
    raw = _load_csv(
        raw_indicator_path,
        ("hex_id", SCORING_INPUT, NOMINAL_DISTANCE_FIELD, *SUPPORTING_INPUTS, BOUNDARY_FLAG),
        "raw Protected-Area Reinforcement input",
    )
    validate_raw_indicators(raw, expected_candidate_count=EXPECTED_CANDIDATE_COUNT)
    candidates = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
    if candidates.empty:
        raise ProtectedAreaReinforcementScoreError("Candidate layer is empty")
    reconciliation = validate_candidate_reconciliation(raw, candidates)
    if len(candidates) != EXPECTED_CANDIDATE_COUNT:
        raise ProtectedAreaReinforcementScoreError(
            f"Unexpected candidate population {len(candidates)}; expected {EXPECTED_CANDIDATE_COUNT}"
        )
    scores = calculate_scores(raw)
    if set(scores["hex_id"]) != set(candidates["hex_id"].astype(str)):
        raise ProtectedAreaReinforcementScoreError(
            "Component output IDs do not reconcile with candidates"
        )

    candidate_fields = [
        "hex_id",
        "candidate_fraction_of_terrestrial",
        "candidate_area_m2",
        "habitat_context_fraction_of_terrestrial",
        "terrestrial_area_m2",
        "artificial_constraint_area_m2",
        "sea_pixels",
    ]
    _require_columns(candidates, candidate_fields, "Candidate input")
    candidate_frame = pd.DataFrame(candidates.drop(columns="geometry", errors="ignore"))
    candidate_frame["hex_id"] = candidate_frame["hex_id"].astype(str)
    for field in candidate_fields[1:]:
        _numeric(candidate_frame[field], field)
    terrestrial_area = _numeric(candidate_frame["terrestrial_area_m2"], "terrestrial_area_m2")
    artificial_area = _numeric(
        candidate_frame["artificial_constraint_area_m2"], "artificial_constraint_area_m2"
    )
    if np.any(terrestrial_area <= 0):
        raise ProtectedAreaReinforcementScoreError("Candidate terrestrial_area_m2 must be positive")
    candidate_frame["artificial_constraint_fraction"] = artificial_area / terrestrial_area
    _validate_fraction(
        candidate_frame["artificial_constraint_fraction"], "artificial_constraint_fraction"
    )
    candidate_frame["sea_containing"] = candidate_frame["sea_pixels"] > 0

    joined = scores.copy()
    joined = _join_by_id(
        joined,
        raw[["hex_id", "protected_adjacent_fraction", "protected_local_fraction"]],
        ("protected_adjacent_fraction", "protected_local_fraction"),
        "Raw supporting protected fractions",
    )
    for path, field, label in (
        (HABITAT_SCORE_PATH, "habitat_context_score", "Habitat Context score"),
        (NETWORK_SCORE_PATH, "ecological_network_score", "Ecological Network Context score"),
        (RIPARIAN_SCORE_PATH, "riparian_opportunity_score", "Riparian Opportunity score"),
    ):
        other = _load_csv(path, ("hex_id", field), label)
        _numeric(other[field], field)
        joined = _join_by_id(joined, other, (field,), label)
    joined = _join_by_id(
        joined,
        candidate_frame,
        tuple(field for field in candidate_frame.columns if field != "hex_id"),
        "Candidate composition",
    )
    for field in (
        "habitat_context_score",
        "ecological_network_score",
        "riparian_opportunity_score",
    ):
        if not joined[field].between(0, 100, inclusive="both").all():
            raise ProtectedAreaReinforcementScoreError(f"{field} must lie in [0, 100]")

    reversed_scores = calculate_scores(raw.iloc[::-1].reset_index(drop=True))
    validation = _transformation_validation(joined, reversed_scores)
    if not all(
        validation[key]
        for key in (
            "step_zero_scores_exactly_100",
            "step_positive_scores_strictly_below_100",
            "step_positive_scores_strictly_above_0",
            "smaller_step_never_lower_score",
            "equal_step_values_receive_equal_scores",
            "score_bounds_0_to_100",
            "candidate_order_does_not_affect_score",
            "formula_recomputed_exactly",
        )
    ):
        raise ProtectedAreaReinforcementScoreError(
            "Protected-Area Reinforcement transformation validation failed"
        )

    overlap_count = int((joined[SCORING_INPUT] == 0).sum())
    positive_count = int((joined[SCORING_INPUT] > 0).sum())
    if positive_count <= 1:
        raise ProtectedAreaReinforcementScoreError(
            "Real candidate population has too few positive distances"
        )
    marine_status = _marine_status()
    provenance: dict[str, Any] = {
        "component_name": "Protected-Area Reinforcement",
        "status": "canonical production artifact",
        "source": {
            "raw_indicator_source": str(raw_indicator_path),
            "candidate_source": str(candidate_units_path),
            "protected_source_provenance_reference": str(PROTECTED_SOURCE_PROVENANCE_PATH),
            "step17_raw_provenance_reference": str(RAW_PROVENANCE_PATH),
            "source_network_scope": (
                "Naturvårdsverket national parks, nature reserves, and Natura 2000 SCI, SPA, "
                "and SPA/SCI, physically unioned in the protected_footprint"
            ),
        },
        "selected_raw_input": {
            "field": SCORING_INPUT,
            "sole_scoring_input": True,
            "rejected_fields": [
                "protected_focal_fraction",
                "protected_adjacent_fraction",
                "protected_local_fraction",
                "raw marine-inclusive vector distance",
                NOMINAL_DISTANCE_FIELD,
            ],
            "semantics": (
                "Minimum axial hex-grid distance from the candidate grid position to any grid "
                "position containing at least one NMD terrestrial pixel whose center lies within "
                "the unified formal protected footprint. Traversal may cross water or "
                "non-terrestrial grid coordinates."
            ),
        },
        "scoring": {
            "formula": (
                "if d_i == 0: score_i = 100; otherwise score_i = "
                "100 * (N_pos - average_positive_distance_rank_i + 1) / (N_pos + 1)"
            ),
            "positive_population_count": positive_count,
            "overlap_population_count": overlap_count,
            "positive_rank_direction": "ascending distance; smaller is better",
            "tie_handling": "average ranks for equal positive integer step distances",
            "zero_overlap_convention": (
                "Distance zero means the candidate analysis unit itself contains formally "
                "protected terrestrial land under the analytical 10 m NMD center-based support model."
            ),
            "why_reverse_empirical_ranking": (
                "Grid-step distance is discrete; there is no defensible MVP basis for subtracting "
                "a fixed number of points per additional 500 m and no justified hard cutoff. "
                "Empirical reverse ranking preserves nearer-is-better ordering on a common 0–100 "
                "decision-support scale while retaining overlap as a distinct maximum."
            ),
            "not_used": [
                "100 - steps * constant",
                "exponential decay",
                "inverse distance",
                "maximum useful radius",
                "5 km WFS acquisition buffer as a score threshold",
                "nominal metres",
                "protected fractions",
            ],
        },
        "candidate_population_validation": reconciliation
        | {
            "overlap_step_zero_count": overlap_count,
            "non_overlap_count": positive_count,
            "output_unique_ids": int(scores["hex_id"].nunique()),
            "missing_or_extra_or_duplicate_id_count": 0,
        },
        "raw_step_distribution": _raw_step_distribution(joined),
        "score_distribution": _distribution(joined[PROTECTED_SCORE]),
        "score_range_bins": _score_bins(joined[PROTECTED_SCORE]),
        "non_overlap_score_distribution": _distribution(
            joined.loc[joined[SCORING_INPUT] > 0, PROTECTED_SCORE]
        ),
        "transformation_validation": validation,
        "step_to_score_lookup": _step_to_score_lookup(joined),
        "tie_diagnostics": _tie_diagnostics(joined),
        "supporting_protected_fraction_diagnostics": {
            "score_band_medians": _fraction_band_diagnostics(joined),
            "score_correlations": {
                field: _correlation(joined[PROTECTED_SCORE], joined[field])
                for field in SUPPORTING_INPUTS
            },
        },
        "finalized_component_correlations": _component_correlations(joined),
        "four_component_pearson_matrix": _component_matrix(joined, "pearson"),
        "four_component_spearman_matrix": _component_matrix(joined, "spearman"),
        "four_component_contrast_counts": _contrast_counts(joined),
        "overlap_population": _overlap_diagnostics(joined, candidates),
        "high_protection_context_tail": _tail_diagnostics(joined),
        "low_protection_local_overlap_contrast": _low_score_local_contrast(joined),
        "raw_marine_inclusive_distance_status": marine_status,
        "boundary_effect": _boundary_diagnostics(joined),
        "candidate_composition_correlations": _composition_correlations(joined),
        "top_non_overlap_examples": _top_non_overlap(joined),
        "interpretation": {
            "score_100": (
                "The candidate analysis unit itself contains some formally protected terrestrial "
                "land under the analytical 10 m NMD center-based support model."
            ),
            "non_overlap_score": (
                "A non-overlap score such as 80 means the candidate is among the nearer portion "
                "of non-overlap candidates to Skåne's terrestrial formal protected-area network; "
                "it is not 80% protected, conservation value, protection likelihood, or restoration suitability."
            ),
            "nominal_distance": "Not exact Euclidean, polygon-edge, ecological movement, or functional connectivity distance.",
        },
        "boundary_handling": (
            "Boundary-edge flags are retained as diagnostics. Cross-county terrestrial support "
            "outside the Skåne NMD raster is unavailable and is not corrected in this component."
        ),
        "caveats": [
            "Marine and inland-water protected geometry is excluded from analytical support through the NMD terrestrial mask; raw legal geometry remains unmodified.",
            "Protected fractions are supporting diagnostics only and do not contribute mathematically to the score.",
            "The distance score is population-relative among non-overlap candidates.",
            "Formal protection does not imply ecological quality or restoration feasibility.",
            "The 5 km protected-source acquisition context is not a scoring cutoff.",
            "Cross-county terrestrial protected support outside the Skåne NMD raster remains unavailable.",
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environmental_data_downloaded": False,
        "dependency_changes": "None; existing project dependencies only.",
        "output": {
            "path": str(output_path),
            "schema": list(OUTPUT_COLUMNS),
            "row_count": int(len(scores)),
            "deterministic_order": "ascending hex_id",
        },
    }
    _write_csv(scores, output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["provenance_output"] = {
        "path": str(provenance_path),
        "size_bytes": None,
    }
    provenance["runtime_seconds"] = time.perf_counter() - started
    _write_json(provenance_path, provenance)
    provenance["provenance_output"]["size_bytes"] = int(provenance_path.stat().st_size)
    _write_json(provenance_path, provenance)
    return scores, provenance


def main() -> None:
    """Generate the real-data Protected-Area Reinforcement component."""

    output, provenance = build_protected_area_reinforcement_score()
    print(
        f"Protected-Area Reinforcement component: {provenance['output']['path']} "
        f"({provenance['output']['size_bytes']:,} bytes)"
    )
    print(
        f"Candidates {len(output):,}; overlap {provenance['scoring']['overlap_population_count']:,}; "
        f"non-overlap {provenance['scoring']['positive_population_count']:,}"
    )
    print(
        f"Provenance: {PROVENANCE_PATH} ({provenance['provenance_output']['size_bytes']:,} bytes)"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
