"""Build and audit the canonical five-component prioritization model.

This module integrates the five finalized component artifacts only.  It does
not recalculate indicators, normalize component scores, or define hard gates.
The three final MVP preset vectors are canonical here; the historical Step 22
sensitivity module imports the shared Balanced vector but remains the source
of the reproducible experimental audit.
"""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import geopandas as gpd
import numpy as np
import pandas as pd

CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
OUTPUT_PATH = Path("data/processed/prioritization/prioritization_scores.csv")
PROVENANCE_PATH = Path("data/processed/prioritization/prioritization_scores.provenance.json")
PRESETS_PATH = Path("data/processed/prioritization/presets.json")
BALANCED_BASELINE_OUTPUT_PATH = Path("data/processed/prioritization/balanced_baseline.csv")
BALANCED_BASELINE_PROVENANCE_PATH = Path(
    "data/processed/prioritization/balanced_baseline.provenance.json"
)
EXPECTED_CANDIDATE_COUNT = 26_395
BOUNDARY_FLAG = "boundary_edge_flag"
BALANCED_SCORE = "balanced_score"
BALANCED_WEIGHTS = OrderedDict(
    [
        ("habitat_context_score", 0.20),
        ("ecological_network_score", 0.20),
        ("riparian_opportunity_score", 0.20),
        ("protected_area_reinforcement_score", 0.20),
        ("restoration_land_availability_score", 0.20),
    ]
)
CONNECTIVITY_FIRST_WEIGHTS = OrderedDict(
    [
        ("habitat_context_score", 0.20),
        ("ecological_network_score", 0.30),
        ("riparian_opportunity_score", 0.10),
        ("protected_area_reinforcement_score", 0.25),
        ("restoration_land_availability_score", 0.15),
    ]
)
RIPARIAN_RESTORATION_WEIGHTS = OrderedDict(
    [
        ("habitat_context_score", 0.20),
        ("ecological_network_score", 0.10),
        ("riparian_opportunity_score", 0.35),
        ("protected_area_reinforcement_score", 0.15),
        ("restoration_land_availability_score", 0.20),
    ]
)
# Backwards-compatible Step 21 name.  The canonical final definitions above
# remain the only production source of weight values.
WEIGHTS = BALANCED_WEIGHTS
PRESET_DEFINITIONS = OrderedDict(
    [
        (
            "balanced",
            {
                "name": "Balanced",
                "purpose": "neutral equal-component reference",
                "weights": BALANCED_WEIGHTS,
            },
        ),
        (
            "connectivity_first",
            {
                "name": "Connectivity First",
                "purpose": "emphasize ecological-network configuration and protected-network reinforcement",
                "weights": CONNECTIVITY_FIRST_WEIGHTS,
            },
        ),
        (
            "riparian_restoration",
            {
                "name": "Riparian Restoration",
                "purpose": "emphasize focal wetland/inland-water restoration opportunity",
                "weights": RIPARIAN_RESTORATION_WEIGHTS,
            },
        ),
    ]
)
COMPONENTS = OrderedDict(
    [
        (
            "Habitat Context",
            {
                "path": Path("data/processed/components/habitat_context.csv"),
                "score_field": "habitat_context_score",
                "definition": "relative surrounding two-ring habitat-context proxy",
            },
        ),
        (
            "Ecological Network Context",
            {
                "path": Path("data/processed/components/ecological_network.csv"),
                "score_field": "ecological_network_score",
                "definition": "opposing-side neighborhood configuration proxy",
            },
        ),
        (
            "Riparian Opportunity",
            {
                "path": Path("data/processed/components/riparian_opportunity.csv"),
                "score_field": "riparian_opportunity_score",
                "definition": "relative focal riparian/water-context opportunity",
            },
        ),
        (
            "Protected-Area Reinforcement",
            {
                "path": Path("data/processed/components/protected_area_reinforcement.csv"),
                "score_field": "protected_area_reinforcement_score",
                "definition": "relative reinforcement of terrestrial protected areas",
            },
        ),
        (
            "Restoration Land Availability",
            {
                "path": Path("data/processed/components/land_restoration_feasibility.csv"),
                "score_field": "restoration_land_availability_score",
                "definition": "relative mapped candidate hectares available in the unit",
            },
        ),
    ]
)
SCORE_FIELDS = tuple(WEIGHTS)
PRESET_SCORE_FIELDS = OrderedDict(
    (preset_id, f"{preset_id}_score") for preset_id in PRESET_DEFINITIONS
)
OUTPUT_COLUMNS = (
    "hex_id",
    *SCORE_FIELDS,
    BALANCED_SCORE,
    BOUNDARY_FLAG,
)
FINAL_OUTPUT_COLUMNS = ("hex_id", *SCORE_FIELDS, *PRESET_SCORE_FIELDS.values(), BOUNDARY_FLAG)
CONTRIBUTION_FIELDS = (
    "habitat_contribution",
    "network_contribution",
    "riparian_contribution",
    "protection_contribution",
    "availability_contribution",
)
COMPONENT_LABELS = OrderedDict(
    zip(SCORE_FIELDS, ["Habitat", "Network", "Riparian", "Protection", "Availability"])
)
STRICT_TOLERANCE = 1e-10


class PrioritizationModelError(ValueError):
    """Raised when finalized integration inputs violate the model contract."""


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise PrioritizationModelError(f"{label} is missing required fields: {missing}")


def normalize_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    """Normalize and validate the candidate key without changing its values."""

    _require_columns(frame, ("hex_id",), label)
    if frame["hex_id"].isna().any():
        raise PrioritizationModelError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if ids.str.strip().eq("").any():
        raise PrioritizationModelError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise PrioritizationModelError(f"{label} hex_id values must be unique")
    return ids


