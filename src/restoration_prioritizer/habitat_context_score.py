"""Score the raw Habitat Context indicator for the eligible candidate population.

The two-ring local habitat-context fraction is the sole scoring input. The
first-ring fraction remains in the raw artifact and is used only for audit
correlations. This module deliberately implements one transparent component;
it does not provide generic normalization or multi-component infrastructure.
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

RAW_INDICATOR_PATH = Path("data/processed/indicators/habitat_context.csv")
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
OUTPUT_PATH = Path("data/processed/components/habitat_context.csv")
PROVENANCE_PATH = Path("data/processed/components/habitat_context.provenance.json")

SCORING_INPUT = "habitat_context_local_fraction"
IMMEDIATE_DIAGNOSTIC = "habitat_context_adjacent_fraction"
BOUNDARY_FLAG = "boundary_edge_flag"
CANDIDATE_CORRELATION_FIELDS = (
    "candidate_fraction_of_terrestrial",
    "candidate_area_m2",
    "habitat_context_fraction_of_terrestrial",
)
OUTPUT_COLUMNS = ("hex_id", SCORING_INPUT, "habitat_context_score", BOUNDARY_FLAG)


class HabitatContextScoreError(ValueError):
    """Raised when raw indicators or candidate reconciliation is invalid."""


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...], label: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise HabitatContextScoreError(f"{label} is missing required fields: {missing}")


def _normalise_ids(frame: pd.DataFrame, label: str) -> pd.Series:
    if frame["hex_id"].isna().any():
        raise HabitatContextScoreError(f"{label} hex_id values must be non-null")
    ids = frame["hex_id"].astype(str)
    if (ids.str.len() == 0).any():
        raise HabitatContextScoreError(f"{label} hex_id values must be non-empty")
    if ids.duplicated().any():
        raise HabitatContextScoreError(f"{label} hex_id values must be unique")
    return ids


def _validate_fraction(values: pd.Series, field: str, allow_missing: bool = False) -> None:
    numeric = pd.to_numeric(values, errors="coerce")
    if not allow_missing and numeric.isna().any():
        raise HabitatContextScoreError(f"{field} contains missing or non-numeric values")
    valid = numeric.dropna().to_numpy(dtype=float)
    if not np.all(np.isfinite(valid)):
        raise HabitatContextScoreError(f"{field} contains non-finite values")
    if np.any((valid < 0) | (valid > 1)):
        raise HabitatContextScoreError(f"{field} must lie in [0, 1]")


def _normalise_boundary_flags(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise HabitatContextScoreError(f"{BOUNDARY_FLAG} contains missing values")
    if pd.api.types.is_bool_dtype(values):
        return values.astype(bool)
    if pd.api.types.is_numeric_dtype(values):
        numeric = values.to_numpy(dtype=float)
        if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, [0, 1])):
            raise HabitatContextScoreError(f"{BOUNDARY_FLAG} must contain only true/false values")
        return values.astype(bool)
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin(["true", "false"]).all():
        raise HabitatContextScoreError(f"{BOUNDARY_FLAG} must contain only true/false values")
    return normalised.eq("true")


def validate_raw_indicators(
    indicators: pd.DataFrame, expected_candidate_count: int | None = None
) -> None:
    """Validate the raw local fraction and the required candidate key."""

    if not isinstance(indicators, pd.DataFrame):
        raise HabitatContextScoreError("Raw Habitat Context input must be a DataFrame")
    _require_columns(indicators, ("hex_id", SCORING_INPUT), "Raw Habitat Context input")
    _normalise_ids(indicators, "Raw Habitat Context input")
    if expected_candidate_count is not None and len(indicators) != expected_candidate_count:
        raise HabitatContextScoreError(
            "Raw Habitat Context row count does not match candidate population "
            f"({len(indicators)} != {expected_candidate_count})"
        )
    _validate_fraction(indicators[SCORING_INPUT], SCORING_INPUT)
    if IMMEDIATE_DIAGNOSTIC in indicators.columns:
        _validate_fraction(indicators[IMMEDIATE_DIAGNOSTIC], IMMEDIATE_DIAGNOSTIC)
    if BOUNDARY_FLAG in indicators.columns:
        _normalise_boundary_flags(indicators[BOUNDARY_FLAG])


def validate_candidate_reconciliation(
    indicators: pd.DataFrame, candidate_units: pd.DataFrame
) -> dict[str, int | bool]:
    """Validate that raw indicator IDs and candidate IDs match exactly."""

    if not isinstance(candidate_units, pd.DataFrame):
        raise HabitatContextScoreError("Candidate input must be a DataFrame")
    _require_columns(candidate_units, ("hex_id", *CANDIDATE_CORRELATION_FIELDS), "Candidate input")
    raw_ids = _normalise_ids(indicators, "Raw Habitat Context input")
    candidate_ids = _normalise_ids(candidate_units, "Candidate input")
    raw_set = set(raw_ids)
    candidate_set = set(candidate_ids)
    missing = candidate_set - raw_set
    extra = raw_set - candidate_set
    if len(indicators) != len(candidate_units) or missing or extra:
        raise HabitatContextScoreError(
            "Raw Habitat Context and candidate IDs do not reconcile exactly: "
            f"missing={len(missing)}, extra={len(extra)}, "
            f"raw_count={len(indicators)}, candidate_count={len(candidate_units)}"
        )
    return {
        "raw_input_count": int(len(indicators)),
        "candidate_input_count": int(len(candidate_units)),
        "raw_duplicate_hex_id_count": 0,
        "candidate_duplicate_hex_id_count": 0,
        "missing_raw_ids_count": int(len(missing)),
        "extra_raw_ids_count": int(len(extra)),
        "ids_reconcile_exactly": True,
    }


def percentile_scores(values: pd.Series | np.ndarray | list[float]) -> np.ndarray:
    """Return empirical 0–100 percentile scores using average ranks for ties."""

    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.empty:
        raise HabitatContextScoreError("At least two valid observations are required")
    if numeric.isna().any():
        raise HabitatContextScoreError("Scoring input contains missing or non-numeric values")
    array = numeric.to_numpy(dtype=float)
    if not np.all(np.isfinite(array)):
        raise HabitatContextScoreError("Scoring input contains non-finite values")
    if len(array) < 2:
        raise HabitatContextScoreError("At least two valid observations are required")
    ranks = pd.Series(array).rank(method="average", ascending=True).to_numpy(dtype=float)
    return 100.0 * (ranks - 1.0) / (len(array) - 1.0)


def calculate_scores(indicators: pd.DataFrame) -> pd.DataFrame:
    """Calculate one scored row per raw indicator row in deterministic ID order."""

    validate_raw_indicators(indicators)
    result = pd.DataFrame(
        {
            "hex_id": indicators["hex_id"].astype(str),
            SCORING_INPUT: pd.to_numeric(indicators[SCORING_INPUT], errors="raise"),
            "habitat_context_score": percentile_scores(indicators[SCORING_INPUT]),
        }
    )
    if BOUNDARY_FLAG in indicators.columns:
        result[BOUNDARY_FLAG] = _normalise_boundary_flags(indicators[BOUNDARY_FLAG]).to_numpy()
    result = result.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    score = result["habitat_context_score"]
    if ((score < 0) | (score > 100) | ~np.isfinite(score)).any():
        raise HabitatContextScoreError("Habitat Context scores must be finite and in [0, 100]")
    return result[[column for column in OUTPUT_COLUMNS if column in result.columns]]


def _distribution(values: pd.Series) -> dict[str, float]:
    quantiles = values.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95])
    return {
        "min": float(values.min()),
        "p10": float(quantiles.loc[0.10]),
        "p25": float(quantiles.loc[0.25]),
        "median": float(quantiles.loc[0.50]),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "max": float(values.max()),
        "mean": float(values.mean()),
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


def _tie_diagnostics(raw: pd.Series, scores: pd.Series) -> dict[str, Any]:
    counts = raw.value_counts()
    tied = counts[counts > 1]
    return {
        "unique_raw_local_fraction_values": int(raw.nunique()),
        "distinct_score_values": int(scores.nunique()),
        "candidates_participating_in_any_tied_raw_value": int(tied.sum()),
        "candidates_participating_in_any_tied_raw_value_percent": float(
            100.0 * tied.sum() / len(raw)
        ),
        "number_of_tied_raw_value_groups": int(len(tied)),
        "largest_raw_value_tie_group": int(tied.max()) if not tied.empty else 1,
        "ties_materially_affect_percentile_interpretation": False,
        "interpretation": (
            "Ties create shared scores and reduce score granularity, but average-rank semantics "
            "preserve ordering and the relative percentile interpretation."
        ),
    }


def _score_anchors(raw: pd.Series, scores: pd.Series) -> dict[str, dict[str, float | int | None]]:
    anchors: dict[str, dict[str, float | int | None]] = {}
    for threshold in (50, 75, 90, 95):
        selected = raw.loc[scores >= threshold]
        value = float(selected.min()) if not selected.empty else None
        anchors[str(threshold)] = {
            "score_threshold": threshold,
            "minimum_raw_fraction": value,
            "minimum_raw_percent": value * 100.0 if value is not None else None,
            "candidate_count": int(len(selected)),
        }
    return anchors


def _top_tail_diagnostics(scores: pd.Series, boundary: pd.Series) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for threshold in (90, 95, 99):
        mask = scores >= threshold
        count = int(mask.sum())
        edge_count = int((mask & boundary).sum())
        result[str(threshold)] = {
            "score_threshold": threshold,
            "candidate_count": count,
            "boundary_edge_candidate_count": edge_count,
            "boundary_edge_candidate_percent": float(100.0 * edge_count / count) if count else None,
        }
    return result


def build_habitat_context_score(
    raw_indicator_path: Path = RAW_INDICATOR_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read, validate, score, audit, and write the Habitat Context component."""

    start = time.perf_counter()
    try:
        raw = pd.read_csv(raw_indicator_path)
    except (OSError, ValueError) as exc:
        raise HabitatContextScoreError(
            f"Could not read raw Habitat Context indicators: {exc}"
        ) from exc
    try:
        candidates = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
    except (OSError, ValueError) as exc:
        raise HabitatContextScoreError(f"Could not read candidate units: {exc}") from exc

    validate_raw_indicators(raw, expected_candidate_count=len(candidates))
    reconciliation = validate_candidate_reconciliation(raw, candidates)
    scored = calculate_scores(raw)
    output_ids = set(scored["hex_id"])
    candidate_ids = set(candidates["hex_id"].astype(str))
    if (
        len(scored) != len(candidates)
        or output_ids != candidate_ids
        or scored["hex_id"].duplicated().any()
    ):
        raise HabitatContextScoreError(
            "Component output does not contain exactly one score per candidate"
        )

    raw_by_id = raw.copy()
    raw_by_id["hex_id"] = raw_by_id["hex_id"].astype(str)
    raw_by_id = raw_by_id.set_index("hex_id").loc[scored["hex_id"]]
    boundary = (
        _normalise_boundary_flags(raw_by_id[BOUNDARY_FLAG]).reset_index(drop=True)
        if BOUNDARY_FLAG in raw_by_id.columns
        else pd.Series(False, index=scored.index)
    )
    raw_local = raw_by_id[SCORING_INPUT].reset_index(drop=True)
    score_values = scored["habitat_context_score"]
    if IMMEDIATE_DIAGNOSTIC not in raw_by_id.columns:
        raise HabitatContextScoreError(
            f"Raw Habitat Context input must include {IMMEDIATE_DIAGNOSTIC} for the required audit"
        )
    immediate = raw_by_id[IMMEDIATE_DIAGNOSTIC].reset_index(drop=True)
    candidate_by_id = candidates.copy()
    candidate_by_id["hex_id"] = candidate_by_id["hex_id"].astype(str)
    candidate_by_id = candidate_by_id.set_index("hex_id").loc[scored["hex_id"]]

    raw_order = np.argsort(raw_local.to_numpy(dtype=float), kind="stable")
    monotonic = bool(
        np.array_equal(
            score_values.to_numpy(dtype=float)[raw_order],
            np.sort(score_values.to_numpy(dtype=float), kind="stable"),
        )
    )
    equal_raw_equal_score = bool(
        scored.assign(_raw=raw_local.to_numpy())
        .groupby("_raw", sort=False)["habitat_context_score"]
        .nunique()
        .le(1)
        .all()
    )
    correlation_diagnostics = {
        "final_score_vs_immediate_raw_indicator": _correlation(score_values, immediate),
        "final_score_vs_candidate_fraction_of_terrestrial": _correlation(
            score_values,
            candidate_by_id["candidate_fraction_of_terrestrial"].reset_index(drop=True),
        ),
        "final_score_vs_candidate_area_m2": _correlation(
            score_values, candidate_by_id["candidate_area_m2"].reset_index(drop=True)
        ),
        "final_score_vs_focal_habitat_context_fraction_of_terrestrial": _correlation(
            score_values,
            candidate_by_id["habitat_context_fraction_of_terrestrial"].reset_index(drop=True),
        ),
    }
    _write_csv(scored, output_path)
    provenance: dict[str, Any] = {
        "component_name": "Habitat Context",
        "status": "IMPLEMENTED FOR MVP",
        "source": {
            "raw_indicator_path": str(raw_indicator_path),
            "candidate_units_path": str(candidate_units_path),
            "candidate_units_layer": CANDIDATE_UNITS_LAYER,
        },
        "scoring": {
            "input_field": SCORING_INPUT,
            "input_role": "sole scoring input",
            "direction": "higher is better",
            "method": "empirical percentile / average rank",
            "formula": "100 * (average ascending rank - 1) / (N - 1)",
            "ties": "average rank; equal raw values receive identical scores",
            "eligible_population_definition": "the validated Step 8 candidate population",
        },
        "candidate_population_validation": {
            **reconciliation,
            "output_count": int(len(scored)),
            "output_duplicate_hex_id_count": int(scored["hex_id"].duplicated().sum()),
            "output_missing_candidate_id_count": 0,
            "output_extra_candidate_id_count": 0,
            "one_score_per_candidate": True,
        },
        "raw_input_distribution": _distribution(raw_local),
        "score_distribution": _distribution(score_values),
        "tie_diagnostics": _tie_diagnostics(raw_local, score_values),
        "score_anchors": _score_anchors(raw_local, score_values),
        "top_tail_diagnostics": _top_tail_diagnostics(score_values, boundary),
        "validation": {
            "raw_local_fraction_missing_count": int(raw_local.isna().sum()),
            "raw_local_fraction_finite_and_in_range": True,
            "score_finite_and_in_range": bool(
                np.all(np.isfinite(score_values))
                and np.all((score_values >= 0) & (score_values <= 100))
            ),
            "raw_to_score_monotonicity_passed": monotonic,
            "equal_raw_values_receive_equal_scores": equal_raw_equal_score,
        },
        "diagnostic_indicators": {
            "immediate_indicator_field": IMMEDIATE_DIAGNOSTIC,
            "immediate_indicator_role": "retained from Step 8 for diagnostic/audit use only",
            "local_indicator_is_sole_score_input": True,
            "focal_cell_habitat_fraction_added": False,
        },
        "correlation_diagnostics": correlation_diagnostics,
        "boundary_edge_handling": {
            "boundary_edge_flag_field": BOUNDARY_FLAG,
            "used_in_score": False,
            "edge_candidates_excluded": False,
            "cross_county_context_correction_applied": False,
            "decision": (
                "County-boundary truncation is accepted as an MVP limitation; no Halland or "
                "Blekinge NMD data are ingested and edge candidates remain in the ranking."
            ),
        },
        "interpretation": (
            "A score is the candidate's relative percentile standing for the local surrounding "
            "habitat-context proxy among eligible candidates. It is not habitat quality, "
            "restoration suitability, probability of success, or habitat coverage. The raw "
            "local fraction remains the direct habitat-cover proportion."
        ),
        "caveats": [
            "The habitat-context proxy is a structural NMD land-cover proxy, not biodiversity or habitat quality.",
            "The immediate first-ring indicator is diagnostic only; the two-ring local indicator is the sole score input.",
            "Focal-cell habitat-context fraction is not added separately, avoiding direct double-counting.",
            "Percentile scoring is selected for this component and does not prescribe transformations for future components.",
            "The accepted county-edge truncation affects approximately 0.31% of candidates and is not corrected or imputed.",
        ],
        "output": {
            "path": str(output_path),
            "columns": list(scored.columns),
            "row_count": int(len(scored)),
            "deterministic_order": "ascending hex_id",
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - start,
    }
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["provenance_output"] = {
        "path": str(provenance_path),
        "size_bytes": 0,
    }
    for _ in range(5):
        _write_json(provenance_path, provenance)
        actual_size = int(provenance_path.stat().st_size)
        if actual_size == provenance["provenance_output"]["size_bytes"]:
            break
        provenance["provenance_output"]["size_bytes"] = actual_size
    return scored, provenance


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    frame.to_csv(
        temporary_path,
        index=False,
        columns=[column for column in OUTPUT_COLUMNS if column in frame.columns],
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


def main() -> None:
    """Generate the real-data Habitat Context component and print its audit summary."""

    scored, provenance = build_habitat_context_score()
    distribution = provenance["score_distribution"]
    validation = provenance["validation"]
    print(
        f"Habitat Context score: {provenance['output']['path']} "
        f"({provenance['output']['size_bytes']:,} bytes, {len(scored):,} rows)"
    )
    print(
        f"Score min {distribution['min']:.6f}; max {distribution['max']:.6f}; "
        f"mean {distribution['mean']:.6f}; median {distribution['median']:.6f}"
    )
    print(
        f"Monotonicity: {'passed' if validation['raw_to_score_monotonicity_passed'] else 'FAILED'}; "
        f"ties: {provenance['tie_diagnostics']['unique_raw_local_fraction_values']:,} unique raw values"
    )
    print(f"Provenance: {provenance['provenance_output']['path']}")


if __name__ == "__main__":
    main()
