"""Run the fixed Step 22 prioritization-preset sensitivity experiment.

The module evaluates exactly six policy/scenario weight vectors against the
approved equal-weight reference.  It does not search weights, recalculate any
component, or write a final user-facing preset.
"""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .prioritization_model import (
    BALANCED_SCORE,
    BALANCED_WEIGHTS,
    BOUNDARY_FLAG,
    COMPONENT_LABELS,
    SCORE_FIELDS,
    STRICT_TOLERANCE,
    PrioritizationModelError,
    load_and_reconcile_inputs,
    validate_weights,
    weighted_mean,
)

BALANCED_REFERENCE_PATH = Path("data/processed/prioritization/balanced_baseline.csv")
OUTPUT_PATH = Path("data/processed/prioritization/preset_sensitivity.csv")
PROVENANCE_PATH = Path("data/processed/prioritization/preset_sensitivity.provenance.json")

SCHEMES = OrderedDict(
    [
        (
            "connectivity_mild",
            {
                "family": "Connectivity First",
                "intensity": "Mild",
                "weights": OrderedDict(zip(SCORE_FIELDS, [0.20, 0.25, 0.15, 0.25, 0.15])),
            },
        ),
        (
            "connectivity_medium",
            {
                "family": "Connectivity First",
                "intensity": "Medium",
                "weights": OrderedDict(zip(SCORE_FIELDS, [0.20, 0.30, 0.10, 0.25, 0.15])),
            },
        ),
        (
            "connectivity_strong",
            {
                "family": "Connectivity First",
                "intensity": "Strong",
                "weights": OrderedDict(zip(SCORE_FIELDS, [0.20, 0.35, 0.10, 0.25, 0.10])),
            },
        ),
        (
            "riparian_mild",
            {
                "family": "Riparian Restoration",
                "intensity": "Mild",
                "weights": OrderedDict(zip(SCORE_FIELDS, [0.20, 0.15, 0.30, 0.15, 0.20])),
            },
        ),
        (
            "riparian_medium",
            {
                "family": "Riparian Restoration",
                "intensity": "Medium",
                "weights": OrderedDict(zip(SCORE_FIELDS, [0.20, 0.10, 0.35, 0.15, 0.20])),
            },
        ),
        (
            "riparian_strong",
            {
                "family": "Riparian Restoration",
                "intensity": "Strong",
                "weights": OrderedDict(zip(SCORE_FIELDS, [0.15, 0.10, 0.40, 0.15, 0.20])),
            },
        ),
    ]
)
SCHEME_SCORE_COLUMNS = OrderedDict(
    [(name, f"{name}_score") for name in ["balanced_reference", *SCHEMES]]
)
OUTPUT_COLUMNS = ("hex_id", *SCHEME_SCORE_COLUMNS.values(), BOUNDARY_FLAG)
TAILS = OrderedDict(
    [
        (0.25, "top_25_percent"),
        (0.10, "top_10_percent"),
        (0.05, "top_5_percent"),
        (0.01, "top_1_percent"),
    ]
)
PROFILE_FIELDS = SCORE_FIELDS


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise PrioritizationModelError(f"{label} is missing required fields: {missing}")


def _distribution(values: pd.Series | np.ndarray) -> dict[str, Any]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
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
                    "std",
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
        "std": float(numeric.std(ddof=0)),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "p99": float(quantiles.loc[0.99]),
        "max": float(numeric.max()),
    }


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


def _top_ids(frame: pd.DataFrame, score_field: str, fraction: float) -> set[str]:
    count = max(1, math.ceil(len(frame) * fraction))
    ordered = frame.sort_values([score_field, "hex_id"], ascending=[False, True], kind="mergesort")
    return set(ordered.head(count)["hex_id"])


def _rank_percentile(frame: pd.DataFrame, score_field: str) -> pd.Series:
    if len(frame) <= 1:
        return pd.Series(0.0, index=frame.index)
    return (frame[score_field].rank(method="average") - 1.0) / (len(frame) - 1.0) * 100.0