def normalize_boundary_flags(values: pd.Series, label: str = BOUNDARY_FLAG) -> pd.Series:
    """Accept the boolean encodings emitted by the component writers."""

    if values.isna().any():
        raise PrioritizationModelError(f"{label} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise PrioritizationModelError(f"{label} must contain only true/false values")
        return values.astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    if not normalized.isin(["true", "false"]).all():
        raise PrioritizationModelError(f"{label} must contain only true/false values")
    return normalized.eq("true")


def validate_component(
    frame: pd.DataFrame,
    score_field: str,
    label: str,
    expected_count: int | None = EXPECTED_CANDIDATE_COUNT,
) -> pd.DataFrame:
    """Validate one authoritative finalized component artifact."""

    if not isinstance(frame, pd.DataFrame):
        raise PrioritizationModelError(f"{label} must be a pandas DataFrame")
    _require_columns(frame, ("hex_id", score_field), label)
    ids = normalize_ids(frame, label)
    if expected_count is not None and len(frame) != expected_count:
        raise PrioritizationModelError(
            f"{label} has {len(frame):,} rows; expected {expected_count:,}"
        )
    values = pd.to_numeric(frame[score_field], errors="coerce")
    if values.isna().any():
        raise PrioritizationModelError(f"{label} {score_field} contains missing/non-numeric values")
    numeric = values.to_numpy(dtype=float)
    if not np.all(np.isfinite(numeric)):
        raise PrioritizationModelError(f"{label} {score_field} contains non-finite values")
    if np.any((numeric < 0) | (numeric > 100)):
        raise PrioritizationModelError(f"{label} {score_field} must lie in [0, 100]")
    result = frame.copy()
    result["hex_id"] = ids
    result[score_field] = numeric
    if BOUNDARY_FLAG in result.columns:
        result[BOUNDARY_FLAG] = normalize_boundary_flags(
            result[BOUNDARY_FLAG], f"{label} {BOUNDARY_FLAG}"
        )
    return result


def validate_candidate_source(
    candidate_units: pd.DataFrame,
    expected_count: int | None = EXPECTED_CANDIDATE_COUNT,
) -> pd.DataFrame:
    """Validate the authoritative candidate source used for ID reconciliation."""

    if not isinstance(candidate_units, pd.DataFrame):
        raise PrioritizationModelError("Candidate source must be a pandas DataFrame")
    _require_columns(candidate_units, ("hex_id",), "Candidate source")
    ids = normalize_ids(candidate_units, "Candidate source")
    if expected_count is not None and len(candidate_units) != expected_count:
        raise PrioritizationModelError(
            f"Candidate source has {len(candidate_units):,} rows; expected {expected_count:,}"
        )
    result = candidate_units.copy()
    result["hex_id"] = ids
    return result


def reconcile_component_ids(
    component: pd.DataFrame,
    candidate_units: pd.DataFrame,
    label: str,
) -> dict[str, Any]:
    """Return exact candidate/component reconciliation diagnostics."""

    component_ids = set(normalize_ids(component, label))
    candidate_ids = set(normalize_ids(candidate_units, "Candidate source"))
    missing = sorted(candidate_ids - component_ids)
    extra = sorted(component_ids - candidate_ids)
    result = {
        "authoritative_candidate_count": int(len(candidate_units)),
        "component_rows": int(len(component)),
        "duplicate_component_ids": int(component["hex_id"].duplicated().sum()),
        "missing_candidate_ids": missing,
        "missing_candidate_id_count": len(missing),
        "extra_component_ids": extra,
        "extra_component_id_count": len(extra),
        "exact_one_to_one": not missing and not extra and len(component) == len(candidate_units),
    }
    if not result["exact_one_to_one"]:
        raise PrioritizationModelError(
            f"{label} does not reconcile exactly with candidate source: "
            f"missing={len(missing)}, extra={len(extra)}, rows={len(component)}"
        )
    return result


def reconcile_boundary_flags(components: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Require all available component boundary-flag copies to agree by ID."""

    reference: pd.Series | None = None
    mismatches: dict[str, int] = {}
    for label, frame in components.items():
        _require_columns(frame, ("hex_id", BOUNDARY_FLAG), label)
        flags = frame.copy()
        flags["hex_id"] = normalize_ids(flags, label)
        flags[BOUNDARY_FLAG] = normalize_boundary_flags(
            flags[BOUNDARY_FLAG], f"{label} {BOUNDARY_FLAG}"
        )
        current = flags.set_index("hex_id")[BOUNDARY_FLAG].sort_index()
        if reference is None:
            reference = current
            mismatches[label] = 0
            continue
        mismatch = int((current != reference).sum())
        mismatches[label] = mismatch
        if mismatch:
            raise PrioritizationModelError(
                f"Boundary flags disagree between finalized component artifacts; "
                f"{label} has {mismatch} mismatches"
            )
    if reference is None:
        raise PrioritizationModelError("No component boundary flags were supplied")
    return mismatches


def join_component_scores(
    candidate_units: pd.DataFrame,
    components: dict[str, tuple[pd.DataFrame, str]],
) -> pd.DataFrame:
    """Join validated component scores one-to-one to the candidate source."""

    candidate = validate_candidate_source(candidate_units, expected_count=None)[["hex_id"]]
    joined = candidate.copy()
    for index, (label, (component, score_field)) in enumerate(components.items()):
        _require_columns(component, ("hex_id", score_field, BOUNDARY_FLAG), label)
        extra_fields = [
            field
            for field in (
                "candidate_land_area_ha",
                "riparian_focal_fraction",
                "nearest_protected_hex_steps",
            )
            if field in component.columns
        ]
        boundary_fields = [BOUNDARY_FLAG] if index == 0 else []
        piece = component[["hex_id", score_field, *boundary_fields, *extra_fields]].copy()
        piece["hex_id"] = normalize_ids(piece, label)
        if BOUNDARY_FLAG in piece.columns:
            piece[BOUNDARY_FLAG] = normalize_boundary_flags(
                piece[BOUNDARY_FLAG], f"{label} {BOUNDARY_FLAG}"
            )
        joined = joined.merge(
            piece,
            on="hex_id",
            how="left",
            validate="one_to_one",
            suffixes=("", "_component"),
        )
    if BOUNDARY_FLAG not in joined.columns:
        raise PrioritizationModelError("Joined components do not contain boundary flags")
    if joined[list(SCORE_FIELDS)].isna().any().any():
        raise PrioritizationModelError("Joined baseline input contains missing component scores")
    return joined


def validate_weights(
    weights: Mapping[str, float], *, require_positive: bool = True
) -> OrderedDict[str, float]:
    """Validate a complete five-component weight vector."""

    if set(weights) != set(SCORE_FIELDS):
        missing = sorted(set(SCORE_FIELDS).difference(weights))
        extra = sorted(set(weights).difference(SCORE_FIELDS))
        raise PrioritizationModelError(
            f"Weights must contain exactly the five score fields; missing={missing}, extra={extra}"
        )
    normalized: OrderedDict[str, float] = OrderedDict()
    for field in SCORE_FIELDS:
        try:
            value = float(weights[field])
        except (TypeError, ValueError) as error:
            raise PrioritizationModelError(f"Weight for {field} must be numeric") from error
        if not math.isfinite(value):
            raise PrioritizationModelError(f"Weight for {field} must be finite")
        if value < 0:
            raise PrioritizationModelError(f"Weight for {field} must be non-negative")
        if require_positive and value <= 0:
            raise PrioritizationModelError(f"Weight for {field} must be greater than zero")
        normalized[field] = value
    weight_sum = math.fsum(normalized.values())
    if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=STRICT_TOLERANCE):
        raise PrioritizationModelError(f"Weights must sum to 1.0; got {weight_sum!r}")
    return normalized


def weighted_mean(frame: pd.DataFrame, weights: Mapping[str, float] = WEIGHTS) -> pd.Series:
    """Compute the explicit weighted arithmetic mean used by the baseline."""

    normalized_weights = validate_weights(weights)
    _require_columns(frame, normalized_weights.keys(), "Weighted-mean input")
    numeric = frame[list(normalized_weights)].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise PrioritizationModelError("Weighted-mean input contains non-finite component scores")
    weight_sum = math.fsum(normalized_weights.values())
    if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=STRICT_TOLERANCE):
        raise PrioritizationModelError(f"Weights must sum to 1.0; got {weight_sum!r}")
    result = pd.Series(0.0, index=frame.index, dtype=float)
    for field, weight in normalized_weights.items():
        result = result + weight * numeric[field]
    return result


def calculate_baseline(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate contributions and the equal-weight baseline in input order."""

    _require_columns(frame, ("hex_id", *SCORE_FIELDS), "Baseline input")
    result = frame.copy()
    for field, contribution in zip(SCORE_FIELDS, CONTRIBUTION_FIELDS):
        result[contribution] = float(WEIGHTS[field]) * result[field].astype(float)
    result[BALANCED_SCORE] = weighted_mean(result, WEIGHTS)
    return result


def _distribution(values: pd.Series | np.ndarray, include_n: bool = True) -> dict[str, Any]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    keys = ("min", "p10", "p25", "median", "mean", "std", "p75", "p90", "p95", "p99", "max")
    if numeric.empty:
        result: dict[str, Any] = {key: None for key in keys}
    else:
        quantiles = numeric.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
        result = {
            "min": float(numeric.min()),
            "p10": float(quantiles.loc[0.10]),
            "p25": float(quantiles.loc[0.25]),
            "median": float(quantiles.loc[0.50]),
            "mean": float(numeric.mean()),
            "std": float(numeric.std(ddof=0)),
            "p75": float(quantiles.loc[0.75]),
            "p90": float(quantiles.loc[0.90]),
            "p95": float(quantiles.loc[0.95]),
            "p99": float(quantiles.loc[0.99]),
            "max": float(numeric.max()),
        }
    if include_n:
        return {"n": int(len(numeric)), **result}
    return result


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


def _matrix(
    frame: pd.DataFrame, fields: list[str], labels: list[str], method: str
) -> dict[str, dict[str, float | None]]:
    values = frame[fields].astype(float)
    if method == "spearman":
        values = values.rank(method="average")
    matrix = values.corr(method="pearson")
    return {
        label: {
            other: float(matrix.loc[field, other_field])
            for other, other_field in zip(labels, fields)
        }
        for label, field in zip(labels, fields)
    }


def _rank_percentile(values: pd.Series) -> pd.Series:
    return (values.rank(method="average", ascending=True) - 1.0) / (len(values) - 1.0) * 100.0


def _top_mask(values: pd.Series, fraction: float) -> pd.Series:
    count = max(1, math.ceil(len(values) * fraction))
    order = pd.DataFrame({"value": values, "id": values.index}).sort_values(
        ["value", "id"], ascending=[False, True], kind="mergesort"
    )
    selected = order.head(count).index
    return values.index.isin(selected)


def _top_examples(frame: pd.DataFrame, count: int, ascending: bool) -> list[dict[str, Any]]:
    ordered = frame.sort_values(
        [BALANCED_SCORE, "hex_id"], ascending=[ascending, True], kind="mergesort"
    ).head(count)
    fields = ["hex_id", BALANCED_SCORE, *SCORE_FIELDS, BOUNDARY_FLAG]
    optional = [
        "candidate_land_area_ha",
        "riparian_focal_fraction",
        "nearest_protected_hex_steps",
    ]
    fields.extend(field for field in optional if field in ordered.columns)
    return ordered[fields].to_dict(orient="records")


def _component_distributions(frame: pd.DataFrame) -> dict[str, Any]:
    return {label: _distribution(frame[field]) for field, label in COMPONENT_LABELS.items()}


def _leave_one_out(frame: pd.DataFrame) -> dict[str, Any]:
    baseline_rank = _rank_percentile(frame[BALANCED_SCORE])
    baseline_top = _top_mask(frame[BALANCED_SCORE], 0.10)
    result: dict[str, Any] = {}
    for omitted in SCORE_FIELDS:
        included = [field for field in SCORE_FIELDS if field != omitted]
        score = frame[included].mean(axis=1)
        rank = _rank_percentile(score)
        absolute_change = (rank - baseline_rank).abs()
        top_mask = _top_mask(score, 0.10)
        result[COMPONENT_LABELS[omitted]] = {
            "omitted_field": omitted,
            "spearman_rank_correlation": float(baseline_rank.corr(rank, method="pearson")),
            "median_absolute_percentile_rank_change": float(absolute_change.median()),
            "p90_absolute_percentile_rank_change": float(absolute_change.quantile(0.90)),
            "candidates_moving_at_least_10_percentile_points": int((absolute_change >= 10).sum()),
            "candidates_moving_at_least_25_percentile_points": int((absolute_change >= 25).sum()),
            "top_10_percent_overlap_count": int((baseline_top & top_mask).sum()),
            "top_10_percent_overlap_percent_of_baseline": float(
                100.0 * (baseline_top & top_mask).sum() / baseline_top.sum()
            ),
        }
    return result


def _top_tail_profiles(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for fraction, label in (
        (0.25, "top_25_percent"),
        (0.10, "top_10_percent"),
        (0.05, "top_5_percent"),
        (0.01, "top_1_percent"),
    ):
        subset = frame.loc[_top_mask(frame[BALANCED_SCORE], fraction)]
        profile: dict[str, Any] = {"candidate_count": int(len(subset))}
        for field, component in COMPONENT_LABELS.items():
            profile[component] = {"median": float(subset[field].median())}
            if fraction == 0.05:
                profile[component].update(
                    {
                        "p10": float(subset[field].quantile(0.10)),
                        "p90": float(subset[field].quantile(0.90)),
                    }
                )
        result[label] = profile
    return result


def _compensability(frame: pd.DataFrame) -> dict[str, Any]:
    subset = frame.loc[_top_mask(frame[BALANCED_SCORE], 0.10)]
    minimum = subset[list(SCORE_FIELDS)].min(axis=1)
    return {
        "top_10_percent_candidate_count": int(len(subset)),
        "any_component_le_25": int((subset[list(SCORE_FIELDS)] <= 25).any(axis=1).sum()),
        "any_component_eq_0": int((subset[list(SCORE_FIELDS)] == 0).any(axis=1).sum()),
        "two_or_more_components_le_25": int(
            (subset[list(SCORE_FIELDS)] <= 25).sum(axis=1).ge(2).sum()
        ),
        "availability_le_25": int((subset["restoration_land_availability_score"] <= 25).sum()),
        "habitat_le_25": int((subset["habitat_context_score"] <= 25).sum()),
        "riparian_eq_0": int((subset["riparian_opportunity_score"] == 0).sum()),
        "minimum_component_score_distribution": _distribution(minimum),
    }


def _consistent_strength(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    scores = frame[list(SCORE_FIELDS)]
    for threshold in (50, 60, 75):
        mask = scores.ge(threshold).all(axis=1)
        result[f"all_five_ge_{threshold}"] = {
            "candidate_count": int(mask.sum()),
            "balanced_score_distribution": _distribution(frame.loc[mask, BALANCED_SCORE]),
        }
    return result


def _tradeoff(frame: pd.DataFrame) -> dict[str, Any]:
    ecology_fields = SCORE_FIELDS[:4]
    ecology = frame[list(ecology_fields)].mean(axis=1)
    availability = frame[SCORE_FIELDS[4]]
    ecology_quartile = pd.Series(
        pd.qcut(ecology.rank(method="first"), 4, labels=["Q1", "Q2", "Q3", "Q4"]), index=frame.index
    )
    availability_quartile = pd.Series(
        pd.qcut(availability.rank(method="first"), 4, labels=["Q1", "Q2", "Q3", "Q4"]),
        index=frame.index,
    )
    return {
        "ecological_context_mean_distribution": _distribution(ecology),
        "correlation_ecological_context_mean_vs_availability": _correlation(ecology, availability),
        "median_availability_by_ecological_context_quartile": {
            str(q): float(availability[ecology_quartile == q].median())
            for q in ("Q1", "Q2", "Q3", "Q4")
        },
        "median_ecological_context_mean_by_availability_quartile": {
            str(q): float(ecology[availability_quartile == q].median())
            for q in ("Q1", "Q2", "Q3", "Q4")
        },
        "threshold_counts": {
            "ecology_ge_75_and_availability_ge_75": int(
                ((ecology >= 75) & (availability >= 75)).sum()
            ),
            "ecology_ge_75_and_availability_le_25": int(
                ((ecology >= 75) & (availability <= 25)).sum()
            ),
            "ecology_le_25_and_availability_ge_75": int(
                ((ecology <= 25) & (availability >= 75)).sum()
            ),
        },
    }


def _winner_profile(frame: pd.DataFrame) -> dict[str, Any]:
    scores = frame[list(SCORE_FIELDS)]
    max_values = scores.max(axis=1)
    tie_counts = scores.eq(max_values, axis=0).sum(axis=1)
    winners = scores.idxmax(axis=1)
    counts = {
        COMPONENT_LABELS[field]: {
            "count": int((winners == field).sum()),
            "percent": float(100.0 * (winners == field).mean()),
        }
        for field in SCORE_FIELDS
    }
    return {
        "deterministic_tie_break_order": list(COMPONENT_LABELS.values()),
        "winner_counts": counts,
        "candidate_count_with_any_maximum_tie": int((tie_counts > 1).sum()),
        "tie_cardinality_counts": {
            str(k): int((tie_counts == k).sum()) for k in sorted(tie_counts.unique()) if k > 1
        },
    }


def _boundary_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    edge = frame[BOUNDARY_FLAG].astype(bool)
    result: dict[str, Any] = {
        "boundary_edge_candidate_count": int(edge.sum()),
        "non_edge_candidate_count": int((~edge).sum()),
        "boundary_edge_balanced_distribution": _distribution(frame.loc[edge, BALANCED_SCORE]),
        "non_edge_balanced_distribution": _distribution(frame.loc[~edge, BALANCED_SCORE]),
        "top_tail_counts": {},
    }
    for fraction, label in (
        (0.25, "top_25_percent"),
        (0.10, "top_10_percent"),
        (0.05, "top_5_percent"),
        (0.01, "top_1_percent"),
    ):
        mask = _top_mask(frame[BALANCED_SCORE], fraction)
        result["top_tail_counts"][label] = {
            "boundary_count": int((mask & edge).sum()),
            "boundary_percent_of_tail": float(100.0 * (mask & edge).sum() / mask.sum()),
            "boundary_percent_of_all_boundary_candidates": float(
                100.0 * (mask & edge).sum() / edge.sum()
            )
            if edge.sum()
            else None,
        }
    return result


def _spatial_sanity(frame: pd.DataFrame, candidate_units: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "output_candidate_count": int(len(frame)),
        "candidate_source_count": int(len(candidate_units)),
        "full_candidate_extent_covered": bool(set(frame.hex_id) == set(candidate_units.hex_id)),
        "all_balanced_scores_finite": bool(np.isfinite(frame[BALANCED_SCORE]).all()),
    }
    if {"grid_col", "grid_row"}.issubset(candidate_units.columns):
        joined = frame[["hex_id", BALANCED_SCORE]].merge(
            candidate_units[["hex_id", "grid_col", "grid_row"]],
            on="hex_id",
            how="left",
            validate="one_to_one",
        )
        for field in ("grid_col", "grid_row"):
            low, high = float(joined[field].min()), float(joined[field].max())
            edges = np.linspace(low, high, 4)
            bins = pd.cut(
                joined[field], bins=edges, labels=["low", "middle", "high"], include_lowest=True
            )
            result[f"{field}_thirds"] = {
                str(label): {
                    "candidate_count": int((bins == label).sum()),
                    "valid_balanced_count": int(
                        joined.loc[bins == label, BALANCED_SCORE].notna().sum()
                    ),
                }
                for label in ("low", "middle", "high")
            }
        col_bins = pd.cut(
            joined["grid_col"],
            bins=np.linspace(joined.grid_col.min(), joined.grid_col.max(), 4),
            labels=False,
            include_lowest=True,
        )
        row_bins = pd.cut(
            joined["grid_row"],
            bins=np.linspace(joined.grid_row.min(), joined.grid_row.max(), 4),
            labels=False,
            include_lowest=True,
        )
        top_mask = _top_mask(joined[BALANCED_SCORE], 0.01)
        result["top_1_percent_coordinate_bin_count"] = int(
            pd.DataFrame({"col": col_bins[top_mask], "row": row_bins[top_mask]})
            .drop_duplicates()
            .shape[0]
        )
        result["broad_coordinate_bins_all_valid"] = bool(
            all(
                value["candidate_count"] > 0
                and value["valid_balanced_count"] == value["candidate_count"]
                for key, value in result.items()
                if key.endswith("_thirds")
                for value in value.values()
            )
        )
    else:
        result["warning"] = (
            "candidate source lacks grid_col/grid_row; coordinate-bin audit not available"
        )
    return result


def _influence_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, contribution in zip(SCORE_FIELDS, CONTRIBUTION_FIELDS):
        result[COMPONENT_LABELS[field]] = {
            "score_field": field,
            "score_standard_deviation": float(frame[field].std(ddof=0)),
            "weighted_contribution_standard_deviation": float(frame[contribution].std(ddof=0)),
            "nominal_weight": float(WEIGHTS[field]),
            "correlation_with_balanced": _correlation(frame[field], frame[BALANCED_SCORE]),
            "covariance_contribution_with_balanced": float(
                np.cov(frame[contribution], frame[BALANCED_SCORE], ddof=0)[0, 1]
            ),
        }
    return result


def _audit(
    frame: pd.DataFrame, candidate_units: pd.DataFrame, reconciliation: dict[str, Any]
) -> dict[str, Any]:
    component_correlations = {
        COMPONENT_LABELS[field]: _correlation(frame[BALANCED_SCORE], frame[field])
        for field in SCORE_FIELDS
    }
    contribution_ranges = {
        COMPONENT_LABELS[field]: {
            "contribution_field": contribution,
            "min": float(frame[contribution].min()),
            "median": float(frame[contribution].median()),
            "p90": float(frame[contribution].quantile(0.90)),
            "max": float(frame[contribution].max()),
            "theoretical_min": 0.0,
            "theoretical_max": 20.0,
        }
        for field, contribution in zip(SCORE_FIELDS, CONTRIBUTION_FIELDS)
    }
    balanced_rank = _rank_percentile(frame[BALANCED_SCORE])
    frame = frame.copy()
    frame["_balanced_rank_percentile"] = balanced_rank
    return {
        "input_reconciliation": reconciliation,
        "component_distributions": _component_distributions(frame),
        "balanced_score_distribution": _distribution(frame[BALANCED_SCORE]),
        "balanced_score_bins": {
            "0–10": int((frame[BALANCED_SCORE] <= 10).sum()),
            ">10–25": int(((frame[BALANCED_SCORE] > 10) & (frame[BALANCED_SCORE] <= 25)).sum()),
            ">25–50": int(((frame[BALANCED_SCORE] > 25) & (frame[BALANCED_SCORE] <= 50)).sum()),
            ">50–75": int(((frame[BALANCED_SCORE] > 50) & (frame[BALANCED_SCORE] <= 75)).sum()),
            ">75–90": int(((frame[BALANCED_SCORE] > 75) & (frame[BALANCED_SCORE] <= 90)).sum()),
            ">90": int((frame[BALANCED_SCORE] > 90).sum()),
        },
        "formula_validation": {
            "weights": {field: float(value) for field, value in WEIGHTS.items()},
            "weight_sum": math.fsum(WEIGHTS.values()),
            "weights_sum_to_one": math.isclose(
                math.fsum(WEIGHTS.values()), 1.0, rel_tol=0.0, abs_tol=STRICT_TOLERANCE
            ),
            "balanced_equals_arithmetic_mean": bool(
                np.allclose(
                    frame[BALANCED_SCORE],
                    frame[list(SCORE_FIELDS)].mean(axis=1),
                    rtol=0.0,
                    atol=STRICT_TOLERANCE,
                )
            ),
            "balanced_in_0_100": bool(
                (
                    (frame[BALANCED_SCORE] >= 0)
                    & (frame[BALANCED_SCORE] <= 100)
                    & np.isfinite(frame[BALANCED_SCORE])
                ).all()
            ),
            "contribution_sum_equals_balanced": bool(
                np.allclose(
                    frame[list(CONTRIBUTION_FIELDS)].sum(axis=1),
                    frame[BALANCED_SCORE],
                    rtol=0.0,
                    atol=STRICT_TOLERANCE,
                )
            ),
            "row_order_independent": bool(
                np.allclose(
                    calculate_baseline(
                        frame.sample(frac=1.0, random_state=21).sort_values("hex_id")
                    )[BALANCED_SCORE].to_numpy(),
                    frame.sort_values("hex_id")[BALANCED_SCORE].to_numpy(),
                    rtol=0.0,
                    atol=STRICT_TOLERANCE,
                )
            ),
        },
        "balanced_vs_component_correlations": component_correlations,
        "empirical_component_influence": _influence_diagnostics(frame),
        "leave_one_component_out": _leave_one_out(frame),
        "top_tail_component_profiles": _top_tail_profiles(frame),
        "compensability": _compensability(frame),
        "consistently_strong_candidates": _consistent_strength(frame),
        "ecology_availability_tradeoff": _tradeoff(frame),
        "component_contribution_ranges": contribution_ranges,
        "top_balanced_examples": _top_examples(frame, 20, ascending=False),
        "bottom_balanced_examples": _top_examples(frame, 10, ascending=True),
        "component_winner_profile": _winner_profile(frame),
        "boundary_diagnostics": _boundary_diagnostics(frame),
        "spatial_coverage_sanity": _spatial_sanity(frame, candidate_units),
        "component_plus_balanced_pearson_matrix": _matrix(
            frame,
            [*SCORE_FIELDS, BALANCED_SCORE],
            [*COMPONENT_LABELS.values(), "Balanced"],
            "pearson",
        ),
        "component_plus_balanced_spearman_matrix": _matrix(
            frame,
            [*SCORE_FIELDS, BALANCED_SCORE],
            [*COMPONENT_LABELS.values(), "Balanced"],
            "spearman",
        ),
    }


def _preset_readiness(audit: dict[str, Any]) -> dict[str, Any]:
    correlations = audit["balanced_vs_component_correlations"]
    strongest = max(
        correlations.items(),
        key=lambda item: item[1]["spearman"] if item[1]["spearman"] is not None else -math.inf,
    )[0]
    loo = audit["leave_one_component_out"]
    return {
        "equal_weighting_produces_numerically_stable_score": audit["formula_validation"][
            "balanced_in_0_100"
        ]
        and audit["formula_validation"]["contribution_sum_equals_balanced"],
        "strongest_empirical_relationship_with_balanced_ranking": strongest,
        "strongest_spearman_correlation": correlations[strongest]["spearman"],
        "severe_single_component_dominance_observed": False,
        "dominance_assessment": "Nominal weights are equal; empirical influence differs with score distributions and correlations, but no component is assigned an adjusted weight in this step.",
        "leave_one_out_summary": {
            label: {
                "spearman_rank_correlation": values["spearman_rank_correlation"],
                "top_10_percent_overlap_percent_of_baseline": values[
                    "top_10_percent_overlap_percent_of_baseline"
                ],
            }
            for label, values in loo.items()
        },
        "high_balanced_severe_weaknesses_are_quantified": True,
        "ecological_availability_tradeoff_is_visible": True,
        "suitable_reference_for_later_preset_testing": True,
        "summary": "The equal-component-weight baseline is a transparent, numerically valid reference model. It remains compensatory and nominally allocates 80% to four ecological/context dimensions and 20% to land availability. It is suitable to carry forward for explicit preset testing, but is not itself a finalized user-facing policy preset.",
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )


def load_and_reconcile_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Load all finalized CSVs and reconcile them to the candidate source."""

    if not CANDIDATE_UNITS_PATH.exists():
        raise PrioritizationModelError(f"Missing candidate source: {CANDIDATE_UNITS_PATH}")
    candidate_units = validate_candidate_source(
        gpd.read_file(CANDIDATE_UNITS_PATH, layer=CANDIDATE_UNITS_LAYER)
    )
    reconciliation: dict[str, Any] = {
        "authoritative_candidate_path": str(CANDIDATE_UNITS_PATH),
        "authoritative_candidate_count": int(len(candidate_units)),
        "components": {},
        "boundary_flag_field": BOUNDARY_FLAG,
        "boundary_flag_mismatch_count_by_component": {},
        "boundary_flags_agree": True,
    }
    component_frames: dict[str, pd.DataFrame] = {}
    for label, config in COMPONENTS.items():
        path = Path(config["path"])
        if not path.exists():
            raise PrioritizationModelError(f"Missing finalized component artifact: {path}")
        raw = pd.read_csv(path)
        component = validate_component(raw, config["score_field"], label)
        reconciliation["components"][label] = reconcile_component_ids(
            component, candidate_units, label
        )
        _require_columns(component, (BOUNDARY_FLAG,), label)
        keep = component[["hex_id", config["score_field"], BOUNDARY_FLAG]].copy()
        optional = [
            "candidate_land_area_ha",
            "riparian_focal_fraction",
            "nearest_protected_hex_steps",
        ]
        for field in optional:
            if field in component.columns:
                keep[field] = component[field]
        component_frames[label] = keep
    reconciliation["boundary_flag_mismatch_count_by_component"] = reconcile_boundary_flags(
        component_frames
    )
    reconciliation["boundary_flags_agree"] = all(
        mismatch == 0
        for mismatch in reconciliation["boundary_flag_mismatch_count_by_component"].values()
    )
    joined = join_component_scores(
        candidate_units,
        {
            label: (component_frames[label], config["score_field"])
            for label, config in COMPONENTS.items()
        },
    )
    reconciliation["final_joined_rows"] = int(len(joined))
    reconciliation["final_join_one_row_per_candidate"] = bool(
        len(joined) == len(candidate_units) and joined["hex_id"].is_unique
    )
    reconciliation["all_component_ids_reconcile_exactly"] = all(
        value["exact_one_to_one"] for value in reconciliation["components"].values()
    )
    return joined, candidate_units, reconciliation


SENSITIVITY_OUTPUT_PATH = Path("data/processed/prioritization/preset_sensitivity.csv")
SENSITIVITY_PROVENANCE_PATH = Path(
    "data/processed/prioritization/preset_sensitivity.provenance.json"
)


def calculate_prioritization_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate the three final weighted means in input order."""

    _require_columns(frame, ("hex_id", *SCORE_FIELDS), "Prioritization input")
    numeric = frame[list(SCORE_FIELDS)].apply(pd.to_numeric, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    if numeric.isna().any().any() or not np.isfinite(values).all():
        raise PrioritizationModelError(
            "Prioritization input contains missing, non-numeric, or non-finite component scores"
        )
    if np.any((values < 0) | (values > 100)):
        raise PrioritizationModelError("Prioritization component scores must lie in [0, 100]")
    result = frame.copy()
    for preset_id, config in PRESET_DEFINITIONS.items():
        result[PRESET_SCORE_FIELDS[preset_id]] = weighted_mean(result, config["weights"])
    score_values = result[list(PRESET_SCORE_FIELDS.values())].to_numpy(dtype=float)
    if not np.isfinite(score_values).all() or np.any((score_values < 0) | (score_values > 100)):
        raise PrioritizationModelError("Final preset scores must lie in [0, 100]")
    return result


def preset_metadata() -> OrderedDict[str, dict[str, Any]]:
    """Return stable application metadata generated from canonical definitions."""

    return OrderedDict(
        (
            preset_id,
            {
                "name": config["name"],
                "weights": OrderedDict(
                    (field, float(value)) for field, value in config["weights"].items()
                ),
            },
        )
        for preset_id, config in PRESET_DEFINITIONS.items()
    )


def _final_top_ids(frame: pd.DataFrame, score_field: str, fraction: float) -> set[str]:
    count = max(1, math.ceil(len(frame) * fraction))
    ordered = frame.sort_values([score_field, "hex_id"], ascending=[False, True], kind="mergesort")
    return set(ordered.head(count)["hex_id"])


def _final_top_n_ids(frame: pd.DataFrame, score_field: str, count: int) -> set[str]:
    ordered = frame.sort_values([score_field, "hex_id"], ascending=[False, True], kind="mergesort")
    return set(ordered.head(min(count, len(frame)))["hex_id"])


def _final_top_subset(frame: pd.DataFrame, score_field: str, fraction: float) -> pd.DataFrame:
    return frame[frame["hex_id"].isin(_final_top_ids(frame, score_field, fraction))].copy()


def _final_rank_percentile(frame: pd.DataFrame, score_field: str) -> pd.Series:
    if len(frame) <= 1:
        return pd.Series(0.0, index=frame.index)
    return (frame[score_field].rank(method="average") - 1.0) / (len(frame) - 1.0) * 100.0


def _final_ordinal_ranks(frame: pd.DataFrame, score_field: str) -> pd.Series:
    ordered = frame.sort_values([score_field, "hex_id"], ascending=[False, True], kind="mergesort")
    ranks = pd.Series(range(1, len(frame) + 1), index=ordered.index, dtype=int)
    return ranks.reindex(frame.index)


def _final_overlap(frame: pd.DataFrame, score_field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    balanced_field = PRESET_SCORE_FIELDS["balanced"]
    for fraction, label in (
        (0.25, "top_25_percent"),
        (0.10, "top_10_percent"),
        (0.05, "top_5_percent"),
        (0.01, "top_1_percent"),
    ):
        balanced_ids = _final_top_ids(frame, balanced_field, fraction)
        preset_ids = _final_top_ids(frame, score_field, fraction)
        common = len(balanced_ids & preset_ids)
        result[label] = {
            "tail_size": len(balanced_ids),
            "common_candidate_count": common,
            "overlap_percent_relative_to_tail": 100.0 * common / len(balanced_ids),
        }
    balanced_ids = _final_top_n_ids(frame, balanced_field, 100)
    preset_ids = _final_top_n_ids(frame, score_field, 100)
    common = len(balanced_ids & preset_ids)
    result["top_100"] = {
        "tail_size": len(balanced_ids),
        "common_candidate_count": common,
        "overlap_percent_relative_to_tail": 100.0 * common / len(balanced_ids),
    }
    return result


def _final_rank_movement(frame: pd.DataFrame, score_field: str) -> dict[str, Any]:
    balanced = _final_rank_percentile(frame, PRESET_SCORE_FIELDS["balanced"])
    preset = _final_rank_percentile(frame, score_field)
    absolute = (preset - balanced).abs()
    return {
        "median_absolute_percentile_movement": float(absolute.median()),
        "p90_absolute_percentile_movement": float(absolute.quantile(0.90)),
        "p95_absolute_percentile_movement": float(absolute.quantile(0.95)),
        "candidates_moving_at_least_10_percentile_points": int((absolute >= 10).sum()),
        "candidates_moving_at_least_25_percentile_points": int((absolute >= 25).sum()),
        "maximum_absolute_percentile_movement": float(absolute.max()),
    }


def _final_profile(subset: pd.DataFrame) -> dict[str, Any]:
    return {
        COMPONENT_LABELS[field]: {
            "field": field,
            "median": float(subset[field].median()),
            "p10": float(subset[field].quantile(0.10)),
            "p90": float(subset[field].quantile(0.90)),
        }
        for field in SCORE_FIELDS
    }


def _final_compensability(subset: pd.DataFrame) -> dict[str, Any]:
    values = subset[list(SCORE_FIELDS)]
    minimum = values.min(axis=1)
    return {
        "candidate_count": int(len(subset)),
        "any_component_le_25": int((values <= 25).any(axis=1).sum()),
        "any_component_eq_0": int((values == 0).any(axis=1).sum()),
        "two_or_more_components_le_25": int((values <= 25).sum(axis=1).ge(2).sum()),
        "median_minimum_component_score": float(minimum.median()),
        "p10_minimum_component_score": float(minimum.quantile(0.10)),
    }


def _final_availability_tradeoff(frame: pd.DataFrame, score_field: str) -> dict[str, Any]:
    top10 = _final_top_subset(frame, score_field, 0.10)
    availability = frame["restoration_land_availability_score"]
    return {
        "score_vs_availability_correlation": _correlation(frame[score_field], availability),
        "top_10_median_availability": float(top10["restoration_land_availability_score"].median()),
        "top_10_p10_availability": float(
            top10["restoration_land_availability_score"].quantile(0.10)
        ),
        "top_10_availability_le_25_count": int(
            (top10["restoration_land_availability_score"] <= 25).sum()
        ),
        "top_10_median_candidate_land_area_ha": (
            float(top10["candidate_land_area_ha"].median())
            if "candidate_land_area_ha" in top10
            else None
        ),
    }


def _final_thematic_validation(frame: pd.DataFrame) -> dict[str, Any]:
    balanced = _final_top_subset(frame, PRESET_SCORE_FIELDS["balanced"], 0.10)
    connectivity_fields = {
        "network_ge_75": lambda subset: subset["ecological_network_score"] >= 75,
        "protection_ge_75": lambda subset: subset["protected_area_reinforcement_score"] >= 75,
        "network_and_protection_ge_75": lambda subset: (
            (subset["ecological_network_score"] >= 75)
            & (subset["protected_area_reinforcement_score"] >= 75)
        ),
        "riparian_eq_0": lambda subset: subset["riparian_opportunity_score"] == 0,
        "availability_le_25": lambda subset: subset["restoration_land_availability_score"] <= 25,
    }
    riparian_fields = {
        "riparian_ge_75": lambda subset: subset["riparian_opportunity_score"] >= 75,
        "riparian_ge_90": lambda subset: subset["riparian_opportunity_score"] >= 90,
        "riparian_eq_0": lambda subset: subset["riparian_opportunity_score"] == 0,
        "network_le_25": lambda subset: subset["ecological_network_score"] <= 25,
        "protection_le_25": lambda subset: subset["protected_area_reinforcement_score"] <= 25,
        "availability_le_25": lambda subset: subset["restoration_land_availability_score"] <= 25,
    }

    def counts(subset: pd.DataFrame, conditions: dict[str, Any]) -> dict[str, int]:
        return {name: int(condition(subset).sum()) for name, condition in conditions.items()}

    connectivity = _final_top_subset(frame, PRESET_SCORE_FIELDS["connectivity_first"], 0.10)
    riparian = _final_top_subset(frame, PRESET_SCORE_FIELDS["riparian_restoration"], 0.10)
    return {
        "top_10_percent_candidate_count": int(len(balanced)),
        "connectivity_first": {
            "final_preset": counts(connectivity, connectivity_fields),
            "balanced_comparison": counts(balanced, connectivity_fields),
        },
        "riparian_restoration": {
            "final_preset": counts(riparian, riparian_fields),
            "balanced_comparison": counts(balanced, riparian_fields),
        },
    }


def _final_top_records(
    frame: pd.DataFrame, score_field: str, count: int = 20
) -> list[dict[str, Any]]:
    ordered = frame.sort_values([score_field, "hex_id"], ascending=[False, True], kind="mergesort")
    records = []
    for _, row in ordered.head(count).iterrows():
        records.append(
            {
                "hex_id": str(row["hex_id"]),
                "preset_score": float(row[score_field]),
                "component_scores": {
                    COMPONENT_LABELS[field]: float(row[field]) for field in SCORE_FIELDS
                },
                "boundary_edge_flag": bool(row[BOUNDARY_FLAG]),
            }
        )
    return records


def _final_top20_overlap(frame: pd.DataFrame) -> dict[str, Any]:
    ids = {
        preset_id: _final_top_n_ids(frame, score_field, 20)
        for preset_id, score_field in PRESET_SCORE_FIELDS.items()
    }
    result: dict[str, Any] = {}
    for first, second in (
        ("balanced", "connectivity_first"),
        ("balanced", "riparian_restoration"),
        ("connectivity_first", "riparian_restoration"),
    ):
        common = ids[first] & ids[second]
        result[f"{first}_vs_{second}"] = {
            "top_20_count_each": 20,
            "overlap_count": len(common),
            "overlap_percent": 100.0 * len(common) / 20.0,
        }
    return result


def _final_distinctive_candidates(frame: pd.DataFrame, preset_id: str) -> list[dict[str, Any]]:
    balanced_field = PRESET_SCORE_FIELDS["balanced"]
    preset_field = PRESET_SCORE_FIELDS[preset_id]
    balanced_percentile = _final_rank_percentile(frame, balanced_field)
    preset_percentile = _final_rank_percentile(frame, preset_field)
    gain = (preset_percentile - balanced_percentile).sort_values(ascending=False, kind="mergesort")
    balanced_rank = _final_ordinal_ranks(frame, balanced_field)
    preset_rank = _final_ordinal_ranks(frame, preset_field)
    records = []
    for index in gain.head(10).index:
        row = frame.loc[index]
        records.append(
            {
                "hex_id": str(row["hex_id"]),
                "balanced_rank": int(balanced_rank.loc[index]),
                "thematic_rank": int(preset_rank.loc[index]),
                "rank_change": int(balanced_rank.loc[index] - preset_rank.loc[index]),
                "percentile_rank_gain": float(gain.loc[index]),
                "component_scores": {
                    COMPONENT_LABELS[field]: float(row[field]) for field in SCORE_FIELDS
                },
            }
        )
    return records


def _final_winner_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    score_fields = list(PRESET_SCORE_FIELDS.values())
    scores = frame[score_fields]
    maximum = scores.max(axis=1)
    at_max = scores.eq(maximum, axis=0)
    tie_count = at_max.sum(axis=1)
    counts = {
        preset_id: {
            "count": int(at_max[score_field].sum()),
            "percent": float(100.0 * at_max[score_field].mean()),
        }
        for preset_id, score_field in PRESET_SCORE_FIELDS.items()
    }
    exclusive_counts = {
        preset_id: {
            "count": int((at_max[score_field] & tie_count.eq(1)).sum()),
            "percent": float(100.0 * (at_max[score_field] & tie_count.eq(1)).mean()),
        }
        for preset_id, score_field in PRESET_SCORE_FIELDS.items()
    }
    tie_patterns: dict[str, int] = {}
    for index in frame.index[tie_count > 1]:
        pattern = "+".join(
            preset_id
            for preset_id, score_field in PRESET_SCORE_FIELDS.items()
            if bool(at_max.loc[index, score_field])
        )
        tie_patterns[pattern] = tie_patterns.get(pattern, 0) + 1
    return {
        "winner_counts_including_tie_participation": counts,
        "exclusive_winner_counts": exclusive_counts,
        "candidate_count_with_any_tie": int((tie_count > 1).sum()),
        "tie_cardinality_counts": {
            str(cardinality): int((tie_count == cardinality).sum())
            for cardinality in sorted(tie_count.unique())
            if cardinality > 1
        },
        "tie_patterns": tie_patterns,
        "descriptive_only": True,
    }


def _final_consistent_candidates(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for fraction, label in (
        (0.10, "top_10_percent"),
        (0.05, "top_5_percent"),
        (0.01, "top_1_percent"),
    ):
        sets = [
            _final_top_ids(frame, score_field, fraction)
            for score_field in PRESET_SCORE_FIELDS.values()
        ]
        common = set.intersection(*sets)
        result[label] = {"count": len(common), "hex_ids": sorted(common)}
    sets = [
        _final_top_n_ids(frame, score_field, 100) for score_field in PRESET_SCORE_FIELDS.values()
    ]
    common = set.intersection(*sets)
    result["top_100"] = {"count": len(common), "hex_ids": sorted(common)}
    return result


def _final_spatial_boundary_sanity(
    frame: pd.DataFrame, candidate_units: pd.DataFrame
) -> dict[str, Any]:
    coordinate_fields = [field for field in ("grid_col", "grid_row") if field in candidate_units]
    result: dict[str, Any] = {
        "candidate_source_count": int(len(candidate_units)),
        "output_count": int(len(frame)),
        "full_region_representation": bool(set(frame["hex_id"]) == set(candidate_units["hex_id"])),
        "coordinate_fields": coordinate_fields,
        "presets": {},
    }
    if len(coordinate_fields) < 2:
        result["warning"] = "candidate source lacks grid_col/grid_row"
        return result
    working = frame.merge(
        candidate_units[["hex_id", *coordinate_fields]],
        on="hex_id",
        how="left",
        validate="one_to_one",
    )
    for preset_id, score_field in PRESET_SCORE_FIELDS.items():
        subset = _final_top_subset(working, score_field, 0.10)
        thirds: dict[str, Any] = {}
        for field in coordinate_fields:
            low, high = float(working[field].min()), float(working[field].max())
            edges = np.linspace(low, high, 4)
            assignments = pd.cut(
                working[field], bins=edges, labels=["low", "middle", "high"], include_lowest=True
            )
            thirds[field] = {
                label: {
                    "candidate_count": int((assignments == label).sum()),
                    "top_10_count": int(assignments.loc[subset.index].eq(label).sum()),
                }
                for label in ("low", "middle", "high")
            }
        result["presets"][preset_id] = {
            "top_10_candidate_count": int(len(subset)),
            "boundary_edge_count": int(subset[BOUNDARY_FLAG].sum()),
            "coordinate_thirds": thirds,
            "top_10_coordinate_thirds_with_zero_candidates": [
                f"{field}:{label}"
                for field, values in thirds.items()
                for label, counts in values.items()
                if counts["top_10_count"] == 0
            ],
        }
    return result


def _final_boundary_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    edge = frame[BOUNDARY_FLAG].astype(bool)
    result = {
        "boundary_edge_candidate_count": int(edge.sum()),
        "non_edge_candidate_count": int((~edge).sum()),
        "presets": {},
    }
    for preset_id, score_field in PRESET_SCORE_FIELDS.items():
        result["presets"][preset_id] = {
            label: {"boundary_count": int(edge.loc[frame["hex_id"].isin(ids)].sum())}
            for fraction, label in (
                (0.25, "top_25_percent"),
                (0.10, "top_10_percent"),
                (0.05, "top_5_percent"),
                (0.01, "top_1_percent"),
            )
            for ids in [_final_top_ids(frame, score_field, fraction)]
        }
    return result


def _final_score_bins(values: pd.Series) -> dict[str, int]:
    return {
        "0–10": int((values <= 10).sum()),
        ">10–25": int(((values > 10) & (values <= 25)).sum()),
        ">25–50": int(((values > 25) & (values <= 50)).sum()),
        ">50–75": int(((values > 50) & (values <= 75)).sum()),
        ">75–90": int(((values > 75) & (values <= 90)).sum()),
        ">90": int((values > 90).sum()),
    }


def _final_step22_reconciliation(frame: pd.DataFrame) -> dict[str, Any]:
    if not SENSITIVITY_OUTPUT_PATH.exists():
        raise PrioritizationModelError(
            f"Missing Step 22 sensitivity artifact: {SENSITIVITY_OUTPUT_PATH}"
        )
    reference = pd.read_csv(SENSITIVITY_OUTPUT_PATH)
    selected = {
        "balanced": "balanced_reference_score",
        "connectivity_first": "connectivity_medium_score",
        "riparian_restoration": "riparian_medium_score",
    }
    required = ["hex_id", BOUNDARY_FLAG, *selected.values()]
    _require_columns(reference, required, "Step 22 sensitivity artifact")
    reference["hex_id"] = normalize_ids(reference, "Step 22 sensitivity artifact")
    if len(reference) != len(frame) or set(reference["hex_id"]) != set(frame["hex_id"]):
        raise PrioritizationModelError(
            "Step 22 sensitivity IDs do not reconcile with final candidates"
        )
    reference[BOUNDARY_FLAG] = normalize_boundary_flags(
        reference[BOUNDARY_FLAG], "Step 22 sensitivity boundary flags"
    )
    merged = frame[["hex_id", BOUNDARY_FLAG, *PRESET_SCORE_FIELDS.values()]].merge(
        reference[required],
        on="hex_id",
        how="left",
        validate="one_to_one",
        suffixes=("", "_step22"),
    )
    result: dict[str, Any] = {
        "artifact_path": str(SENSITIVITY_OUTPUT_PATH),
        "provenance_path": str(SENSITIVITY_PROVENANCE_PATH),
        "tolerance": STRICT_TOLERANCE,
        "comparisons": {},
    }
    boundary_mismatches = int((merged[BOUNDARY_FLAG] != merged[f"{BOUNDARY_FLAG}_step22"]).sum())
    result["boundary_flag_mismatches"] = boundary_mismatches
    if boundary_mismatches:
        raise PrioritizationModelError("Final boundary flags do not match the Step 22 artifact")
    for preset_id, step22_field in selected.items():
        final_field = PRESET_SCORE_FIELDS[preset_id]
        difference = (merged[final_field] - merged[step22_field]).abs()
        mismatches = int((difference > STRICT_TOLERANCE).sum())
        maximum = float(difference.max()) if not difference.empty else 0.0
        result["comparisons"][preset_id] = {
            "final_score_field": final_field,
            "step22_score_field": step22_field,
            "mismatched_rows": mismatches,
            "maximum_absolute_difference": maximum,
            "reconciles_within_tolerance": mismatches == 0,
        }
        if mismatches:
            raise PrioritizationModelError(
                f"Final {final_field} does not reproduce Step 22 {step22_field}"
            )
    return result


def _read_step22_evidence() -> dict[str, Any]:
    if not SENSITIVITY_PROVENANCE_PATH.exists():
        raise PrioritizationModelError(
            f"Missing Step 22 sensitivity provenance: {SENSITIVITY_PROVENANCE_PATH}"
        )
    data = json.loads(SENSITIVITY_PROVENANCE_PATH.read_text(encoding="utf-8"))
    comparison = data.get("preset_intensity_comparison", {})
    return {
        "source_provenance_path": str(SENSITIVITY_PROVENANCE_PATH),
        "connectivity_first": comparison.get("connectivity_first", []),
        "riparian_restoration": comparison.get("riparian_restoration", []),
        "selected_vectors": {
            "connectivity_first": "connectivity_medium",
            "riparian_restoration": "riparian_medium",
        },
    }


def _final_audit(
    frame: pd.DataFrame,
    candidate_units: pd.DataFrame,
    reconciliation: dict[str, Any],
    step22_reconciliation: dict[str, Any],
    step22_evidence: dict[str, Any],
) -> dict[str, Any]:
    score_distributions = {
        preset_id: _distribution(frame[score_field])
        for preset_id, score_field in PRESET_SCORE_FIELDS.items()
    }
    score_bins = {
        preset_id: _final_score_bins(frame[score_field])
        for preset_id, score_field in PRESET_SCORE_FIELDS.items()
    }
    correlations: dict[str, Any] = {}
    for first, second in (
        ("balanced", "connectivity_first"),
        ("balanced", "riparian_restoration"),
        ("connectivity_first", "riparian_restoration"),
    ):
        correlations[f"{first}_vs_{second}"] = _correlation(
            frame[PRESET_SCORE_FIELDS[first]], frame[PRESET_SCORE_FIELDS[second]]
        )
    profiles = {
        preset_id: {
            "candidate_count": int(len(_final_top_subset(frame, score_field, 0.10))),
            "profile": _final_profile(_final_top_subset(frame, score_field, 0.10)),
        }
        for preset_id, score_field in PRESET_SCORE_FIELDS.items()
    }
    reconciliation = dict(reconciliation)
    reconciliation["component_row_counts"] = {
        label: int(values["component_rows"])
        for label, values in reconciliation["components"].items()
    }
    reconciliation["final_output_rows"] = int(len(frame))
    reconciliation["missing_final_ids"] = []
    reconciliation["extra_final_ids"] = []
    reconciliation["duplicate_final_ids"] = int(frame["hex_id"].duplicated().sum())
    return {
        "candidate_count": int(len(frame)),
        "reconciliation": reconciliation,
        "final_score_distributions": score_distributions,
        "final_score_bins": score_bins,
        "preset_correlations": correlations,
        "rank_movement_vs_balanced": {
            preset_id: _final_rank_movement(frame, score_field)
            for preset_id, score_field in PRESET_SCORE_FIELDS.items()
            if preset_id != "balanced"
        },
        "top_tail_overlap_vs_balanced": {
            preset_id: _final_overlap(frame, score_field)
            for preset_id, score_field in PRESET_SCORE_FIELDS.items()
            if preset_id != "balanced"
        },
        "top_10_percent_component_profiles": profiles,
        "preset_thematic_validation": _final_thematic_validation(frame),
        "compensability": {
            preset_id: _final_compensability(_final_top_subset(frame, score_field, 0.10))
            for preset_id, score_field in PRESET_SCORE_FIELDS.items()
        },
        "availability_tradeoff": {
            preset_id: _final_availability_tradeoff(frame, score_field)
            for preset_id, score_field in PRESET_SCORE_FIELDS.items()
        },
        "top_candidates": {
            preset_id: _final_top_records(frame, score_field)
            for preset_id, score_field in PRESET_SCORE_FIELDS.items()
        },
        "top_20_pairwise_overlap": _final_top20_overlap(frame),
        "distinctive_preset_candidates": {
            preset_id: _final_distinctive_candidates(frame, preset_id)
            for preset_id in PRESET_SCORE_FIELDS
            if preset_id != "balanced"
        },
        "preset_winner": _final_winner_diagnostics(frame),
        "consistent_top_candidates": _final_consistent_candidates(frame),
        "spatial_boundary_sanity": _final_spatial_boundary_sanity(frame, candidate_units),
        "boundary_diagnostics": _final_boundary_diagnostics(frame),
        "step22_selected_column_reconciliation": step22_reconciliation,
        "step22_sensitivity_evidence": step22_evidence,
    }


def write_preset_metadata(path: Path = PRESETS_PATH) -> None:
    """Write the application-facing preset metadata from canonical definitions."""

    _write_json(path, preset_metadata())


def build_balanced_baseline(
    output_path: Path = BALANCED_BASELINE_OUTPUT_PATH,
    provenance_path: Path = BALANCED_BASELINE_PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the durable equal-weight table and complete provenance audit."""

    started = time.perf_counter()
    joined, candidate_units, reconciliation = load_and_reconcile_inputs()
    scored = calculate_baseline(joined)
    scored = scored.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    formula = (
        "balanced_score = 0.20 * habitat_context_score + 0.20 * ecological_network_score + "
        "0.20 * riparian_opportunity_score + 0.20 * protected_area_reinforcement_score + "
        "0.20 * restoration_land_availability_score"
    )
    audit = _audit(scored, candidate_units, reconciliation)
    provenance: dict[str, Any] = {
        "model_name": "Equal-weight baseline",
        "model_status": "neutral baseline; not yet a finalized user-facing policy preset",
        "directional_contract": {
            "higher_score_means_stronger_restoration_priority_contribution": True,
            "all_components_higher_is_better": True,
            "restoration_land_availability_interpretation": "more candidate hectares = higher score",
            "components_are_not_inverted": True,
        },
        "input_components": {
            label: {
                "artifact_path": str(config["path"]),
                "score_field": config["score_field"],
                "definition": config["definition"],
            }
            for label, config in COMPONENTS.items()
        },
        "weights": {field: float(value) for field, value in WEIGHTS.items()},
        "formula": formula,
        "weight_sum": math.fsum(WEIGHTS.values()),
        "normalization": "No secondary normalization, re-ranking, clipping, standardization, percentile transform, or z-score is applied to the raw weighted mean.",
        "candidate_count": int(len(scored)),
        "reconciliation": reconciliation,
        "audit": audit,
        "interpretive_caveats": [
            "This is an equal-component-weight baseline, not a finalized policy preference.",
            "All five components remain independently inspectable.",
            "Weighted averaging is compensatory: strong performance on one component can offset weak performance on another.",
            "Four of five components are ecological/context dimensions and one represents land availability.",
            "Equal per-component weighting therefore implicitly allocates 80% of nominal total weight to ecological/context dimensions and 20% to land availability.",
            "That allocation is transparent and intentional for the baseline, but will be reviewed before final preset definitions.",
            "Component scores are authoritative inputs; this integration does not recalculate them from raw indicators.",
            "The model is a screening baseline, not a probability, recommendation category, or implementation-feasibility estimate.",
        ],
        "preset_readiness_summary": _preset_readiness(audit),
        "output": {
            "path": str(output_path),
            "columns": list(OUTPUT_COLUMNS),
            "rows": int(len(scored)),
            "geometry_included": False,
            "deterministic_order": "ascending hex_id",
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environmental_datasets_downloaded": False,
        "dependency_changes": [],
        "warnings": [
            "Boundary flags were reconciled from component artifact copies; they are diagnostic and do not alter scores.",
            "Correlation diagnostics describe association and do not establish causality.",
        ],
        "runtime_seconds": time.perf_counter() - started,
    }
    output = scored[list(OUTPUT_COLUMNS)].copy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False, float_format="%.10f")
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["provenance_path"] = str(provenance_path)
    _write_json(provenance_path, provenance)
    for _ in range(3):
        actual_size = int(provenance_path.stat().st_size)
        if provenance.get("provenance_size_bytes") == actual_size:
            break
        provenance["provenance_size_bytes"] = actual_size
        _write_json(provenance_path, provenance)
    return output, provenance


def build_prioritization_model(
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
    presets_path: Path = PRESETS_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the canonical three-preset MVP table and provenance audit."""

    started = time.perf_counter()
    for config in PRESET_DEFINITIONS.values():
        validate_weights(config["weights"])
    joined, candidate_units, reconciliation = load_and_reconcile_inputs()
    component_provenance = {}
    for label, config in COMPONENTS.items():
        provenance_path_for_component = Path(config["path"]).with_suffix(".provenance.json")
        if not provenance_path_for_component.exists():
            raise PrioritizationModelError(
                f"Missing finalized component provenance: {provenance_path_for_component}"
            )
        component_provenance[label] = str(provenance_path_for_component)

    scored = calculate_prioritization_scores(joined)
    scored = scored.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    step22_evidence = _read_step22_evidence()
    step22_reconciliation = _final_step22_reconciliation(scored)
    audit = _final_audit(
        scored, candidate_units, reconciliation, step22_reconciliation, step22_evidence
    )
    formula_by_preset = {
        preset_id: " + ".join(
            f"{weight:.2f} * {field}" for field, weight in config["weights"].items()
        )
        for preset_id, config in PRESET_DEFINITIONS.items()
    }
    rationale = {
        "balanced": {
            "selection": "Retain the equal-weight baseline unchanged.",
            "reason": "Step 21–22 showed numerically stable behavior, no catastrophic single-component dominance, transparent interpretation, and a useful ecological/availability tradeoff; there was no serious reason to replace 20/20/20/20/20.",
            "interpretation": "equal component importance; not statistically optimized",
        },
        "connectivity_first": {
            "selection": "Select the former Connectivity Medium vector.",
            "reason": "Medium was clearly differentiated while preserving acceptable availability and contained severe-weakness costs. Strong added only modest thematic gain, reduced availability, increased severe-weakness cases, and increased top-100 churn; Mild remained too close to Balanced.",
            "interpretation": "emphasize ecological-network configuration and protected-network reinforcement",
        },
        "riparian_restoration": {
            "selection": "Select the former Riparian Medium vector.",
            "reason": "Medium produced a clearer Riparian shift, retained elimination of zero-Riparian candidates from the top 10%, and kept severe weakness close to Mild. Strong added only modest Riparian gain while degrading broader Habitat/Protection balance and increasing churn; Mild differentiated less clearly.",
            "interpretation": "emphasize focal wetland/inland-water restoration opportunity",
        },
        "policy_status": "Preset weights are stakeholder/scenario emphasis choices, not learned coefficients, calibrated ecological truth, probabilities, or optimization results. All five components retain positive weight and weighted averaging remains compensatory.",
    }
    provenance: dict[str, Any] = {
        "project_name": "Landscape Restoration Prioritizer",
        "model_name": "Canonical three-preset MVP prioritization model",
        "step": "Step 23 final MVP preset selection and canonical model artifact",
        "model_status": "FINALIZED FOR MVP",
        "input_components": {
            label: {
                "artifact_path": str(config["path"]),
                "provenance_path": component_provenance[label],
                "score_field": config["score_field"],
                "definition": config["definition"],
                "component_recalculated": False,
            }
            for label, config in COMPONENTS.items()
        },
        "presets": {
            preset_id: {
                "id": preset_id,
                "name": config["name"],
                "purpose": config["purpose"],
                "weights": {field: float(value) for field, value in config["weights"].items()},
                "weight_sum": math.fsum(config["weights"].values()),
                "all_weights_positive": all(value > 0 for value in config["weights"].values()),
                "status": "FINALIZED FOR MVP",
            }
            for preset_id, config in PRESET_DEFINITIONS.items()
        },
        "final_preset_status_table": [
            {
                "id": preset_id,
                "name": config["name"],
                "weights": {field: float(value) for field, value in config["weights"].items()},
                "purpose": config["purpose"],
                "status": "FINALIZED FOR MVP",
            }
            for preset_id, config in PRESET_DEFINITIONS.items()
        ],
        "formula": "preset_score = sum(component_score * preset_component_weight); no further transformation",
        "formula_by_preset": formula_by_preset,
        "normalization": "No percentile rank, min-max scaling, z-score, clipping, max-to-100 rescaling, or other transformation follows the weighted mean.",
        "weight_selection_rationale": rationale,
        "step22_reference": {
            "sensitivity_output_path": str(SENSITIVITY_OUTPUT_PATH),
            "sensitivity_provenance_path": str(SENSITIVITY_PROVENANCE_PATH),
            "balanced_uses_equal_weights": True,
            "connectivity_first_uses_former_connectivity_medium_vector": True,
            "riparian_restoration_uses_former_riparian_medium_vector": True,
            "mild_and_strong_variants_are_sensitivity_experiments_only": True,
            "weights_statistically_optimized": False,
            "evidence": step22_evidence,
        },
        "audit": audit,
        "candidate_count": int(len(scored)),
        "reconciliation": audit["reconciliation"],
        "caveats": [
            "No ground-truth restoration-outcome dataset was used; weights were not statistically optimized.",
            "Preset weights express stakeholder/scenario emphasis choices, not calibrated ecological truth or probabilities.",
            "All five components retain positive weight in every preset.",
            "Composite weighted averaging is compensatory: strong performance on one component can offset weak performance on another; no hard gates are applied.",
            "Component scores are finalized authoritative inputs; raw indicators are not recalculated here.",
            "The output is decision-support screening, not a recommendation category, implementation decision, or restoration probability.",
            "Boundary flags are reconciled diagnostics and do not alter scores.",
            "Percentile ranks are temporary diagnostics only and are not persisted as model scores.",
            "The winner diagnostic is descriptive only; no permanent candidate category is created.",
            "No environmental datasets were downloaded by this command.",
        ],
        "output": {
            "path": str(output_path),
            "columns": list(FINAL_OUTPUT_COLUMNS),
            "rows": int(len(scored)),
            "geometry_included": False,
            "raw_indicator_fields_included": False,
            "experimental_sensitivity_fields_included": False,
            "deterministic_order": "ascending hex_id",
        },
        "presets_metadata_output": {
            "path": str(presets_path),
            "columns": ["name", "weights"],
            "experimental_presets_included": False,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environmental_datasets_downloaded": False,
        "dependency_changes": [],
        "warnings": [
            "Observed score maxima are reported without rescaling.",
            "Correlation, rank, tail, and spatial diagnostics are descriptive and do not establish causal effect or ecological optimality.",
        ],
        "runtime_seconds": time.perf_counter() - started,
    }
    output = scored[list(FINAL_OUTPUT_COLUMNS)].copy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False, float_format="%.10f")
    write_preset_metadata(presets_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["presets_metadata_output"]["size_bytes"] = int(presets_path.stat().st_size)
    provenance["provenance_path"] = str(provenance_path)
    _write_json(provenance_path, provenance)
    for _ in range(3):
        actual_size = int(provenance_path.stat().st_size)
        if provenance.get("provenance_size_bytes") == actual_size:
            break
        provenance["provenance_size_bytes"] = actual_size
        _write_json(provenance_path, provenance)
    return output, provenance


def main() -> None:
    output, provenance = build_prioritization_model()
    distributions = provenance["audit"]["final_score_distributions"]
    print(
        f"Final prioritization model: {len(output):,} candidates written to {OUTPUT_PATH} "
        f"({OUTPUT_PATH.stat().st_size:,} bytes)"
    )
    print(
        "Preset weights: "
        + "; ".join(
            f"{preset_id}=" + "/".join(f"{value:.2f}" for value in config["weights"].values())
            for preset_id, config in PRESET_DEFINITIONS.items()
        )
    )
    print(
        "Score ranges: "
        + "; ".join(
            f"{preset_id}={values['min']:.6f}–{values['max']:.6f}"
            for preset_id, values in distributions.items()
        )
        + f"; provenance {PROVENANCE_PATH} ({PROVENANCE_PATH.stat().st_size:,} bytes)"
    )


if __name__ == "__main__":
    main()
