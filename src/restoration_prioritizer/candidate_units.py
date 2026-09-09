"""Materialize the fixed MVP candidate analysis-unit population.

Step 6 provides the factual full terrestrial analysis-unit grid. This module
derives a separate candidate population using exactly two inclusive conditions:
at least 50,000 m² of NMD class-3 arable land and at least 25% arable land of
the unit's terrestrial NMD pixels. It deliberately stops before context
indicators, normalization, or prioritization.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

from restoration_prioritizer.config import TARGET_CRS

SOURCE_PATH = Path("data/processed/analysis_units.gpkg")
SOURCE_LAYER = "analysis_units"
OUTPUT_PATH = Path("data/processed/candidate_units.gpkg")
OUTPUT_LAYER = "candidate_units"
PROVENANCE_PATH = Path("data/processed/candidate_units.provenance.json")

MIN_CANDIDATE_AREA_M2 = 50_000.0
MIN_CANDIDATE_AREA_HA = MIN_CANDIDATE_AREA_M2 / 10_000.0
MIN_CANDIDATE_FRACTION_OF_TERRESTRIAL = 0.25
STEP6_PIXEL_AREA_M2 = 100.0

REQUIRED_FIELDS = (
    "hex_id",
    "grid_col",
    "grid_row",
    "terrestrial_pixels",
    "terrestrial_area_m2",
    "terrestrial_fraction",
    "candidate_pixels",
    "candidate_area_m2",
    "candidate_fraction_of_terrestrial",
    "habitat_context_pixels",
    "habitat_context_area_m2",
    "habitat_context_fraction_of_terrestrial",
    "wetland_context_pixels",
    "wetland_context_area_m2",
    "inland_water_pixels",
    "inland_water_area_m2",
    "artificial_constraint_pixels",
    "artificial_constraint_area_m2",
    "transitional_forest_pixels",
    "transitional_forest_area_m2",
    "peat_extraction_pixels",
    "peat_extraction_area_m2",
    "sea_pixels",
    "sea_area_m2",
)

PIXEL_COUNT_FIELDS = (
    "terrestrial_pixels",
    "candidate_pixels",
    "habitat_context_pixels",
    "wetland_context_pixels",
    "inland_water_pixels",
    "artificial_constraint_pixels",
    "transitional_forest_pixels",
    "peat_extraction_pixels",
    "sea_pixels",
)

AREA_FIELDS = (
    "terrestrial_area_m2",
    "candidate_area_m2",
    "habitat_context_area_m2",
    "wetland_context_area_m2",
    "inland_water_area_m2",
    "artificial_constraint_area_m2",
    "transitional_forest_area_m2",
    "peat_extraction_area_m2",
    "sea_area_m2",
)


class CandidateUnitsError(ValueError):
    """Raised when the Step 6 analysis-unit input is malformed."""


def _require_fields(units: gpd.GeoDataFrame) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in units.columns]
    if missing:
        raise CandidateUnitsError(f"Analysis-unit input is missing required fields: {missing}")


def _validate_numeric_fields(units: gpd.GeoDataFrame) -> None:
    for field in (*PIXEL_COUNT_FIELDS, *AREA_FIELDS):
        values = units[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)):
            raise CandidateUnitsError(f"Analysis-unit field {field} contains non-finite values")
        if np.any(values < 0):
            raise CandidateUnitsError(f"Analysis-unit field {field} contains negative values")

    terrestrial_pixels = units["terrestrial_pixels"].to_numpy(dtype=float)
    candidate_pixels = units["candidate_pixels"].to_numpy(dtype=float)
    if np.any(terrestrial_pixels <= 0):
        raise CandidateUnitsError("Analysis-unit terrestrial_pixels must be positive")
    if np.any(candidate_pixels > terrestrial_pixels):
        raise CandidateUnitsError("Candidate pixels exceed terrestrial pixels")

    candidate_area = units["candidate_area_m2"].to_numpy(dtype=float)
    if not np.allclose(
        candidate_area,
        candidate_pixels * STEP6_PIXEL_AREA_M2,
        rtol=0,
        atol=1e-6,
    ):
        raise CandidateUnitsError("candidate_area_m2 is inconsistent with candidate_pixels")

    candidate_fraction = units["candidate_fraction_of_terrestrial"].to_numpy(dtype=float)
    if not np.all(np.isfinite(candidate_fraction)) or np.any(
        (candidate_fraction < 0) | (candidate_fraction > 1)
    ):
        raise CandidateUnitsError("candidate_fraction_of_terrestrial must lie in [0, 1]")
    if not np.allclose(
        candidate_fraction,
        candidate_pixels / terrestrial_pixels,
        rtol=0,
        atol=1e-12,
    ):
        raise CandidateUnitsError(
            "candidate_fraction_of_terrestrial is inconsistent with candidate and terrestrial pixels"
        )

    terrestrial_fraction = units["terrestrial_fraction"].to_numpy(dtype=float)
    if not np.all(np.isfinite(terrestrial_fraction)) or np.any(terrestrial_fraction < 0):
        raise CandidateUnitsError("terrestrial_fraction must be finite and non-negative")


def validate_analysis_units(units: gpd.GeoDataFrame) -> None:
    """Validate the Step 6 schema, CRS, geometry, and factual attributes."""

    if not isinstance(units, gpd.GeoDataFrame):
        raise CandidateUnitsError("Analysis-unit input must be a GeoDataFrame")
    if units.geometry.name not in units.columns:
        raise CandidateUnitsError("Analysis-unit input has no geometry column")
    if units.crs is None or str(units.crs) != TARGET_CRS:
        raise CandidateUnitsError(f"Analysis-unit CRS is {units.crs}; expected {TARGET_CRS}")
    _require_fields(units)
    if units["hex_id"].isna().any() or units["hex_id"].duplicated().any():
        raise CandidateUnitsError("hex_id must be non-null and unique")
    if units.geometry.isna().any() or units.geometry.is_empty.any():
        raise CandidateUnitsError("Analysis-unit geometries must be non-empty")
    if (~units.geometry.is_valid).any():
        raise CandidateUnitsError("Analysis-unit geometries must be valid")
    _validate_numeric_fields(units)


def eligibility_mask(units: gpd.GeoDataFrame) -> np.ndarray:
    """Return the fixed inclusive MVP eligibility mask."""

    validate_analysis_units(units)
    return (units["candidate_area_m2"].to_numpy(dtype=float) >= MIN_CANDIDATE_AREA_M2) & (
        units["candidate_fraction_of_terrestrial"].to_numpy(dtype=float)
        >= MIN_CANDIDATE_FRACTION_OF_TERRESTRIAL
    )


def _percentage(numerator: float, denominator: float) -> float:
    return float(100.0 * numerator / denominator) if denominator else 0.0


def _quantiles(values: np.ndarray, include_min_max: bool = True) -> dict[str, float | None]:
    if values.size == 0:
        result: dict[str, float | None] = {
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
        }
        return result
    quantile_values = np.quantile(values, [0.10, 0.25, 0.50, 0.75, 0.90])
    result = {
        "min": float(np.min(values)) if include_min_max else None,
        "p10": float(quantile_values[0]),
        "p25": float(quantile_values[1]),
        "median": float(quantile_values[2]),
        "p75": float(quantile_values[3]),
        "p90": float(quantile_values[4]),
        "max": float(np.max(values)) if include_min_max else None,
    }
    return result


def _candidate_area_quantiles(candidate_area_m2: np.ndarray) -> dict[str, Any]:
    m2 = _quantiles(candidate_area_m2)
    hectares = {key: (value / 10_000.0 if value is not None else None) for key, value in m2.items()}
    return {"m2": m2, "ha": hectares}


def _spatial_sanity(candidate_units: gpd.GeoDataFrame) -> dict[str, Any]:
    if candidate_units.empty:
        return {"bounding_box_epsg_3006": None, "centroid_thirds": {}}
    min_x, min_y, max_x, max_y = (float(value) for value in candidate_units.total_bounds)
    centroids = candidate_units.geometry.centroid
    x_values = centroids.x.to_numpy(dtype=float)
    y_values = centroids.y.to_numpy(dtype=float)
    x_edges = np.linspace(min_x, max_x, 4)
    y_edges = np.linspace(min_y, max_y, 4)
    x_bins = np.clip(np.digitize(x_values, x_edges[1:-1], right=False), 0, 2)
    y_bins = np.clip(np.digitize(y_values, y_edges[1:-1], right=False), 0, 2)
    thirds = {
        f"x_third_{x_index + 1},y_third_{y_index + 1}": int(
            np.count_nonzero((x_bins == x_index) & (y_bins == y_index))
        )
        for x_index in range(3)
        for y_index in range(3)
    }
    return {
        "bounding_box_epsg_3006": {
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
            "width_m": max_x - min_x,
            "height_m": max_y - min_y,
        },
        "centroid_thirds": thirds,
        "note": "Descriptive coordinate-bin sanity check; no spatial eligibility condition.",
    }


def calculate_diagnostics(
    units: gpd.GeoDataFrame, eligible: np.ndarray | None = None
) -> dict[str, Any]:
    """Calculate the Step 7 population, accounting, and distribution diagnostics."""

    validate_analysis_units(units)
    if eligible is None:
        eligible = eligibility_mask(units)
    eligible = np.asarray(eligible, dtype=bool)
    if eligible.shape != (len(units),):
        raise CandidateUnitsError("Eligibility mask length does not match analysis-unit input")

    candidate_pixels = units["candidate_pixels"].to_numpy(dtype=np.int64)
    candidate_area_m2 = units["candidate_area_m2"].to_numpy(dtype=float)
    candidate_fraction = units["candidate_fraction_of_terrestrial"].to_numpy(dtype=float)
    terrestrial_fraction = units["terrestrial_fraction"].to_numpy(dtype=float)
    terrestrial_area_m2 = units["terrestrial_area_m2"].to_numpy(dtype=float)
    any_arable = candidate_pixels > 0
    area_pass = candidate_area_m2 >= MIN_CANDIDATE_AREA_M2
    fraction_pass = candidate_fraction >= MIN_CANDIDATE_FRACTION_OF_TERRESTRIAL

    failure_masks = {
        "area_below_5ha_only": any_arable & ~area_pass & fraction_pass,
        "fraction_below_25_percent_only": any_arable & area_pass & ~fraction_pass,
        "both_area_and_fraction_fail": any_arable & ~area_pass & ~fraction_pass,
        "pass_both": any_arable & area_pass & fraction_pass,
    }
    reason_total = sum(int(mask.sum()) for mask in failure_masks.values())
    if reason_total != int(any_arable.sum()):
        raise CandidateUnitsError("Eligibility failure-reason categories are not exhaustive")

    eligible_units = units.loc[eligible].copy()
    all_arable_area = float(candidate_area_m2.sum())
    retained_arable_area = float(candidate_area_m2[eligible].sum())
    excluded_arable_area = float(candidate_area_m2[~eligible].sum())
    if not math.isclose(
        retained_arable_area + excluded_arable_area,
        all_arable_area,
        rel_tol=0,
        abs_tol=1e-6,
    ):
        raise CandidateUnitsError("Eligible and excluded arable areas do not reconcile")

    all_terrestrial_area = float(terrestrial_area_m2.sum())
    retained_terrestrial_area = float(terrestrial_area_m2[eligible].sum())
    edge_cases = {
        "eligible_terrestrial_fraction_below_25_percent": int(
            np.count_nonzero(eligible & (terrestrial_fraction < 0.25))
        ),
        "eligible_terrestrial_fraction_below_50_percent": int(
            np.count_nonzero(eligible & (terrestrial_fraction < 0.50))
        ),
        "eligible_terrestrial_fraction_below_75_percent": int(
            np.count_nonzero(eligible & (terrestrial_fraction < 0.75))
        ),
        "eligible_containing_sea_pixels": int(
            np.count_nonzero(eligible & (units["sea_pixels"].to_numpy(dtype=float) > 0))
        ),
        "eligible_containing_inland_water_pixels": int(
            np.count_nonzero(eligible & (units["inland_water_pixels"].to_numpy(dtype=float) > 0))
        ),
        "eligible_containing_artificial_constraint_pixels": int(
            np.count_nonzero(
                eligible & (units["artificial_constraint_pixels"].to_numpy(dtype=float) > 0)
            )
        ),
        "eligible_containing_habitat_context_pixels": int(
            np.count_nonzero(eligible & (units["habitat_context_pixels"].to_numpy(dtype=float) > 0))
        ),
        "eligible_containing_wetland_context_pixels": int(
            np.count_nonzero(eligible & (units["wetland_context_pixels"].to_numpy(dtype=float) > 0))
        ),
    }
    candidate_pixel_conservation = {
        "eligible_candidate_pixels": int(candidate_pixels[eligible].sum()),
        "excluded_terrestrial_candidate_pixels": int(candidate_pixels[~eligible].sum()),
        "full_analysis_unit_candidate_pixels": int(candidate_pixels.sum()),
        "matches_exactly": int(candidate_pixels[eligible].sum() + candidate_pixels[~eligible].sum())
        == int(candidate_pixels.sum()),
    }
    if not candidate_pixel_conservation["matches_exactly"]:
        raise CandidateUnitsError("Eligible and excluded candidate pixels do not reconcile")

    candidate_fraction_eligible = candidate_fraction[eligible]
    candidate_area_eligible = candidate_area_m2[eligible]
    terrestrial_fraction_eligible = terrestrial_fraction[eligible]
    return {
        "population": {
            "total_terrestrial_analysis_units": int(len(units)),
            "units_containing_any_arable_land": int(any_arable.sum()),
            "eligible_candidate_units": int(eligible.sum()),
            "ineligible_units": int((~eligible).sum()),
            "ineligible_units_containing_some_arable_land": int((~eligible & any_arable).sum()),
        },
        "failure_reasons_among_units_with_any_arable": {
            key: int(mask.sum()) for key, mask in failure_masks.items()
        },
        "arable_area_retention": {
            "total_arable_area_m2": all_arable_area,
            "total_arable_area_ha": all_arable_area / 10_000.0,
            "eligible_arable_area_m2": retained_arable_area,
            "eligible_arable_area_ha": retained_arable_area / 10_000.0,
            "excluded_arable_area_m2": excluded_arable_area,
            "excluded_arable_area_ha": excluded_arable_area / 10_000.0,
            "eligible_percent_of_total": _percentage(retained_arable_area, all_arable_area),
            "excluded_percent_of_total": _percentage(excluded_arable_area, all_arable_area),
        },
        "terrestrial_area_context": {
            "total_terrestrial_area_m2": all_terrestrial_area,
            "total_terrestrial_area_ha": all_terrestrial_area / 10_000.0,
            "eligible_terrestrial_area_m2": retained_terrestrial_area,
            "eligible_terrestrial_area_ha": retained_terrestrial_area / 10_000.0,
            "eligible_population_share_percent": _percentage(
                retained_terrestrial_area, all_terrestrial_area
            ),
        },
        "eligible_population_quantiles": {
            "candidate_area": _candidate_area_quantiles(candidate_area_eligible),
            "candidate_fraction_of_terrestrial": _quantiles(candidate_fraction_eligible),
            "terrestrial_fraction": {
                key: value
                for key, value in _quantiles(terrestrial_fraction_eligible).items()
                if key in {"min", "p10", "median", "p90", "max"}
            },
        },
        "edge_case_diagnostics": edge_cases,
        "candidate_pixel_conservation": candidate_pixel_conservation,
        "spatial_sanity": _spatial_sanity(eligible_units),
    }


def _write_geopackage(candidate_units: gpd.GeoDataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.stem}.part{output_path.suffix}")
    temporary_path.unlink(missing_ok=True)
    candidate_units.to_file(temporary_path, layer=OUTPUT_LAYER, driver="GPKG", index=False)
    os.replace(temporary_path, output_path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_path, path)


def build_candidate_units(
    source_path: Path = SOURCE_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Load, validate, filter, write, and summarize the candidate population."""

    start = time.perf_counter()
    try:
        source_units = gpd.read_file(source_path, layer=SOURCE_LAYER)
    except (OSError, ValueError) as exc:
        raise CandidateUnitsError(
            f"Could not read Step 6 analysis units: {source_path}: {exc}"
        ) from exc
    validate_analysis_units(source_units)
    eligible = eligibility_mask(source_units)
    candidate_units = source_units.loc[eligible].copy().reset_index(drop=True)
    if candidate_units.empty:
        raise CandidateUnitsError("The approved eligibility rule produced no candidate units")
    validate_analysis_units(candidate_units)
    if not np.all(eligibility_mask(candidate_units)):
        raise CandidateUnitsError("Retained candidate units do not all satisfy both conditions")

    diagnostics = calculate_diagnostics(source_units, eligible)
    _write_geopackage(candidate_units, output_path)
    output_size = output_path.stat().st_size
    elapsed_seconds = time.perf_counter() - start
    provenance: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "methodological_note": (
            "Eligibility defines the MVP screening population to be ranked; it is a pragmatic "
            "domain definition, not ecological suitability. Eligible units are not claims of "
            "restorability, feasibility, ecological value, or availability."
        ),
        "source": {
            "path": str(source_path),
            "layer": SOURCE_LAYER,
            "feature_count": int(len(source_units)),
            "crs": str(source_units.crs),
        },
        "eligibility_definition": {
            "expression": (
                "candidate_area_m2 >= 50000.0 and candidate_fraction_of_terrestrial >= 0.25"
            ),
            "minimum_candidate_area_m2": MIN_CANDIDATE_AREA_M2,
            "minimum_candidate_area_ha": MIN_CANDIDATE_AREA_HA,
            "minimum_candidate_fraction_of_terrestrial": MIN_CANDIDATE_FRACTION_OF_TERRESTRIAL,
            "comparison_semantics": "Both comparisons are inclusive (>=); no epsilon is applied.",
            "primary_candidate_pixel": "NMD class 3 arable land",
            "additional_conditions": "None; no terrestrial-coverage or context filter is applied.",
        },
        "output": {
            "path": str(output_path),
            "layer": OUTPUT_LAYER,
            "feature_count": int(len(candidate_units)),
            "size_bytes": int(output_size),
            "fields": [column for column in candidate_units.columns if column != "geometry"],
        },
        **diagnostics,
        "runtime_seconds": elapsed_seconds,
    }
    _write_json(provenance_path, provenance)
    return candidate_units, provenance


def main() -> None:
    """Build the real-data candidate artifact and print a concise summary."""

    _, provenance = build_candidate_units()
    population = provenance["population"]
    retention = provenance["arable_area_retention"]
    output = provenance["output"]
    print(f"Candidate units: {output['path']} [{output['layer']}] ({output['size_bytes']:,} bytes)")
    print(
        f"Terrestrial units {population['total_terrestrial_analysis_units']:,}; "
        f"with arable {population['units_containing_any_arable_land']:,}; "
        f"eligible {population['eligible_candidate_units']:,}; "
        f"ineligible {population['ineligible_units']:,}"
    )
    print(
        f"Arable area retained {retention['eligible_percent_of_total']:.2f}% "
        f"({retention['eligible_arable_area_ha']:,.2f} ha); "
        f"excluded {retention['excluded_percent_of_total']:.2f}%"
    )
    print(
        f"Candidate pixels reconcile exactly: "
        f"{provenance['candidate_pixel_conservation']['full_analysis_unit_candidate_pixels']:,}"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