def _ordinal_ranks(frame: pd.DataFrame, score_field: str) -> pd.Series:
    ordered = frame.sort_values([score_field, "hex_id"], ascending=[False, True], kind="mergesort")
    ranks = pd.Series(range(1, len(frame) + 1), index=ordered.index, dtype=int)
    return ranks.reindex(frame.index)


def _profile(subset: pd.DataFrame, fields: Iterable[str] = PROFILE_FIELDS) -> dict[str, Any]:
    if subset.empty:
        return {
            COMPONENT_LABELS[field]: {"median": None, "p10": None, "p90": None} for field in fields
        }
    return {
        COMPONENT_LABELS[field]: {
            "field": field,
            "median": float(subset[field].median()),
            "p10": float(subset[field].quantile(0.10)),
            "p90": float(subset[field].quantile(0.90)),
        }
        for field in fields
    }


def _availability_profile(subset: pd.DataFrame) -> dict[str, Any]:
    availability = subset["restoration_land_availability_score"]
    result: dict[str, Any] = {
        "candidate_count": int(len(subset)),
        "median_availability_score": float(availability.median()),
        "p10_availability_score": float(availability.quantile(0.10)),
        "candidates_availability_le_25": int((availability <= 25).sum()),
        "candidates_availability_le_10": int((availability <= 10).sum()),
    }
    if "candidate_land_area_ha" in subset:
        result["median_candidate_land_area_ha"] = float(subset["candidate_land_area_ha"].median())
    else:
        result["median_candidate_land_area_ha"] = None
    return result


def _severe_weakness(subset: pd.DataFrame) -> dict[str, Any]:
    values = subset[list(SCORE_FIELDS)]
    minimum = values.min(axis=1)
    return {
        "candidate_count": int(len(subset)),
        "any_component_le_25": int((values <= 25).any(axis=1).sum()),
        "any_component_eq_0": int((values == 0).any(axis=1).sum()),
        "two_or_more_components_le_25": int((values <= 25).sum(axis=1).ge(2).sum()),
        "minimum_component_median": float(minimum.median()),
        "minimum_component_p10": float(minimum.quantile(0.10)),
    }


def _family_weakness(subset: pd.DataFrame, family: str) -> dict[str, int]:
    if family == "Connectivity First":
        fields = {
            "riparian_le_25": ("riparian_opportunity_score", 25),
            "riparian_eq_0": ("riparian_opportunity_score", 0),
            "availability_le_25": ("restoration_land_availability_score", 25),
            "habitat_le_25": ("habitat_context_score", 25),
        }
    else:
        fields = {
            "network_le_25": ("ecological_network_score", 25),
            "protection_le_25": ("protected_area_reinforcement_score", 25),
            "availability_le_25": ("restoration_land_availability_score", 25),
            "habitat_le_25": ("habitat_context_score", 25),
        }
    result = {}
    for name, (field, threshold) in fields.items():
        result[name] = int((subset[field] <= threshold).sum())
        if name.endswith("_eq_0"):
            result[name] = int((subset[field] == threshold).sum())
    return result


def _top_tail_mask(frame: pd.DataFrame, score_field: str, fraction: float) -> pd.Series:
    return frame["hex_id"].isin(_top_ids(frame, score_field, fraction))


def _overlap(frame: pd.DataFrame, thematic_field: str) -> dict[str, Any]:
    result = {}
    balanced_ids = {
        fraction: _top_ids(frame, "balanced_reference_score", fraction) for fraction in TAILS
    }
    thematic_ids = {fraction: _top_ids(frame, thematic_field, fraction) for fraction in TAILS}
    for fraction, label in TAILS.items():
        common = len(balanced_ids[fraction] & thematic_ids[fraction])
        tail_size = len(balanced_ids[fraction])
        result[label] = {
            "tail_size": tail_size,
            "common_candidate_count": common,
            "overlap_percent_relative_to_tail": 100.0 * common / tail_size,
        }
    return result


