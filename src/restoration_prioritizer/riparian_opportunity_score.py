"""Score the finalized focal Riparian Opportunity indicator.

``riparian_focal_fraction`` is the sole scoring input. Zero is an absolute
absence of mapped focal hydrologic context and remains score zero; positive
observations receive an empirical percentile among the positive population.
The adjacent, near, and local indicators remain supporting diagnostics only.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

RAW_INDICATOR_PATH = Path("data/processed/indicators/riparian_opportunity.csv")
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
HABITAT_RAW_PATH = Path("data/processed/indicators/habitat_context.csv")
HABITAT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
NETWORK_SCORE_PATH = Path("data/processed/components/ecological_network.csv")
OUTPUT_PATH = Path("data/processed/components/riparian_opportunity.csv")
PROVENANCE_PATH = Path("data/processed/components/riparian_opportunity.provenance.json")

SCORING_INPUT = "riparian_focal_fraction"
SUPPORTING_INPUTS = (
    "riparian_adjacent_fraction",
    "riparian_near_fraction",
    "riparian_local_fraction",
)
BOUNDARY_FLAG = "boundary_edge_flag"
HABITAT_LOCAL = "habitat_context_local_fraction"
HABITAT_SCORE = "habitat_context_score"
NETWORK_SCORE = "ecological_network_score"
CANDIDATE_CORRELATION_FIELDS = (
    "candidate_fraction_of_terrestrial",
    "candidate_area_m2",
    "habitat_context_fraction_of_terrestrial",
)
OUTPUT_COLUMNS = ("hex_id", SCORING_INPUT, "riparian_opportunity_score", BOUNDARY_FLAG)


class RiparianOpportunityScoreError(ValueError):
    """Raised when a Riparian Opportunity score input is malformed."""


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...], label: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise RiparianOpportunityScoreError(f"{label} is missing required fields: {missing}")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    _require_columns(frame, ("hex_id",), label)
    if frame["hex_id"].isna().any():
        raise RiparianOpportunityScoreError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if (ids.str.strip().str.len() == 0).any():
        raise RiparianOpportunityScoreError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise RiparianOpportunityScoreError(f"{label} hex_id values must be unique")
    return ids


def _numeric(values: pd.Series, field: str) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any():
        raise RiparianOpportunityScoreError(f"{field} contains missing or non-numeric values")
    result = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(result)):
        raise RiparianOpportunityScoreError(f"{field} contains non-finite values")
    return result


def _validate_fraction(values: pd.Series, field: str) -> None:
    numeric = _numeric(values, field)
    if np.any((numeric < 0) | (numeric > 1)):
        raise RiparianOpportunityScoreError(f"{field} must lie in [0, 1]")


def _normalise_boundary_flags(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise RiparianOpportunityScoreError(f"{BOUNDARY_FLAG} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise RiparianOpportunityScoreError(f"{BOUNDARY_FLAG} must contain only true/false")
        return values.astype(bool)
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin(["true", "false"]).all():
        raise RiparianOpportunityScoreError(f"{BOUNDARY_FLAG} must contain only true/false")
    return normalised.eq("true")


def validate_raw_indicators(
    indicators: pd.DataFrame, expected_candidate_count: int | None = None
) -> None:
    """Validate the selected focal field and retained supporting raw fields."""

    if not isinstance(indicators, pd.DataFrame):
        raise RiparianOpportunityScoreError("Raw Riparian Opportunity input must be a DataFrame")
    _require_columns(
        indicators,
        ("hex_id", SCORING_INPUT, *SUPPORTING_INPUTS, BOUNDARY_FLAG),
        "Raw Riparian Opportunity input",
    )
    _normalise_ids(indicators, "Raw Riparian Opportunity input")
    if expected_candidate_count is not None and len(indicators) != expected_candidate_count:
        raise RiparianOpportunityScoreError(
            "Raw Riparian Opportunity row count does not match candidate population "
            f"({len(indicators)} != {expected_candidate_count})"
        )
    for field in (SCORING_INPUT, *SUPPORTING_INPUTS):
        _validate_fraction(indicators[field], field)
    _normalise_boundary_flags(indicators[BOUNDARY_FLAG])
    _require_columns(
        indicators,
        ("focal_wetland_fraction", "focal_inland_water_fraction"),
        "Raw Riparian Opportunity audit input",
    )
    _validate_fraction(indicators["focal_wetland_fraction"], "focal_wetland_fraction")
    _validate_fraction(indicators["focal_inland_water_fraction"], "focal_inland_water_fraction")


def validate_candidate_reconciliation(
    indicators: pd.DataFrame, candidate_units: pd.DataFrame
) -> dict[str, int | bool]:
    """Require an exact one-to-one reconciliation between raw and candidate IDs."""

    if not isinstance(candidate_units, pd.DataFrame):
        raise RiparianOpportunityScoreError("Candidate input must be a DataFrame")
    _require_columns(
        candidate_units,
        ("hex_id", *CANDIDATE_CORRELATION_FIELDS),
        "Candidate input",
    )
    raw_ids = _normalise_ids(indicators, "Raw Riparian Opportunity input")
    candidate_ids = _normalise_ids(candidate_units, "Candidate input")
    for field in ("candidate_fraction_of_terrestrial", "habitat_context_fraction_of_terrestrial"):
        _validate_fraction(candidate_units[field], field)
    _numeric(candidate_units["candidate_area_m2"], "candidate_area_m2")
    raw_set = set(raw_ids)
    candidate_set = set(candidate_ids)
    missing = candidate_set - raw_set
    extra = raw_set - candidate_set
    if len(indicators) != len(candidate_units) or missing or extra:
        raise RiparianOpportunityScoreError(
            "Raw Riparian Opportunity and candidate IDs do not reconcile exactly: "
            f"missing={len(missing)}, extra={len(extra)}, "
            f"raw_count={len(indicators)}, candidate_count={len(candidate_units)}"
        )
    return {
        "raw_rows": int(len(indicators)),
        "candidate_rows": int(len(candidate_units)),
        "duplicate_raw_ids": 0,
        "duplicate_candidate_ids": 0,
        "missing_candidate_ids": int(len(missing)),
        "extra_raw_ids": int(len(extra)),
        "ids_reconcile_exactly": True,
    }


def positive_percentile_scores(
    values: pd.Series | np.ndarray | list[float],
) -> np.ndarray:
    """Return zero-anchored positive-population empirical percentile scores.

    Zeros are assigned exactly zero. Positive values are ranked only against
    positive observations with average ranks for ties, then mapped as
    ``100 * average_positive_rank / positive_count``.
    """

    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.empty:
        raise RiparianOpportunityScoreError("At least one focal observation is required")
    if numeric.isna().any():
        raise RiparianOpportunityScoreError(
            f"{SCORING_INPUT} contains missing or non-numeric values"
        )
    array = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(array)):
        raise RiparianOpportunityScoreError(f"{SCORING_INPUT} contains non-finite values")
    if np.any((array < 0) | (array > 1)):
        raise RiparianOpportunityScoreError(f"{SCORING_INPUT} must lie in [0, 1]")
    positive = array > 0
    scores = np.zeros(len(array), dtype=float)
    if positive.any():
        ranks = pd.Series(array[positive]).rank(method="average", ascending=True).to_numpy()
        scores[positive] = 100.0 * ranks / float(positive.sum())
    return scores


def calculate_scores(indicators: pd.DataFrame) -> pd.DataFrame:
    """Calculate one deterministically ordered component row per raw row."""

    validate_raw_indicators(indicators)
    focal = pd.to_numeric(indicators[SCORING_INPUT], errors="raise").to_numpy(dtype=float)
    result = (
        pd.DataFrame(
            {
                "hex_id": indicators["hex_id"].astype(str),
                SCORING_INPUT: focal,
                "riparian_opportunity_score": positive_percentile_scores(focal),
                BOUNDARY_FLAG: _normalise_boundary_flags(indicators[BOUNDARY_FLAG]).to_numpy(),
            }
        )
        .sort_values("hex_id", kind="mergesort")
        .reset_index(drop=True)
    )
    if result["hex_id"].duplicated().any():
        raise RiparianOpportunityScoreError("Component output contains duplicate hex_id values")
    scores = result["riparian_opportunity_score"].to_numpy(dtype=float)
    if not np.all(np.isfinite(scores)) or np.any((scores < 0) | (scores > 100)):
        raise RiparianOpportunityScoreError(
            "Riparian Opportunity scores must be finite and in [0, 100]"
        )
    return result[list(OUTPUT_COLUMNS)]


def _distribution(values: pd.Series | np.ndarray) -> dict[str, float | int]:
    numeric = pd.to_numeric(pd.Series(values), errors="raise")
    if numeric.empty:
        return {
            key: None
            for key in (
                "n",
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
        }  # type: ignore[return-value]
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


def _range_bin_counts(values: pd.Series) -> dict[str, int]:
    masks = {
        "score = 0": values == 0,
        "> 0–10": (values > 0) & (values <= 10),
        "> 10–25": (values > 10) & (values <= 25),
        "> 25–50": (values > 25) & (values <= 50),
        "> 50–75": (values > 50) & (values <= 75),
        "> 75–90": (values > 75) & (values <= 90),
        "> 90": values > 90,
    }
    return {label: int(mask.sum()) for label, mask in masks.items()}


def _band_masks(values: pd.Series) -> dict[str, pd.Series]:
    return {
        "0–25": values.between(0, 25, inclusive="both"),
        ">25–50": (values > 25) & (values <= 50),
        ">50–75": (values > 50) & (values <= 75),
        ">75–100": (values > 75) & (values <= 100),
    }


def _tie_diagnostics(raw: pd.Series, scores: pd.Series) -> dict[str, Any]:
    positive = raw > 0
    counts = raw.loc[positive].value_counts()
    tied = counts[counts > 1]
    return {
        "unique_positive_raw_values": int(raw.loc[positive].nunique()),
        "positive_candidates_participating_in_ties": int(tied.sum()),
        "positive_candidates_participating_in_ties_percent": float(
            100.0 * tied.sum() / positive.sum()
        )
        if positive.any()
        else 0.0,
        "positive_tie_group_count": int(len(tied)),
        "largest_positive_tie_group": int(tied.max()) if not tied.empty else 1,
        "zero_tie_group_count": 1 if (~positive).any() else 0,
        "zero_tie_group_size": int((~positive).sum()),
        "distinct_positive_score_values": int(scores.loc[positive].nunique()),
        "ties_materially_affect_interpretation": False,
        "interpretation": (
            "Positive ties receive shared average-rank scores and reduce granularity; the large "
            "zero tie is reported separately because zero has an absolute absence meaning."
        ),
    }


def _score_anchors(raw: pd.Series, scores: pd.Series) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    for threshold in (25, 50, 75, 90, 95):
        selected = raw.loc[scores >= threshold]
        value = float(selected.min()) if not selected.empty else None
        result[str(threshold)] = {
            "score_threshold": threshold,
            "minimum_raw_fraction": value,
            "minimum_raw_percent": value * 100.0 if value is not None else None,
            "candidate_count": int(len(selected)),
        }
    return result


def _primary_contribution(frame: pd.DataFrame) -> dict[str, Any]:
    wetland = frame["focal_wetland_fraction"]
    water = frame["focal_inland_water_fraction"]
    masks = {
        "wetland_only": (wetland > 0) & (water == 0),
        "inland_water_only": (water > 0) & (wetland == 0),
        "both_wetland_and_inland_water": (wetland > 0) & (water > 0),
        "neither": (wetland == 0) & (water == 0),
    }
    return {
        "candidate_count": int(len(frame)),
        "wetland_fraction_sum": float(wetland.sum()),
        "inland_water_fraction_sum": float(water.sum()),
        "wetland_fraction_mean": float(wetland.mean()) if len(frame) else None,
        "inland_water_fraction_mean": float(water.mean()) if len(frame) else None,
        "presence_categories": {
            label: {"count": int(mask.sum()), "percent": 100.0 * int(mask.sum()) / len(frame)}
            if len(frame)
            else {"count": 0, "percent": 0.0}
            for label, mask in masks.items()
        },
    }


def _high_tail_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for threshold in (75, 90, 95):
        subset = joined.loc[joined["riparian_opportunity_score"] >= threshold]
        result[str(threshold)] = {
            "score_threshold": threshold,
            "candidate_count": int(len(subset)),
            "median_raw_focal_fraction": float(subset[SCORING_INPUT].median())
            if len(subset)
            else None,
            "median_raw_focal_percent": float(subset[SCORING_INPUT].median() * 100)
            if len(subset)
            else None,
            "median_habitat_context_score": float(subset[HABITAT_SCORE].median())
            if len(subset)
            else None,
            "median_ecological_network_score": float(subset[NETWORK_SCORE].median())
            if len(subset)
            else None,
            "boundary_edge_count": int(subset[BOUNDARY_FLAG].sum()),
            "sea_containing_count": int((subset["sea_pixels"] > 0).sum()),
            "focal_wetland_vs_inland_water": _primary_contribution(subset),
        }
    return result


def _zero_population_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    subset = joined.loc[joined["riparian_opportunity_score"] == 0]
    return {
        "candidate_count": int(len(subset)),
        "candidate_percent": 100.0 * len(subset) / len(joined),
        "habitat_context_score": _distribution(subset[HABITAT_SCORE]),
        "ecological_network_score": _distribution(subset[NETWORK_SCORE]),
        "candidate_fraction_of_terrestrial": _distribution(
            subset["candidate_fraction_of_terrestrial"]
        ),
    }


def _supporting_adjacent_audit(joined: pd.DataFrame) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for label, mask in {
        "score_zero": joined["riparian_opportunity_score"] == 0,
        "score_at_most_25": joined["riparian_opportunity_score"] <= 25,
    }.items():
        subset = joined.loc[mask]
        counts = {
            "adjacent_ge_1_percent": int((subset["riparian_adjacent_fraction"] >= 0.01).sum()),
            "adjacent_ge_5_percent": int((subset["riparian_adjacent_fraction"] >= 0.05).sum()),
            "adjacent_ge_10_percent": int((subset["riparian_adjacent_fraction"] >= 0.10).sum()),
            "adjacent_ge_25_percent": int((subset["riparian_adjacent_fraction"] >= 0.25).sum()),
        }
        results[label] = {"candidate_count": int(len(subset)), **counts}
    return results


def _rescue_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    masks = {
        "focal_le_1_percent_adjacent_ge_10_percent": (joined[SCORING_INPUT] <= 0.01)
        & (joined["riparian_adjacent_fraction"] >= 0.10),
        "focal_le_1_percent_adjacent_ge_25_percent": (joined[SCORING_INPUT] <= 0.01)
        & (joined["riparian_adjacent_fraction"] >= 0.25),
    }
    return {
        label: {
            "candidate_count": int(mask.sum()),
            "candidate_percent": 100.0 * int(mask.sum()) / len(joined),
            "final_riparian_score_distribution": _distribution(
                joined.loc[mask, "riparian_opportunity_score"]
            ),
        }
        for label, mask in masks.items()
    }


def _component_band_contrasts(joined: pd.DataFrame) -> dict[str, int]:
    habitat = joined[HABITAT_SCORE]
    network = joined[NETWORK_SCORE]
    riparian = joined["riparian_opportunity_score"]
    return {
        "high_habitat_high_riparian": int(((habitat >= 75) & (riparian >= 75)).sum()),
        "high_habitat_low_riparian": int(((habitat >= 75) & (riparian <= 25)).sum()),
        "low_habitat_high_riparian": int(((habitat <= 25) & (riparian >= 75)).sum()),
        "high_network_high_riparian": int(((network >= 75) & (riparian >= 75)).sum()),
        "low_network_high_riparian": int(((network <= 25) & (riparian >= 75)).sum()),
    }


def _correlation_matrix(
    joined: pd.DataFrame,
) -> dict[str, dict[str, dict[str, float | int | None]]]:
    fields = {
        "Habitat Context": HABITAT_SCORE,
        "Ecological Network Context": NETWORK_SCORE,
        "Riparian Opportunity": "riparian_opportunity_score",
    }
    matrices: dict[str, dict[str, dict[str, float | int | None]]] = {
        "pearson": {},
        "spearman": {},
    }
    for left_label, left_field in fields.items():
        matrices["pearson"][left_label] = {}
        matrices["spearman"][left_label] = {}
        for right_label, right_field in fields.items():
            relation = _correlation(joined[left_field], joined[right_field])
            matrices["pearson"][left_label][right_label] = relation["pearson"]
            matrices["spearman"][left_label][right_label] = relation["spearman"]
    return matrices


def _validate_finalized_component(
    frame: pd.DataFrame, candidate_ids: set[str], required: tuple[str, ...], label: str
) -> pd.DataFrame:
    _require_columns(frame, required, label)
    ids = _normalise_ids(frame, label)
    if set(ids) != candidate_ids or len(ids) != len(candidate_ids):
        raise RiparianOpportunityScoreError(f"{label} IDs do not reconcile with candidates")
    for field in required[1:]:
        values = _numeric(frame[field], field)
        if field.endswith("_score") and np.any((values < 0) | (values > 100)):
            raise RiparianOpportunityScoreError(f"{field} must lie in [0, 100]")
        if field.endswith("_fraction") and np.any((values < 0) | (values > 1)):
            raise RiparianOpportunityScoreError(f"{field} must lie in [0, 1]")
    result = frame.copy()
    result["hex_id"] = ids
    return result


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    frame.to_csv(
        temporary_path,
        index=False,
        columns=list(OUTPUT_COLUMNS),
        float_format="%.10f",
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


def build_riparian_opportunity_score(
    raw_indicator_path: Path = RAW_INDICATOR_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    habitat_raw_path: Path = HABITAT_RAW_PATH,
    habitat_score_path: Path = HABITAT_SCORE_PATH,
    network_score_path: Path = NETWORK_SCORE_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read, validate, score, audit, and write the final component."""

    start = time.perf_counter()
    try:
        raw = pd.read_csv(raw_indicator_path)
        candidates = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
        habitat_raw = pd.read_csv(habitat_raw_path)
        habitat_score = pd.read_csv(habitat_score_path)
        network_score = pd.read_csv(network_score_path)
    except (OSError, ValueError) as exc:
        raise RiparianOpportunityScoreError(
            f"Could not read Riparian Opportunity input artifact: {exc}"
        ) from exc

    validate_raw_indicators(raw, expected_candidate_count=len(candidates))
    reconciliation = validate_candidate_reconciliation(raw, candidates)
    scored = calculate_scores(raw)
    candidate_ids = set(candidates["hex_id"].astype(str))
    output_ids = set(scored["hex_id"])
    missing_ids = candidate_ids - output_ids
    extra_ids = output_ids - candidate_ids
    if len(scored) != len(candidates) or missing_ids or extra_ids:
        raise RiparianOpportunityScoreError(
            "Riparian Opportunity component output does not contain exactly one score per candidate"
        )

    _validate_finalized_component(
        habitat_raw, candidate_ids, ("hex_id", HABITAT_LOCAL), "Habitat Context raw artifact"
    )
    _validate_finalized_component(
        habitat_score,
        candidate_ids,
        ("hex_id", HABITAT_LOCAL, HABITAT_SCORE),
        "Habitat Context component artifact",
    )
    _validate_finalized_component(
        network_score,
        candidate_ids,
        ("hex_id", NETWORK_SCORE),
        "Ecological Network Context component artifact",
    )

    raw_by_id = raw.copy()
    raw_by_id["hex_id"] = raw_by_id["hex_id"].astype(str)
    raw_by_id = raw_by_id.set_index("hex_id").loc[scored["hex_id"]]
    candidate_by_id = candidates.copy()
    candidate_by_id["hex_id"] = candidate_by_id["hex_id"].astype(str)
    candidate_by_id = candidate_by_id.set_index("hex_id").loc[scored["hex_id"]]
    habitat_raw_by_id = habitat_raw.set_index("hex_id").loc[scored["hex_id"]]
    habitat_score_by_id = habitat_score.set_index("hex_id").loc[scored["hex_id"]]
    network_by_id = network_score.set_index("hex_id").loc[scored["hex_id"]]

    candidate_composition = candidate_by_id.copy()
    terrestrial = pd.to_numeric(candidate_composition["terrestrial_pixels"], errors="coerce")
    artificial = pd.to_numeric(
        candidate_composition["artificial_constraint_pixels"], errors="coerce"
    )
    if terrestrial.notna().all() and artificial.notna().all() and (terrestrial > 0).all():
        candidate_composition["artificial_constraint_fraction_of_terrestrial"] = (
            artificial / terrestrial
        )
        _validate_fraction(
            candidate_composition["artificial_constraint_fraction_of_terrestrial"],
            "artificial_constraint_fraction_of_terrestrial",
        )
        artificial_clean = True
    else:
        candidate_composition["artificial_constraint_fraction_of_terrestrial"] = np.nan
        artificial_clean = False

    joined = pd.DataFrame(
        {
            "hex_id": scored["hex_id"].to_numpy(),
            SCORING_INPUT: scored[SCORING_INPUT].to_numpy(dtype=float),
            "riparian_opportunity_score": scored["riparian_opportunity_score"].to_numpy(
                dtype=float
            ),
            BOUNDARY_FLAG: scored[BOUNDARY_FLAG].to_numpy(dtype=bool),
            "riparian_adjacent_fraction": raw_by_id["riparian_adjacent_fraction"].to_numpy(
                dtype=float
            ),
            "riparian_near_fraction": raw_by_id["riparian_near_fraction"].to_numpy(dtype=float),
            "riparian_local_fraction": raw_by_id["riparian_local_fraction"].to_numpy(dtype=float),
            "focal_wetland_fraction": raw_by_id["focal_wetland_fraction"].to_numpy(dtype=float),
            "focal_inland_water_fraction": raw_by_id["focal_inland_water_fraction"].to_numpy(
                dtype=float
            ),
            HABITAT_LOCAL: habitat_raw_by_id[HABITAT_LOCAL].to_numpy(dtype=float),
            HABITAT_SCORE: habitat_score_by_id[HABITAT_SCORE].to_numpy(dtype=float),
            NETWORK_SCORE: network_by_id[NETWORK_SCORE].to_numpy(dtype=float),
            "candidate_fraction_of_terrestrial": candidate_composition[
                "candidate_fraction_of_terrestrial"
            ].to_numpy(dtype=float),
            "candidate_area_m2": candidate_composition["candidate_area_m2"].to_numpy(dtype=float),
            "habitat_context_fraction_of_terrestrial": candidate_composition[
                "habitat_context_fraction_of_terrestrial"
            ].to_numpy(dtype=float),
            "artificial_constraint_fraction_of_terrestrial": candidate_composition[
                "artificial_constraint_fraction_of_terrestrial"
            ].to_numpy(dtype=float),
            "sea_pixels": candidate_composition["sea_pixels"].to_numpy(dtype=float),
        }
    )

    raw_focal = joined[SCORING_INPUT]
    scores = joined["riparian_opportunity_score"]
    positive = raw_focal > 0
    zero = raw_focal == 0
    shuffled = calculate_scores(raw.sample(frac=1, random_state=20260909).reset_index(drop=True))
    order_independent = bool(
        scored.set_index("hex_id")["riparian_opportunity_score"]
        .sort_index()
        .equals(shuffled.set_index("hex_id")["riparian_opportunity_score"].sort_index())
    )
    raw_order = np.argsort(raw_focal.to_numpy(), kind="stable")
    monotonic = bool(np.all(np.diff(scores.to_numpy()[raw_order]) >= -1e-12))
    equal_positive_equal_score = bool(
        joined.loc[positive]
        .groupby(SCORING_INPUT, sort=False)["riparian_opportunity_score"]
        .nunique()
        .le(1)
        .all()
    )
    max_positive_maps_100 = bool(scores.loc[raw_focal == raw_focal[positive].max()].eq(100).all())
    validation = {
        "zero_raw_values_score_exactly_zero": bool(scores.loc[zero].eq(0).all()),
        "positive_raw_values_score_strictly_positive": bool((scores.loc[positive] > 0).all()),
        "maximum_positive_raw_scores_100": max_positive_maps_100,
        "equal_positive_raw_values_receive_equal_scores": equal_positive_equal_score,
        "monotonicity_passed": monotonic,
        "score_bounds_passed": bool(np.isfinite(scores).all() and scores.between(0, 100).all()),
        "candidate_order_does_not_affect_scores": order_independent,
        "positive_rank_formula_passed": bool(
            np.allclose(
                scores.loc[positive].to_numpy(),
                100
                * pd.Series(raw_focal.loc[positive].to_numpy()).rank(method="average").to_numpy()
                / int(positive.sum()),
                rtol=0,
                atol=1e-12,
            )
        ),
    }
    if not all(validation.values()):
        raise RiparianOpportunityScoreError(
            f"Riparian Opportunity transformation validation failed: {validation}"
        )

    raw_to_score_anchor = _score_anchors(raw_focal, scores)
    positive_raw = raw_focal.loc[positive]
    positive_scores = scores.loc[positive]
    positive_percentile_check = {
        "expected_percentiles": [10, 25, 50, 75, 90, 95, 99],
        "observed_score_quantiles": {
            str(q): float(positive_scores.quantile(q / 100)) for q in (10, 25, 50, 75, 90, 95, 99)
        },
        "deviation_from_nominal_percentile_points": {
            str(q): float(positive_scores.quantile(q / 100) - q)
            for q in (10, 25, 50, 75, 90, 95, 99)
        },
        "interpretation": (
            "Ties can make positive-subset score quantiles depart slightly from their nominal "
            "percentile labels; the ordered positive rank scale remains the governing rule."
        ),
    }
    component_correlations = {
        "scored_riparian_vs_habitat_context_score": _correlation(scores, joined[HABITAT_SCORE]),
        "scored_riparian_vs_habitat_context_local_fraction": _correlation(
            scores, joined[HABITAT_LOCAL]
        ),
        "scored_riparian_vs_ecological_network_score": _correlation(scores, joined[NETWORK_SCORE]),
        "raw_focal_vs_habitat_context_score": _correlation(raw_focal, joined[HABITAT_SCORE]),
        "raw_focal_vs_habitat_context_local_fraction": _correlation(
            raw_focal, joined[HABITAT_LOCAL]
        ),
        "raw_focal_vs_ecological_network_score": _correlation(raw_focal, joined[NETWORK_SCORE]),
    }
    candidate_relationships = {
        field: _correlation(scores, joined[field]) for field in CANDIDATE_CORRELATION_FIELDS
    }
    if artificial_clean:
        candidate_relationships["artificial_constraint_fraction_of_terrestrial"] = _correlation(
            scores, joined["artificial_constraint_fraction_of_terrestrial"]
        )
    else:
        candidate_relationships["artificial_constraint_fraction_of_terrestrial"] = {
            "n": 0,
            "pearson": None,
            "spearman": None,
            "available_cleanly": False,
        }

    provenance: dict[str, Any] = {
        "component_name": "Riparian Opportunity",
        "status": "canonical production artifact",
        "source": {
            "raw_indicator_path": str(raw_indicator_path),
            "candidate_source_path": str(candidate_units_path),
            "candidate_source_layer": CANDIDATE_UNITS_LAYER,
            "habitat_context_raw_path": str(habitat_raw_path),
            "habitat_context_score_path": str(habitat_score_path),
            "ecological_network_score_path": str(network_score_path),
            "environmental_datasets_downloaded": False,
        },
        "selected_raw_input": {
            "field": SCORING_INPUT,
            "role": "sole Riparian Opportunity scoring input",
            "definition": (
                "Fraction of the candidate hex's mapped non-marine landscape that consists of "
                "NMD wetland context or inland water."
            ),
            "hydrologic_context": "NMD wetland classes plus inland water class 61",
            "denominator": "terrestrial land plus inland water",
            "excluded": "sea and no-data",
            "interpretation": "hydrologic land-cover context proxy, not a functional probability",
        },
        "supporting_raw_inputs": {
            field: {
                "retained": True,
                "used_in_score": False,
                "role": "supporting methodology diagnostic",
            }
            for field in SUPPORTING_INPUTS
        },
        "scoring": {
            "selected_raw_field": SCORING_INPUT,
            "formula": (
                "if x_i == 0: riparian_opportunity_score = 0; otherwise "
                "100 * average ascending rank among x > 0 / N_pos"
            ),
            "zero_value_convention": (
                "A raw focal fraction of exactly zero means no mapped focal wetland or inland "
                "water and is assigned exactly zero."
            ),
            "positive_population_count": int(positive.sum()),
            "zero_population_count": int(zero.sum()),
            "tie_handling": "average ascending ranks among positive observations",
            "direction": "higher is better as relative focal-context standing",
            "score_range": [0, 100],
            "not_used": [
                "100 * (rank - 1) / (N_pos - 1)",
                "direct raw * 100",
                "min-max scaling",
                "log scaling",
                "p95 clipping",
                "arbitrary ecological thresholds",
                "near/focal weighting",
                "Habitat Context adjustment",
            ],
            "why_percentile": (
                "The focal fraction is highly right-skewed, constrained by candidate-land "
                "composition, and observed only up to approximately 0.66 in this population. "
                "Positive-population empirical percentiles preserve zero as real absence, preserve "
                "positive ordering, avoid invented ecological thresholds, provide a usable common "
                "0–100 decision-support scale, and prevent the observed raw maximum from arbitrarily "
                "capping effective component influence."
            ),
            "interpretation_scope": "relative among positive focal-context candidates, not absolute ecological quality",
        },
        "candidate_population_validation": {
            **reconciliation,
            "output_rows": int(len(scored)),
            "output_duplicate_id_count": int(scored["hex_id"].duplicated().sum()),
            "output_missing_candidate_ids": int(len(missing_ids)),
            "output_extra_candidate_ids": int(len(extra_ids)),
            "one_score_per_candidate": True,
        },
        "raw_distribution": _distribution(raw_focal),
        "score_distribution": _distribution(scores),
        "score_range_bins": _range_bin_counts(scores),
        "positive_subset_score_distribution": _distribution(positive_scores),
        "positive_subset_percentile_check": positive_percentile_check,
        "transformation_validation": validation,
        "tie_diagnostics": _tie_diagnostics(raw_focal, scores),
        "raw_to_score_anchors": raw_to_score_anchor,
        "positive_raw_fraction_at_positive_score_quantiles": {
            str(q): {
                "positive_score_percentile": q,
                "raw_fraction": float(positive_raw.quantile(q / 100)),
                "raw_percent": float(positive_raw.quantile(q / 100) * 100),
            }
            for q in (50, 75, 90, 95)
        },
        "finalized_component_correlations": component_correlations,
        "three_component_score_correlation_matrix": _correlation_matrix(joined),
        "component_band_contrasts": _component_band_contrasts(joined),
        "zero_riparian_population": _zero_population_diagnostics(joined),
        "high_riparian_tail": _high_tail_diagnostics(joined),
        "supporting_adjacent_context_audit": _supporting_adjacent_audit(joined),
        "step_14_rescue_case_status": _rescue_diagnostics(joined),
        "candidate_composition_correlations": candidate_relationships,
        "selected_vs_rejected_summary": {
            "focal": "selected; most distinct serious scale and direct in-unit hydrologic context",
            "adjacent": "supporting diagnostic; immediate surroundings but more redundant with Habitat Context",
            "near": (
                "rejected for scoring; boundary-rescue benefit was limited while whole-population "
                "redundancy and rank reshuffling increased"
            ),
            "local": "rejected for scoring; overly broad, hydrologic presence nearly universal, and redundant",
        },
        "step_14_evidence": {
            "focal_habitat_spearman": 0.475,
            "adjacent_habitat_spearman": 0.675,
            "near_habitat_spearman": 0.636,
            "local_habitat_spearman": 0.725,
            "focal_zero_percent_approximately": 26.5,
            "near_median_absolute_rank_change_percentile_points_approximately": 12.5,
            "near_candidates_moving_at_least_25_percentile_points_approximately": 20.4,
            "clear_boundary_rescue_count": 266,
            "clear_boundary_rescue_percent_approximately": 1.0,
        },
        "wetland_inland_water_composition_diagnostics": {
            "all_candidates": _primary_contribution(joined),
            "high_riparian_groups": {
                threshold: _high_tail_diagnostics(joined)[str(threshold)][
                    "focal_wetland_vs_inland_water"
                ]
                for threshold in (75, 90, 95)
            },
        },
        "boundary_handling": {
            "boundary_flag_field": BOUNDARY_FLAG,
            "used_in_score": False,
            "edge_candidates_excluded": False,
            "decision": (
                "County-edge candidates remain in the MVP population; no cross-county correction "
                "or external hydrology is applied."
            ),
        },
        "caveats": [
            "NMD may underrepresent narrow streams.",
            "No external stream dataset or detailed vector hydrology is used.",
            "County-edge context is not imputed or corrected beyond the retained boundary flag.",
            "The indicator is a hydrologic land-cover context proxy, not riparian-function probability, connectivity, water-quality benefit, feasibility, flood mitigation, or stream proximity.",
            "A score of 90 means approximately a 90th percentile standing among candidates with some focal mapped hydrologic context; it does not mean 90% riparian quality, benefit, or restoration suitability.",
        ],
        "interpretation": (
            "A score of 90 means approximately: among candidate units with some focal mapped "
            "hydrologic context, this candidate ranks around the 90th percentile for focal "
            "wetland/inland-water share. Zero retains a distinct absolute interpretation."
        ),
        "output": {
            "path": str(output_path),
            "columns": list(scored.columns),
            "row_count": int(len(scored)),
            "deterministic_order": "ascending hex_id",
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - start,
    }
    _write_csv(scored, output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["provenance_output"] = {"path": str(provenance_path), "size_bytes": 0}
    for _ in range(5):
        _write_json(provenance_path, provenance)
        actual_size = int(provenance_path.stat().st_size)
        if actual_size == provenance["provenance_output"]["size_bytes"]:
            break
        provenance["provenance_output"]["size_bytes"] = actual_size
    return scored, provenance


def main() -> None:
    """Generate the real-data Riparian Opportunity component and print its audit."""

    scored, provenance = build_riparian_opportunity_score()
    distribution = provenance["score_distribution"]
    validation = provenance["transformation_validation"]
    print(
        f"Riparian Opportunity score: {provenance['output']['path']} "
        f"({provenance['output']['size_bytes']:,} bytes, {len(scored):,} rows)"
    )
    print(
        f"Score min {distribution['min']:.6f}; max {distribution['max']:.6f}; "
        f"mean {distribution['mean']:.6f}; median {distribution['median']:.6f}"
    )
    print(
        f"Zero/positive: {provenance['scoring']['zero_population_count']:,}/"
        f"{provenance['scoring']['positive_population_count']:,}; "
        f"validation: {'passed' if all(validation.values()) else 'FAILED'}"
    )
    print(f"Provenance: {provenance['provenance_output']['path']}")


if __name__ == "__main__":
    main()
