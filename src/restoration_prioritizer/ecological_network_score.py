"""Score the finalized Ecological Network Context configuration proxy.

``opposing_balance_ratio`` is the sole scoring input. It already has a fixed
0–1 structural interpretation, so the MVP component uses direct multiplication
by 100. This module deliberately implements only this component; it does not
provide generic normalization or multi-component scoring infrastructure.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

RAW_INDICATOR_PATH = Path("data/processed/indicators/ecological_network.csv")
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
HABITAT_CONTEXT_RAW_PATH = Path("data/processed/indicators/habitat_context.csv")
HABITAT_CONTEXT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
OUTPUT_PATH = Path("data/processed/components/ecological_network.csv")
PROVENANCE_PATH = Path("data/processed/components/ecological_network.provenance.json")

SCORING_INPUT = "opposing_balance_ratio"
DOMINANT_DIAGNOSTIC = "dominant_opposing_pair_share"
ABSOLUTE_DIAGNOSTICS = ("bridge_strength_max", "bridge_strength_mean")
NEIGHBOR_SUPPORT = "neighbor_habitat_mean"
BOUNDARY_FLAG = "boundary_edge_flag"
HABITAT_LOCAL = "habitat_context_local_fraction"
HABITAT_SCORE = "habitat_context_score"
CANDIDATE_CORRELATION_FIELDS = (
    "candidate_fraction_of_terrestrial",
    "candidate_area_m2",
    "habitat_context_fraction_of_terrestrial",
)
OUTPUT_COLUMNS = ("hex_id", SCORING_INPUT, "ecological_network_score", BOUNDARY_FLAG)
RAW_REQUIRED_COLUMNS = (
    "hex_id",
    SCORING_INPUT,
    DOMINANT_DIAGNOSTIC,
    *ABSOLUTE_DIAGNOSTICS,
    NEIGHBOR_SUPPORT,
)
HABITAT_REQUIRED_COLUMNS = ("hex_id", HABITAT_LOCAL, HABITAT_SCORE)

SCORE_BANDS = ("0–25", ">25–50", ">50–75", ">75–100")
SCORE_RANGE_BINS = ("0", ">0–10", ">10–25", ">25–50", ">50–75", ">75–90", ">90")


class EcologicalNetworkScoreError(ValueError):
    """Raised when the final network-score input contract is invalid."""


def _require_columns(frame: pd.DataFrame, required: Sequence[str], label: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise EcologicalNetworkScoreError(f"{label} is missing required fields: {missing}")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    if "hex_id" not in frame.columns:
        raise EcologicalNetworkScoreError(f"{label} is missing required fields: ['hex_id']")
    if frame["hex_id"].isna().any():
        raise EcologicalNetworkScoreError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if (ids.str.strip().str.len() == 0).any():
        raise EcologicalNetworkScoreError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise EcologicalNetworkScoreError(f"{label} hex_id values must be unique")
    return ids


def _numeric(values: pd.Series, field: str) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any():
        raise EcologicalNetworkScoreError(f"{field} contains missing or non-numeric values")
    result = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(result)):
        raise EcologicalNetworkScoreError(f"{field} contains non-finite values")
    return result


def _validate_fraction(values: pd.Series, field: str) -> None:
    numeric = _numeric(values, field)
    if np.any((numeric < 0) | (numeric > 1)):
        raise EcologicalNetworkScoreError(f"{field} must lie in [0, 1]")


def _validate_nonnegative(values: pd.Series, field: str) -> None:
    numeric = _numeric(values, field)
    if np.any(numeric < 0):
        raise EcologicalNetworkScoreError(f"{field} must be non-negative")


def _normalise_boundary_flags(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise EcologicalNetworkScoreError(f"{BOUNDARY_FLAG} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise EcologicalNetworkScoreError(
                f"{BOUNDARY_FLAG} must contain only true/false values"
            )
        return values.astype(bool)
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin(["true", "false"]).all():
        raise EcologicalNetworkScoreError(f"{BOUNDARY_FLAG} must contain only true/false values")
    return normalised.eq("true")


def validate_raw_indicators(
    indicators: pd.DataFrame, expected_candidate_count: int | None = None
) -> None:
    """Validate selected and supporting raw network indicators."""

    if not isinstance(indicators, pd.DataFrame):
        raise EcologicalNetworkScoreError("Raw Ecological Network input must be a DataFrame")
    _require_columns(indicators, RAW_REQUIRED_COLUMNS, "Raw Ecological Network input")
    _normalise_ids(indicators, "Raw Ecological Network input")
    if expected_candidate_count is not None and len(indicators) != expected_candidate_count:
        raise EcologicalNetworkScoreError(
            "Raw Ecological Network row count does not match candidate population "
            f"({len(indicators)} != {expected_candidate_count})"
        )
    _validate_fraction(indicators[SCORING_INPUT], SCORING_INPUT)
    _validate_fraction(indicators[DOMINANT_DIAGNOSTIC], DOMINANT_DIAGNOSTIC)
    for field in ABSOLUTE_DIAGNOSTICS:
        _validate_fraction(indicators[field], field)
    _validate_fraction(indicators[NEIGHBOR_SUPPORT], NEIGHBOR_SUPPORT)
    if (indicators[DOMINANT_DIAGNOSTIC] > indicators[SCORING_INPUT] + 1e-12).any():
        raise EcologicalNetworkScoreError(f"{DOMINANT_DIAGNOSTIC} must not exceed {SCORING_INPUT}")
    if BOUNDARY_FLAG in indicators.columns:
        _normalise_boundary_flags(indicators[BOUNDARY_FLAG])


def validate_candidate_reconciliation(
    indicators: pd.DataFrame, candidate_units: pd.DataFrame
) -> dict[str, int | bool]:
    """Require an exact one-to-one reconciliation between raw and candidates."""

    if not isinstance(candidate_units, pd.DataFrame):
        raise EcologicalNetworkScoreError("Candidate input must be a DataFrame")
    _require_columns(
        candidate_units,
        ("hex_id", *CANDIDATE_CORRELATION_FIELDS),
        "Candidate input",
    )
    raw_ids = _normalise_ids(indicators, "Raw Ecological Network input")
    candidate_ids = _normalise_ids(candidate_units, "Candidate input")
    for field in ("candidate_fraction_of_terrestrial", "habitat_context_fraction_of_terrestrial"):
        _validate_fraction(candidate_units[field], field)
    _validate_nonnegative(candidate_units["candidate_area_m2"], "candidate_area_m2")
    raw_set = set(raw_ids)
    candidate_set = set(candidate_ids)
    missing = candidate_set - raw_set
    extra = raw_set - candidate_set
    if len(indicators) != len(candidate_units) or missing or extra:
        raise EcologicalNetworkScoreError(
            "Raw Ecological Network and candidate IDs do not reconcile exactly: "
            f"missing={len(missing)}, extra={len(extra)}, "
            f"raw_count={len(indicators)}, candidate_count={len(candidate_units)}"
        )
    return {
        "raw_input_rows": int(len(indicators)),
        "candidate_rows": int(len(candidate_units)),
        "missing_candidate_ids": int(len(missing)),
        "extra_raw_ids": int(len(extra)),
        "raw_duplicate_id_count": 0,
        "candidate_duplicate_id_count": 0,
        "ids_reconcile_exactly": True,
    }


def direct_scale(values: pd.Series | np.ndarray | list[float]) -> np.ndarray:
    """Map a validated [0, 1] ratio directly to the [0, 100] score scale."""

    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.empty:
        raise EcologicalNetworkScoreError("At least one selected raw observation is required")
    _validate_fraction(numeric, SCORING_INPUT)
    return numeric.to_numpy(dtype=float) * 100.0


def calculate_scores(indicators: pd.DataFrame) -> pd.DataFrame:
    """Calculate and deterministically order the narrow component table."""

    validate_raw_indicators(indicators)
    selected = pd.to_numeric(indicators[SCORING_INPUT], errors="raise")
    result = pd.DataFrame(
        {
            "hex_id": indicators["hex_id"].astype(str),
            SCORING_INPUT: selected.to_numpy(dtype=float),
            "ecological_network_score": direct_scale(selected),
        }
    )
    if BOUNDARY_FLAG in indicators.columns:
        result[BOUNDARY_FLAG] = _normalise_boundary_flags(indicators[BOUNDARY_FLAG]).to_numpy()
    else:
        result[BOUNDARY_FLAG] = False
    result = result.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    if result["hex_id"].duplicated().any():
        raise EcologicalNetworkScoreError("Component output contains duplicate hex_id values")
    scores = result["ecological_network_score"].to_numpy(dtype=float)
    if not np.all(np.isfinite(scores)) or np.any((scores < 0) | (scores > 100)):
        raise EcologicalNetworkScoreError(
            "Ecological Network scores must be finite and in [0, 100]"
        )
    return result[list(OUTPUT_COLUMNS)]


def _distribution(values: pd.Series) -> dict[str, float]:
    numeric = pd.to_numeric(values, errors="raise")
    quantiles = numeric.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
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


def _support_distribution(values: pd.Series) -> dict[str, float]:
    summary = _distribution(values)
    return {key: summary[key] for key in ("min", "p10", "median", "p90")}


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
    numeric = pd.to_numeric(values, errors="raise")
    masks = {
        "0": numeric == 0,
        ">0–10": (numeric > 0) & (numeric <= 10),
        ">10–25": (numeric > 10) & (numeric <= 25),
        ">25–50": (numeric > 25) & (numeric <= 50),
        ">50–75": (numeric > 50) & (numeric <= 75),
        ">75–90": (numeric > 75) & (numeric <= 90),
        ">90": numeric > 90,
    }
    return {label: int(mask.sum()) for label, mask in masks.items()}


def _band_masks(values: pd.Series) -> dict[str, pd.Series]:
    return {
        "0–25": values.between(0, 25, inclusive="both"),
        ">25–50": (values > 25) & (values <= 50),
        ">50–75": (values > 50) & (values <= 75),
        ">75–100": (values > 75) & (values <= 100),
    }


def _joint_band_matrix(joined: pd.DataFrame) -> dict[str, Any]:
    habitat_masks = _band_masks(joined[HABITAT_SCORE])
    network_masks = _band_masks(joined["ecological_network_score"])
    counts = {
        habitat_band: {
            network_band: int((habitat_mask & network_mask).sum())
            for network_band, network_mask in network_masks.items()
        }
        for habitat_band, habitat_mask in habitat_masks.items()
    }
    return {
        "habitat_bands": list(SCORE_BANDS),
        "network_bands": list(SCORE_BANDS),
        "counts": counts,
    }


def _tail_support(joined: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for threshold in (75, 90):
        subset = joined.loc[joined["ecological_network_score"] >= threshold]
        result[str(threshold)] = {
            "candidate_count": int(len(subset)),
            "neighbor_habitat_mean": _support_distribution(subset[NEIGHBOR_SUPPORT]),
            "neighbor_habitat_mean_below": {
                "0.05": int((subset[NEIGHBOR_SUPPORT] < 0.05).sum()),
                "0.10": int((subset[NEIGHBOR_SUPPORT] < 0.10).sum()),
                "0.20": int((subset[NEIGHBOR_SUPPORT] < 0.20).sum()),
            },
            "habitat_context_score": _distribution(subset[HABITAT_SCORE]),
        }
    return result


def _top_tail_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for threshold in (75, 90, 95):
        mask = joined["ecological_network_score"] >= threshold
        subset = joined.loc[mask]
        edge_count = int(subset[BOUNDARY_FLAG].sum())
        result[str(threshold)] = {
            "score_threshold": threshold,
            "candidate_count": int(len(subset)),
            "boundary_edge_count": edge_count,
            "boundary_edge_percent": float(100 * edge_count / len(subset)) if len(subset) else None,
            "median_habitat_context_score": float(subset[HABITAT_SCORE].median())
            if len(subset)
            else None,
            "median_neighbor_habitat_mean": float(subset[NEIGHBOR_SUPPORT].median())
            if len(subset)
            else None,
        }
    return result


def _informative_contrasts(joined: pd.DataFrame) -> dict[str, int]:
    habitat = joined[HABITAT_SCORE]
    network = joined["ecological_network_score"]
    masks = {
        "high_habitat_high_network": (habitat >= 75) & (network >= 75),
        "high_habitat_low_network": (habitat >= 75) & (network <= 25),
        "low_habitat_high_network": (habitat <= 25) & (network >= 75),
        "low_habitat_low_network": (habitat <= 25) & (network <= 25),
    }
    return {label: int(mask.sum()) for label, mask in masks.items()}


def _selected_vs_rejected(joined: pd.DataFrame) -> list[dict[str, Any]]:
    reasons = {
        SCORING_INPUT: (
            "Normalized configuration can still be high with low absolute habitat; the audit "
            "keeps that behavior visible and finds less pathological variation than the dominant pair."
        ),
        DOMINANT_DIAGNOSTIC: (
            "Top values often come from one small symmetrical opposing pair and reward single-axis "
            "concentration; retained as a diagnostic only."
        ),
        "bridge_strength_max": (
            "Absolute amount-driven signal remained strongly redundant with Habitat Context; diagnostic only."
        ),
        "bridge_strength_mean": (
            "Absolute amount-driven signal remained strongly redundant with Habitat Context; diagnostic only."
        ),
    }
    status = {
        SCORING_INPUT: "selected sole scoring input",
        DOMINANT_DIAGNOSTIC: "rejected; supporting raw diagnostic",
        "bridge_strength_max": "rejected; supporting raw diagnostic",
        "bridge_strength_mean": "rejected; supporting raw diagnostic",
    }
    result = []
    for indicator in (SCORING_INPUT, DOMINANT_DIAGNOSTIC, *ABSOLUTE_DIAGNOSTICS):
        result.append(
            {
                "indicator": indicator,
                "correlation_with_habitat_context": {
                    "habitat_context_local_fraction": _correlation(
                        joined[indicator], joined[HABITAT_LOCAL]
                    ),
                    "habitat_context_score": _correlation(joined[indicator], joined[HABITAT_SCORE]),
                },
                "major_low_habitat_pathology": reasons[indicator],
                "final_model_status": status[indicator],
            }
        )
    return result


def _validate_habitat_context(
    habitat: pd.DataFrame, candidate_ids: set[str], *, scored: bool
) -> None:
    required = ("hex_id", HABITAT_SCORE) if scored else ("hex_id", HABITAT_LOCAL)
    _require_columns(habitat, required, "Habitat Context artifact")
    ids = _normalise_ids(habitat, "Habitat Context component")
    if set(ids) != candidate_ids or len(ids) != len(candidate_ids):
        raise EcologicalNetworkScoreError(
            "Habitat Context and candidate IDs do not reconcile exactly"
        )
    if not scored:
        _validate_fraction(habitat[HABITAT_LOCAL], HABITAT_LOCAL)
    else:
        scores = _numeric(habitat[HABITAT_SCORE], HABITAT_SCORE)
        if np.any((scores < 0) | (scores > 100)):
            raise EcologicalNetworkScoreError(f"{HABITAT_SCORE} must lie in [0, 100]")


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


def build_ecological_network_score(
    raw_indicator_path: Path = RAW_INDICATOR_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    habitat_context_raw_path: Path = HABITAT_CONTEXT_RAW_PATH,
    habitat_context_score_path: Path = HABITAT_CONTEXT_SCORE_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read, validate, score, audit, and write the final network component."""

    start = time.perf_counter()
    try:
        raw = pd.read_csv(raw_indicator_path)
        candidates = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
        habitat_raw = pd.read_csv(habitat_context_raw_path)
        habitat_score = pd.read_csv(habitat_context_score_path)
    except (OSError, ValueError) as exc:
        raise EcologicalNetworkScoreError(f"Could not read Step 12 input artifact: {exc}") from exc

    validate_raw_indicators(raw, expected_candidate_count=len(candidates))
    reconciliation = validate_candidate_reconciliation(raw, candidates)
    scored = calculate_scores(raw)
    candidate_ids = set(candidates["hex_id"].astype(str))
    output_ids = set(scored["hex_id"])
    missing_ids = candidate_ids - output_ids
    extra_ids = output_ids - candidate_ids
    if (
        len(scored) != len(candidates)
        or missing_ids
        or extra_ids
        or scored["hex_id"].duplicated().any()
    ):
        raise EcologicalNetworkScoreError(
            "Ecological Network component output does not contain exactly one score per candidate"
        )

    _validate_habitat_context(habitat_raw, candidate_ids, scored=False)
    _validate_habitat_context(habitat_score, candidate_ids, scored=True)
    if set(habitat_raw["hex_id"].astype(str)) != set(habitat_score["hex_id"].astype(str)):
        raise EcologicalNetworkScoreError(
            "Habitat Context raw and scored IDs do not reconcile exactly"
        )

    raw_by_id = raw.copy()
    raw_by_id["hex_id"] = raw_by_id["hex_id"].astype(str)
    raw_by_id = raw_by_id.set_index("hex_id").loc[scored["hex_id"]]
    candidate_by_id = candidates.copy()
    candidate_by_id["hex_id"] = candidate_by_id["hex_id"].astype(str)
    candidate_by_id = candidate_by_id.set_index("hex_id").loc[scored["hex_id"]]
    habitat_raw_by_id = habitat_raw.copy()
    habitat_raw_by_id["hex_id"] = habitat_raw_by_id["hex_id"].astype(str)
    habitat_raw_by_id = habitat_raw_by_id.set_index("hex_id").loc[scored["hex_id"]]
    habitat_score_by_id = habitat_score.copy()
    habitat_score_by_id["hex_id"] = habitat_score_by_id["hex_id"].astype(str)
    habitat_score_by_id = habitat_score_by_id.set_index("hex_id").loc[scored["hex_id"]]

    joined = pd.DataFrame(
        {
            "hex_id": scored["hex_id"].to_numpy(),
            SCORING_INPUT: scored[SCORING_INPUT].to_numpy(dtype=float),
            "ecological_network_score": scored["ecological_network_score"].to_numpy(dtype=float),
            BOUNDARY_FLAG: scored[BOUNDARY_FLAG].to_numpy(dtype=bool),
            DOMINANT_DIAGNOSTIC: raw_by_id[DOMINANT_DIAGNOSTIC].to_numpy(dtype=float),
            "bridge_strength_max": raw_by_id["bridge_strength_max"].to_numpy(dtype=float),
            "bridge_strength_mean": raw_by_id["bridge_strength_mean"].to_numpy(dtype=float),
            NEIGHBOR_SUPPORT: raw_by_id[NEIGHBOR_SUPPORT].to_numpy(dtype=float),
            HABITAT_LOCAL: habitat_raw_by_id[HABITAT_LOCAL].to_numpy(dtype=float),
            HABITAT_SCORE: habitat_score_by_id[HABITAT_SCORE].to_numpy(dtype=float),
            **{
                field: candidate_by_id[field].to_numpy(dtype=float)
                for field in CANDIDATE_CORRELATION_FIELDS
            },
        }
    )

    exact_linear = bool(
        np.allclose(
            joined["ecological_network_score"].to_numpy(),
            joined[SCORING_INPUT].to_numpy() * 100.0,
            rtol=0.0,
            atol=1e-12,
        )
    )
    raw_order = np.argsort(joined[SCORING_INPUT].to_numpy(), kind="stable")
    score_order = np.argsort(joined["ecological_network_score"].to_numpy(), kind="stable")
    monotonic = bool(
        np.all(np.diff(joined["ecological_network_score"].to_numpy()[raw_order]) >= -1e-12)
    )
    same_order = bool(np.array_equal(raw_order, score_order))
    validation = {
        "selected_raw_present_and_finite": True,
        "selected_raw_in_0_1": True,
        "supporting_diagnostic_present": True,
        "boundary_flag_valid": True,
        "exact_score_formula_passed": exact_linear,
        "monotonicity_passed": monotonic,
        "raw_and_score_order_identical_without_ranking": same_order,
        "score_bounds_passed": bool(
            np.all(np.isfinite(joined["ecological_network_score"]))
            and np.all(joined["ecological_network_score"].between(0, 100, inclusive="both"))
        ),
        "zero_maps_to_zero": bool(
            np.all(joined.loc[joined[SCORING_INPUT] == 0, "ecological_network_score"] == 0)
        ),
        "synthetic_one_maps_to_100": bool(direct_scale([1.0])[0] == 100.0),
        "percentile_or_rank_transformation_used": False,
        "no_habitat_amount_support_threshold": True,
    }
    positive_checks = [
        value
        for key, value in validation.items()
        if key != "percentile_or_rank_transformation_used"
    ]
    if not all(positive_checks) or validation["percentile_or_rank_transformation_used"]:
        raise EcologicalNetworkScoreError(f"Step 12 validation failed: {validation}")

    boundary = joined[BOUNDARY_FLAG]
    distribution = {
        "raw_input": _distribution(joined[SCORING_INPUT]),
        "score": _distribution(joined["ecological_network_score"]),
    }
    habitat_relationship = {
        "ecological_network_score_vs_habitat_context_local_fraction": _correlation(
            joined["ecological_network_score"], joined[HABITAT_LOCAL]
        ),
        "ecological_network_score_vs_habitat_context_score": _correlation(
            joined["ecological_network_score"], joined[HABITAT_SCORE]
        ),
    }
    candidate_relationship = {
        field: _correlation(joined["ecological_network_score"], joined[field])
        for field in CANDIDATE_CORRELATION_FIELDS
    }
    provenance: dict[str, Any] = {
        "component_name": "Ecological Network Context",
        "status": "IMPLEMENTED FOR MVP",
        "source": {
            "raw_indicator_path": str(raw_indicator_path),
            "candidate_source_path": str(candidate_units_path),
            "candidate_source_layer": CANDIDATE_UNITS_LAYER,
            "habitat_context_raw_path": str(habitat_context_raw_path),
            "habitat_context_score_path": str(habitat_context_score_path),
        },
        "scoring": {
            "selected_raw_field": SCORING_INPUT,
            "formula": "ecological_network_score = 100 * opposing_balance_ratio",
            "direction": "higher is better",
            "score_range": [0, 100],
            "raw_range": [0, 1],
            "method": "direct bounded scaling; no percentile or rank transformation",
            "reason_direct_scaling_over_percentile": (
                "The selected raw input is already a normalized structural ratio with fixed lower "
                "bound 0, fixed upper bound 1, and direct interpretation between those bounds. "
                "Multiplication by 100 preserves that meaning; percentile ranking would replace it "
                "with population-relative standing."
            ),
        },
        "rejected_diagnostic_indicators": {
            "bridge_strength_max": (
                "Rejected for scoring because the absolute bridge metric remained strongly redundant "
                "with Habitat Context and is primarily amount-driven."
            ),
            "bridge_strength_mean": (
                "Rejected for scoring because the absolute bridge metric remained strongly redundant "
                "with Habitat Context and is primarily amount-driven."
            ),
            "dominant_opposing_pair_share": (
                "Rejected for scoring because high values were too easily produced by one small but "
                "symmetrical opposing pair, disproportionately rewarding single-axis concentration."
            ),
        },
        "candidate_population_validation": {
            **reconciliation,
            "output_rows": int(len(scored)),
            "output_duplicate_id_count": int(scored["hex_id"].duplicated().sum()),
            "output_missing_candidate_ids": int(len(missing_ids)),
            "output_extra_candidate_ids": int(len(extra_ids)),
            "one_score_per_candidate": True,
        },
        "raw_input_distribution": distribution["raw_input"],
        "score_distribution": distribution["score"],
        "score_range_bins": _range_bin_counts(joined["ecological_network_score"]),
        "transformation_validation": validation,
        "habitat_context_relationship": habitat_relationship,
        "joint_component_distribution": _joint_band_matrix(joined),
        "informative_contrast_groups": _informative_contrasts(joined),
        "low_habitat_high_network_diagnostics": _tail_support(joined),
        "top_network_tail_diagnostics": _top_tail_diagnostics(joined),
        "candidate_composition_relationship": candidate_relationship,
        "selected_vs_rejected_comparison": _selected_vs_rejected(joined),
        "boundary_handling": {
            "boundary_flag_field": BOUNDARY_FLAG,
            "boundary_edge_count": int(boundary.sum()),
            "boundary_edge_percent": float(100 * boundary.mean()),
            "retained_in_output": True,
            "used_in_score": False,
            "excluded_or_penalized": False,
            "county_edge_limitation": (
                "County-edge truncation is retained as an MVP limitation; missing neighboring "
                "positions are assigned zero for this raw network proxy and are not imputed."
            ),
        },
        "interpretation": (
            "An Ecological Network Context score of 80 means approximately 80% of the immediate "
            "habitat amount around the candidate is matched by habitat on the opposite side of its "
            "corresponding hex-grid axes under this structural proxy. It does not mean 80% ecological "
            "connectivity, corridor quality, movement probability, habitat quality, or restoration suitability."
        ),
        "model_separation": {
            "network_score_measures": "habitat configuration",
            "habitat_context_score_measures": "surrounding habitat amount",
            "habitat_amount_support_threshold_imposed": False,
            "habitat_context_multiplier_or_penalty": False,
        },
        "caveats": [
            "This is a transparent landscape-configuration proxy, not a validated ecological-connectivity metric.",
            "The metric is imposed by the 500 m hex-grid geometry and its three opposing axes.",
            "It is structural rather than functional or species-specific connectivity.",
            "No habitat-amount support threshold, nonlinear adjustment, or Habitat Context multiplier is imposed.",
            "County-edge truncation remains an MVP limitation.",
        ],
        "output": {
            "path": str(output_path),
            "columns": list(OUTPUT_COLUMNS),
            "row_count": int(len(scored)),
            "deterministic_order": "ascending hex_id",
            "no_geometry_or_candidate_composition_copied": True,
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
    """Generate the real-data final network component and print its audit."""

    scored, provenance = build_ecological_network_score()
    print(
        f"Ecological Network Context score: {provenance['output']['path']} "
        f"({provenance['output']['size_bytes']:,} bytes, {len(scored):,} rows)"
    )
    print(
        "Candidate/output reconciliation:",
        json.dumps(provenance["candidate_population_validation"]),
    )
    print("Score distribution:", json.dumps(provenance["score_distribution"], ensure_ascii=False))
    print("Score bins:", json.dumps(provenance["score_range_bins"], ensure_ascii=False))
    print("Transformation validation:", json.dumps(provenance["transformation_validation"]))
    print("Habitat Context correlations:", json.dumps(provenance["habitat_context_relationship"]))
    print("Joint Habitat-vs-Network bands:")
    print(json.dumps(provenance["joint_component_distribution"], ensure_ascii=False, indent=2))
    print("Informative contrast groups:", json.dumps(provenance["informative_contrast_groups"]))
    print(
        "Low-habitat/high-network diagnostics:",
        json.dumps(provenance["low_habitat_high_network_diagnostics"], ensure_ascii=False),
    )
    print("Top network tails:", json.dumps(provenance["top_network_tail_diagnostics"]))
    print(
        "Candidate-composition correlations:",
        json.dumps(provenance["candidate_composition_relationship"]),
    )
    print("Selected-vs-rejected comparison:")
    print(json.dumps(provenance["selected_vs_rejected_comparison"], ensure_ascii=False, indent=2))
    print(
        f"Provenance: {provenance['provenance_output']['path']} ({provenance['provenance_output']['size_bytes']:,} bytes)"
    )
    print("Status: IMPLEMENTED FOR MVP")


if __name__ == "__main__":
    main()