def _movement(frame: pd.DataFrame, thematic_field: str) -> dict[str, Any]:
    balanced_percentile = _rank_percentile(frame, "balanced_reference_score")
    thematic_percentile = _rank_percentile(frame, thematic_field)
    absolute = (thematic_percentile - balanced_percentile).abs()
    return {
        "median_absolute_percentile_rank_movement": float(absolute.median()),
        "p90_absolute_percentile_rank_movement": float(absolute.quantile(0.90)),
        "p95_absolute_percentile_rank_movement": float(absolute.quantile(0.95)),
        "candidates_moving_at_least_10_percentile_points": int((absolute >= 10).sum()),
        "candidates_moving_at_least_25_percentile_points": int((absolute >= 25).sum()),
        "candidates_moving_at_least_40_percentile_points": int((absolute >= 40).sum()),
        "maximum_absolute_percentile_rank_movement": float(absolute.max()),
    }


def _top10(frame: pd.DataFrame, score_field: str) -> pd.DataFrame:
    return frame[frame.hex_id.isin(_top_ids(frame, score_field, 0.10))].copy()


def _thematic_effect(
    frame: pd.DataFrame, fields: list[str], thresholds: dict[str, tuple[str, float]]
) -> dict[str, Any]:
    balanced = _top10(frame, "balanced_reference_score")
    balanced_profile = _profile(balanced, fields)
    result: dict[str, Any] = {
        "balanced": {
            "candidate_count": int(len(balanced)),
            "profile": balanced_profile,
            "threshold_counts": {
                name: int((balanced[field] >= threshold).sum())
                for name, (field, threshold) in thresholds.items()
                if field != "__both__"
            },
        }
    }
    for name, config in SCHEMES.items():
        if config["family"] != (
            "Connectivity First"
            if "network" in thresholds[next(iter(thresholds))][0]
            else "Riparian Restoration"
        ):
            continue
        subset = _top10(frame, SCHEME_SCORE_COLUMNS[name])
        profile = _profile(subset, fields)
        balanced_values = result["balanced"]["profile"]
        changes = {
            label: None
            if profile[label]["median"] is None
            else profile[label]["median"] - balanced_values[label]["median"]
            for label in profile
        }
        result[name] = {
            "candidate_count": int(len(subset)),
            "profile": profile,
            "change_vs_balanced_median": changes,
            "threshold_counts": {
                threshold_name: int((subset[field] >= threshold).sum())
                for threshold_name, (field, threshold) in thresholds.items()
                if field != "__both__"
            },
        }
    return result


def _availability_costs(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        scheme: _availability_profile(_top10(frame, field))
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
    }


def _weakness_audits(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        scheme: {
            "severe_weakness": _severe_weakness(_top10(frame, field)),
            "family_specific_weakness": (
                _family_weakness(_top10(frame, field), SCHEMES[scheme]["family"])
                if scheme in SCHEMES
                else {}
            ),
        }
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
    }


def _tradeoff(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        scheme: {
            "preset_score_vs_availability_score": _correlation(
                frame[field], frame["restoration_land_availability_score"]
            ),
            "top_tail_median_availability": {
                label: float(_top10(frame, field)["restoration_land_availability_score"].median())
                if fraction == 0.10
                else float(
                    frame.loc[
                        frame.hex_id.isin(_top_ids(frame, field, fraction)),
                        "restoration_land_availability_score",
                    ].median()
                )
                for fraction, label in TAILS.items()
            },
        }
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
    }


def _leave_theme_effect(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        scheme: {
            COMPONENT_LABELS[field]: _correlation(frame[score_field], frame[field])
            for field in SCORE_FIELDS
        }
        for scheme, score_field in SCHEME_SCORE_COLUMNS.items()
        if scheme != "balanced_reference"
    }


def _contribution_sd(frame: pd.DataFrame) -> dict[str, Any]:
    result = {}
    for scheme, config in [("balanced_reference", {"weights": BALANCED_WEIGHTS}), *SCHEMES.items()]:
        weights = config["weights"]
        result[scheme] = {
            COMPONENT_LABELS[field]: {
                "field": field,
                "weight": float(weights[field]),
                "sd_weight_times_component_score": float(
                    (weights[field] * frame[field]).std(ddof=0)
                ),
            }
            for field in SCORE_FIELDS
        }
    return result


def _top100_churn(frame: pd.DataFrame) -> dict[str, Any]:
    balanced = _top_ids(frame, "balanced_reference_score", 100 / len(frame))
    result = {}
    for scheme, score_field in SCHEME_SCORE_COLUMNS.items():
        if scheme == "balanced_reference":
            continue
        current = _top_ids(frame, score_field, 100 / len(frame))
        entrants = current - balanced
        leavers = balanced - current
        result[scheme] = {
            "balanced_top_100_count": len(balanced),
            "thematic_top_100_count": len(current),
            "overlap_count": len(current & balanced),
            "newly_entering_count": len(entrants),
            "leaving_count": len(leavers),
            "entrant_profile": _profile(frame[frame.hex_id.isin(entrants)]),
            "leaver_profile": _profile(frame[frame.hex_id.isin(leavers)]),
        }
    return result


def _extreme_movers(frame: pd.DataFrame) -> dict[str, Any]:
    result = {}
    balanced_percentile = _rank_percentile(frame, "balanced_reference_score")
    for scheme, score_field in SCHEME_SCORE_COLUMNS.items():
        if scheme == "balanced_reference":
            continue
        thematic_percentile = _rank_percentile(frame, score_field)
        movement = thematic_percentile - balanced_percentile
        balanced_rank = _ordinal_ranks(frame, "balanced_reference_score")
        thematic_rank = _ordinal_ranks(frame, score_field)
        entries = []
        for direction, subset in (
            ("upward", movement.nlargest(10)),
            ("downward", movement.nsmallest(10)),
        ):
            examples = []
            for index in subset.index:
                row = frame.loc[index]
                examples.append(
                    {
                        "hex_id": row.hex_id,
                        "balanced_score": float(row.balanced_reference_score),
                        "thematic_score": float(row[score_field]),
                        "balanced_rank": int(balanced_rank.loc[index]),
                        "thematic_rank": int(thematic_rank.loc[index]),
                        "rank_change_improvement_positive": int(
                            balanced_rank.loc[index] - thematic_rank.loc[index]
                        ),
                        "percentile_rank_change": float(movement.loc[index]),
                        "component_scores": {
                            COMPONENT_LABELS[field]: float(row[field]) for field in SCORE_FIELDS
                        },
                    }
                )
            entries.append({"direction": direction, "candidates": examples})
        result[scheme] = entries
    return result


def _spatial_sanity(frame: pd.DataFrame, candidate_units: pd.DataFrame) -> dict[str, Any]:
    fields = [field for field in ("grid_col", "grid_row", "sea_pixels") if field in candidate_units]
    if "grid_col" not in fields or "grid_row" not in fields:
        return {"available": False, "reason": "candidate source lacks grid_col/grid_row"}
    coordinates = candidate_units[["hex_id", *fields]].drop_duplicates("hex_id")
    working = frame.merge(coordinates, on="hex_id", how="left", validate="one_to_one")
    result: dict[str, Any] = {"available": True, "coordinate_fields": fields}
    for scheme, score_field in SCHEME_SCORE_COLUMNS.items():
        subset = working[working.hex_id.isin(_top_ids(working, score_field, 0.10))]
        bins: dict[str, Any] = {}
        for field in ("grid_col", "grid_row"):
            edges = np.linspace(float(working[field].min()), float(working[field].max()), 4)
            labels = ["low", "middle", "high"]
            assignments = pd.cut(working[field], bins=edges, labels=labels, include_lowest=True)
            subset_assignments = assignments.loc[subset.index]
            bins[field] = {
                label: {
                    "candidate_count": int((assignments == label).sum()),
                    "top_10_count": int((subset_assignments == label).sum()),
                }
                for label in labels
            }
        result[scheme] = {
            "top_10_candidate_count": int(len(subset)),
            "coordinate_thirds": bins,
            "boundary_edge_count": int(subset[BOUNDARY_FLAG].sum()),
            "sea_containing_count": int((subset["sea_pixels"] > 0).sum())
            if "sea_pixels" in subset
            else None,
            "top_10_coordinate_thirds_with_zero_candidates": [
                f"{field}:{label}"
                for field, values in bins.items()
                for label, counts in values.items()
                if counts["top_10_count"] == 0
            ],
        }
    return result


def _intensity_comparison(
    frame: pd.DataFrame, family: str, intended_field: str
) -> list[dict[str, Any]]:
    balanced = _top10(frame, "balanced_reference_score")
    rows = []
    for scheme, config in SCHEMES.items():
        if config["family"] != family:
            continue
        subset = _top10(frame, SCHEME_SCORE_COLUMNS[scheme])
        rows.append(
            {
                "scheme": scheme,
                "intensity": config["intensity"],
                "spearman_vs_balanced": _correlation(
                    frame[SCHEME_SCORE_COLUMNS[scheme]], frame["balanced_reference_score"]
                )["spearman"],
                "median_absolute_rank_movement": _movement(frame, SCHEME_SCORE_COLUMNS[scheme])[
                    "median_absolute_percentile_rank_movement"
                ],
                "top_10_overlap_percent": _overlap(frame, SCHEME_SCORE_COLUMNS[scheme])[
                    "top_10_percent"
                ]["overlap_percent_relative_to_tail"],
                "intended_theme_top_10_median": float(subset[intended_field].median()),
                "balanced_intended_theme_top_10_median": float(balanced[intended_field].median()),
                "availability_top_10_median": float(
                    subset["restoration_land_availability_score"].median()
                ),
                "severe_weakness_count": _severe_weakness(subset)["any_component_le_25"],
            }
        )
    return rows


def calculate_sensitivity_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate the reference and exactly six fixed weighted means."""

    _require_columns(frame, ("hex_id", *SCORE_FIELDS), "Sensitivity input")
    numeric = frame[list(SCORE_FIELDS)].apply(pd.to_numeric, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    if numeric.isna().any().any() or not np.isfinite(values).all():
        raise PrioritizationModelError("Sensitivity input contains non-finite component scores")
    if np.any((values < 0) | (values > 100)):
        raise PrioritizationModelError("Sensitivity component scores must lie in [0, 100]")
    result = frame.copy()
    result["balanced_reference_score"] = weighted_mean(result, BALANCED_WEIGHTS)
    for scheme, config in SCHEMES.items():
        result[SCHEME_SCORE_COLUMNS[scheme]] = weighted_mean(result, config["weights"])
    return result


def _validate_scheme_contract() -> None:
    validate_weights(BALANCED_WEIGHTS)
    for config in SCHEMES.values():
        validate_weights(config["weights"])


def _read_balanced_reference(frame: pd.DataFrame) -> pd.DataFrame:
    if not BALANCED_REFERENCE_PATH.exists():
        raise PrioritizationModelError(
            f"Missing approved balanced reference: {BALANCED_REFERENCE_PATH}"
        )
    reference = pd.read_csv(BALANCED_REFERENCE_PATH)
    _require_columns(reference, ("hex_id", BALANCED_SCORE, BOUNDARY_FLAG), "Balanced reference")
    if len(reference) != len(frame) or reference.hex_id.duplicated().any():
        raise PrioritizationModelError("Balanced reference does not contain one row per candidate")
    if set(reference.hex_id.astype(str)) != set(frame.hex_id.astype(str)):
        raise PrioritizationModelError(
            "Balanced reference IDs do not reconcile with finalized components"
        )
    reference = reference.copy()
    reference["hex_id"] = reference.hex_id.astype(str)
    reference[BALANCED_SCORE] = pd.to_numeric(reference[BALANCED_SCORE], errors="coerce")
    if reference[BALANCED_SCORE].isna().any() or not np.isfinite(reference[BALANCED_SCORE]).all():
        raise PrioritizationModelError("Balanced reference contains non-finite scores")
    return reference[["hex_id", BALANCED_SCORE, BOUNDARY_FLAG]]


def build_sensitivity(
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run the real-data Step 22 experiment and write both ignored artifacts."""

    started = time.perf_counter()
    _validate_scheme_contract()
    joined, candidate_units, component_reconciliation = load_and_reconcile_inputs()
    joined["hex_id"] = joined.hex_id.astype(str)
    reference = _read_balanced_reference(joined)
    scored = calculate_sensitivity_scores(joined)
    scored = scored.merge(
        reference, on="hex_id", how="left", validate="one_to_one", suffixes=("", "_artifact")
    )
    if not np.allclose(
        scored["balanced_reference_score"],
        scored[BALANCED_SCORE],
        rtol=0.0,
        atol=STRICT_TOLERANCE,
    ):
        raise PrioritizationModelError(
            "Recalculated equal-weight reference does not match approved baseline"
        )
    if not np.array_equal(
        scored[BOUNDARY_FLAG].astype(bool).to_numpy(),
        scored[f"{BOUNDARY_FLAG}_artifact"].astype(bool).to_numpy(),
    ):
        raise PrioritizationModelError("Boundary flags do not reconcile with approved baseline")
    scored = scored.drop(columns=[BALANCED_SCORE, f"{BOUNDARY_FLAG}_artifact"])
    scored = scored.sort_values("hex_id", kind="mergesort").reset_index(drop=True)

    score_distributions = {
        scheme: _distribution(scored[field]) for scheme, field in SCHEME_SCORE_COLUMNS.items()
    }
    rank_correlations = {
        scheme: _correlation(scored[field], scored["balanced_reference_score"])
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
        if scheme != "balanced_reference"
    }
    rank_movement = {
        scheme: _movement(scored, field)
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
        if scheme != "balanced_reference"
    }
    top_tail_overlap = {
        scheme: _overlap(scored, field)
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
        if scheme != "balanced_reference"
    }
    top10_profiles = {
        scheme: {
            "candidate_count": int(len(_top10(scored, field))),
            "profile": _profile(_top10(scored, field)),
        }
        for scheme, field in SCHEME_SCORE_COLUMNS.items()
    }
    connectivity_effect = _thematic_effect(
        scored,
        [
            "ecological_network_score",
            "protected_area_reinforcement_score",
            "habitat_context_score",
            "riparian_opportunity_score",
            "restoration_land_availability_score",
        ],
        {
            "network_ge_75": ("ecological_network_score", 75),
            "protection_ge_75": ("protected_area_reinforcement_score", 75),
            "network_and_protection_ge_75": ("__both__", 75),
        },
    )
    # The combined threshold is kept explicit because it is not a one-column threshold.
    for scheme, field in [
        ("balanced", "balanced_reference_score"),
        *[
            (name, SCHEME_SCORE_COLUMNS[name])
            for name in SCHEMES
            if SCHEMES[name]["family"] == "Connectivity First"
        ],
    ]:
        subset = _top10(scored, field)
        connectivity_effect[scheme]["threshold_counts"]["network_and_protection_ge_75"] = int(
            (
                (subset.ecological_network_score >= 75)
                & (subset.protected_area_reinforcement_score >= 75)
            ).sum()
        )
    riparian_effect = _thematic_effect(
        scored,
        [
            "riparian_opportunity_score",
            "habitat_context_score",
            "restoration_land_availability_score",
            "ecological_network_score",
            "protected_area_reinforcement_score",
        ],
        {
            "riparian_ge_75": ("riparian_opportunity_score", 75),
            "riparian_ge_90": ("riparian_opportunity_score", 90),
            "riparian_eq_0": ("riparian_opportunity_score", 0),
        },
    )
    # The generic thematic helper uses >=; replace the zero threshold with exact equality.
    for scheme, field in [
        ("balanced", "balanced_reference_score"),
        *[
            (name, SCHEME_SCORE_COLUMNS[name])
            for name in SCHEMES
            if SCHEMES[name]["family"] == "Riparian Restoration"
        ],
    ]:
        riparian_effect[scheme]["threshold_counts"]["riparian_eq_0"] = int(
            (_top10(scored, field).riparian_opportunity_score == 0).sum()
        )
    weaknesses = _weakness_audits(scored)
    weaknesses["balanced_reference"]["family_specific_weakness"] = {
        "connectivity": _family_weakness(
            _top10(scored, "balanced_reference_score"), "Connectivity First"
        ),
        "riparian": _family_weakness(
            _top10(scored, "balanced_reference_score"), "Riparian Restoration"
        ),
    }
    provenance: dict[str, Any] = {
        "step": "Step 22 controlled weighting/preset sensitivity study",
        "model_status": "analytical sensitivity study; no thematic preset finalized",
        "reference_model": {
            "name": "balanced_reference",
            "artifact_path": str(BALANCED_REFERENCE_PATH),
            "provenance_path": "data/processed/prioritization/balanced_baseline.provenance.json",
            "weights": dict(BALANCED_WEIGHTS),
            "score_field": "balanced_reference_score",
        },
        "candidate_schemes": {
            name: {
                "family": config["family"],
                "intensity": config["intensity"],
                "weights": dict(config["weights"]),
                "weight_sum": math.fsum(config["weights"].values()),
                "all_components_nonzero": all(value > 0 for value in config["weights"].values()),
            }
            for name, config in SCHEMES.items()
        },
        "formula": "preset_score = sum(component_score * component_weight); no score transformation follows the weighted mean",
        "validation": {
            "weights_finite_nonnegative_sum_to_one": True,
            "all_five_components_nonzero_for_every_scheme": True,
            "higher_component_score_remains_better": True,
            "components_recalculated": False,
            "component_scores_normalized_after_weighting": False,
            "gates_applied": False,
            "outcome_variable_used": False,
            "optimization_performed": False,
        },
        "reconciliation": {
            "candidate_count": int(len(candidate_units)),
            "baseline_rows": int(len(reference)),
            "sensitivity_rows": int(len(scored)),
            "duplicate_sensitivity_ids": int(scored.hex_id.duplicated().sum()),
            "missing_ids_vs_baseline": sorted(set(reference.hex_id) - set(scored.hex_id)),
            "extra_ids_vs_baseline": sorted(set(scored.hex_id) - set(reference.hex_id)),
            "boundary_flag_reconciled_with_baseline": True,
            "boundary_edge_count": int(scored[BOUNDARY_FLAG].sum()),
            "finalized_component_reconciliation": component_reconciliation,
        },
        "score_distributions": score_distributions,
        "rank_correlations_vs_balanced_reference": rank_correlations,
        "rank_movement_vs_balanced_reference": rank_movement,
        "top_tail_overlap_vs_balanced_reference": top_tail_overlap,
        "top_10_percent_component_profiles": top10_profiles,
        "connectivity_thematic_effect": connectivity_effect,
        "riparian_thematic_effect": riparian_effect,
        "availability_cost": _availability_costs(scored),
        "severe_weakness_and_compensability": weaknesses,
        "connectivity_specific_weakness": {
            scheme: (
                weaknesses[scheme]["family_specific_weakness"]["connectivity"]
                if scheme == "balanced_reference"
                else weaknesses[scheme]["family_specific_weakness"]
            )
            for scheme in [
                "balanced_reference",
                *[name for name in SCHEMES if SCHEMES[name]["family"] == "Connectivity First"],
            ]
        },
        "riparian_specific_weakness": {
            scheme: (
                weaknesses[scheme]["family_specific_weakness"]["riparian"]
                if scheme == "balanced_reference"
                else weaknesses[scheme]["family_specific_weakness"]
            )
            for scheme in [
                "balanced_reference",
                *[name for name in SCHEMES if SCHEMES[name]["family"] == "Riparian Restoration"],
            ]
        },
        "ecology_availability_tradeoff": _tradeoff(scored),
        "leave_theme_effect": _leave_theme_effect(scored),
        "weighted_contribution_sd": _contribution_sd(scored),
        "top_100_churn": _top100_churn(scored),
        "extreme_movers": _extreme_movers(scored),
        "spatial_distribution_sanity": _spatial_sanity(scored, candidate_units),
        "preset_intensity_comparison": {
            "connectivity_first": _intensity_comparison(
                scored, "Connectivity First", "ecological_network_score"
            ),
            "riparian_restoration": _intensity_comparison(
                scored, "Riparian Restoration", "riparian_opportunity_score"
            ),
        },
        "candidate_selection_assessment": _candidate_assessment(
            scored, rank_correlations, rank_movement, top_tail_overlap, weaknesses
        ),
        "balanced_reference_assessment": {
            "serious_reason_to_reject_equal_weights": False,
            "assessment": "No serious rejection reason was revealed in Step 22; the equal-weight model remains the approved Balanced reference candidate and is not changed here.",
        },
        "interpretive_caveats": [
            "Weights are policy/scenario choices, not statistically learned parameters.",
            "No outcome variable was used and no optimization was performed.",
            "Thematic presets express stakeholder emphasis; they do not establish ecological truth.",
            "Weighted averaging remains compensatory, so strong components can offset weak components.",
            "Percentile ranks are temporary diagnostics only and are not persisted as model scores.",
            "Spatial thirds are broad diagnostics, not maps or ecological regions.",
            "No environmental datasets were downloaded by this command.",
        ],
        "output": {
            "path": str(output_path),
            "columns": list(OUTPUT_COLUMNS),
            "rows": int(len(scored)),
            "geometry_included": False,
            "deterministic_order": "ascending hex_id",
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dependency_changes": [],
        "warnings": [
            "This is a controlled policy-weight sensitivity experiment, not statistical calibration.",
            "Raw score magnitude is not used alone as evidence of preset quality.",
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


def _candidate_assessment(
    frame: pd.DataFrame,
    correlations: dict[str, Any],
    movements: dict[str, Any],
    overlaps: dict[str, Any],
    weaknesses: dict[str, Any],
) -> dict[str, Any]:
    """Apply descriptive review labels without selecting a final scheme."""

    result = {}
    for scheme, config in SCHEMES.items():
        intended = (
            "ecological_network_score"
            if config["family"] == "Connectivity First"
            else "riparian_opportunity_score"
        )
        balanced_median = _top10(frame, "balanced_reference_score")[intended].median()
        thematic_median = _top10(frame, SCHEME_SCORE_COLUMNS[scheme])[intended].median()
        severe_delta = (
            weaknesses[scheme]["severe_weakness"]["any_component_le_25"]
            - weaknesses["balanced_reference"]["severe_weakness"]["any_component_le_25"]
        )
        movement = movements[scheme]["median_absolute_percentile_rank_movement"]
        overlap = overlaps[scheme]["top_10_percent"]["overlap_percent_relative_to_tail"]
        delta = float(thematic_median - balanced_median)
        if delta < 2.0 and movement < 2.0 and overlap > 95.0:
            assessment = "too weak"
        elif movement >= 15.0 or severe_delta > 0.10 * len(
            _top10(frame, SCHEME_SCORE_COLUMNS[scheme])
        ):
            assessment = "highly aggressive"
        else:
            assessment = "meaningfully differentiated"
        result[scheme] = {
            "descriptive_assessment": assessment,
            "intended_component_top_10_median_change": delta,
            "spearman_vs_balanced": correlations[scheme]["spearman"],
            "median_absolute_percentile_rank_movement": movement,
            "top_10_overlap_percent": overlap,
            "severe_weakness_count_change": severe_delta,
            "selection_status": "not selected; reserved for Step 23 review",
        }
    return {
        "classification_rules": {
            "too_weak": "intended top-10 median change <2, median rank movement <2, and top-10 overlap >95%",
            "highly_aggressive": "median rank movement >=15 or severe-weakness increase exceeds 10% of the thematic top-10",
            "otherwise": "meaningfully differentiated",
        },
        "schemes": result,
        "final_selection_made": False,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    output, provenance = build_sensitivity()
    correlations = provenance["rank_correlations_vs_balanced_reference"]
    print(
        f"Preset sensitivity: {len(output):,} candidates, seven schemes written to {OUTPUT_PATH} "
        f"({OUTPUT_PATH.stat().st_size:,} bytes)"
    )
    print(
        "Spearman vs balanced: "
        + ", ".join(f"{name}={values['spearman']:.6f}" for name, values in correlations.items())
    )
    print(
        f"Provenance: {PROVENANCE_PATH} ({PROVENANCE_PATH.stat().st_size:,} bytes); "
        "no final thematic preset selected."
    )


if __name__ == "__main__":
    main()
