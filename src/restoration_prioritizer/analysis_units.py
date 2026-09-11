"""Build deterministic 500 m hexagonal analysis units from the NMD raster.

This module creates regular pointy-top hexagons in EPSG:3006 and records factual
NMD composition for each hexagon that contains at least one terrestrial pixel.
It deliberately stops before candidate selection, spatial context indicators,
normalisation, or scoring.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import transform as window_transform
from shapely import STRtree
from shapely.geometry import Polygon, box

from restoration_prioritizer.config import NOMINAL_HEX_SIZE_METRES, TARGET_CRS
from restoration_prioritizer.nmd_semantics import (
    ARTIFICIAL_CONSTRAINT,
    HABITAT_CONTEXT_PROXY,
    INLAND_WATER_CONTEXT,
    NMD_RASTER_PATH,
    NMD_SEMANTIC_AUDIT_PATH,
    PEAT_EXTRACTION,
    PRIMARY_CANDIDATE,
    ROLE_FACTUAL_GROUPS,
    SEA,
    TERRESTRIAL_LAND,
    WETLAND_CONTEXT,
    factual_group_masks,
)

GRID_CRS = TARGET_CRS
OUTPUT_PATH = Path("data/processed/analysis_units.gpkg")
OUTPUT_LAYER = "analysis_units"
PROVENANCE_PATH = Path("data/processed/analysis_units.provenance.json")
STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"

HEX_FLAT_TO_FLAT_M = float(NOMINAL_HEX_SIZE_METRES)
HEX_SIDE_M = HEX_FLAT_TO_FLAT_M / math.sqrt(3.0)
HEX_CENTER_X_STEP_M = HEX_FLAT_TO_FLAT_M
HEX_CENTER_Y_STEP_M = 1.5 * HEX_SIDE_M
THEORETICAL_HEX_AREA_M2 = 3.0 * math.sqrt(3.0) / 2.0 * HEX_SIDE_M**2
GRID_ANCHOR_X_M = 0.0
GRID_ANCHOR_Y_M = 0.0

PIXEL_AREA_TOLERANCE_M2 = 1e-6
FRACTION_TOLERANCE = 0.02

ROLE_FIELDS = (
    ("candidate", PRIMARY_CANDIDATE),
    ("habitat_context", HABITAT_CONTEXT_PROXY),
    ("wetland_context", WETLAND_CONTEXT),
    ("inland_water", INLAND_WATER_CONTEXT),
    ("artificial_constraint", ARTIFICIAL_CONSTRAINT),
    ("transitional_forest", "transitional_forest"),
)
FACTUAL_FIELDS = ("peat_extraction", "sea")


class AnalysisUnitsError(RuntimeError):
    """Raised when grid construction or composition validation fails."""


def hex_center(grid_col: int, grid_row: int) -> tuple[float, float]:
    """Return the anchored pointy-top hex center for integer axial coordinates."""

    x = GRID_ANCHOR_X_M + (grid_col + grid_row / 2.0) * HEX_CENTER_X_STEP_M
    y = GRID_ANCHOR_Y_M + grid_row * HEX_CENTER_Y_STEP_M
    return float(x), float(y)


def hex_polygon(grid_col: int, grid_row: int) -> Polygon:
    """Return a complete regular pointy-top hexagon at one grid coordinate."""

    center_x, center_y = hex_center(grid_col, grid_row)
    angles = np.deg2rad(np.arange(30.0, 390.0, 60.0))
    coordinates = [
        (
            round(center_x + HEX_SIDE_M * math.cos(float(angle)), 9),
            round(center_y + HEX_SIDE_M * math.sin(float(angle)), 9),
        )
        for angle in angles
    ]
    return Polygon(coordinates)


def _grid_row_range(bounds: tuple[float, float, float, float]) -> range:
    """Return a conservative integer row range before exact intersection."""

    _, min_y, _, max_y = bounds
    row_min = math.floor((min_y - HEX_SIDE_M - GRID_ANCHOR_Y_M) / HEX_CENTER_Y_STEP_M) - 1
    row_max = math.ceil((max_y + HEX_SIDE_M - GRID_ANCHOR_Y_M) / HEX_CENTER_Y_STEP_M) + 1
    return range(row_min, row_max + 1)


def generate_grid(bounds: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """Generate all complete hexagons intersecting a rectangular extent."""

    extent = box(*bounds)
    records: list[dict[str, Any]] = []
    min_x, min_y, max_x, max_y = bounds
    row_range = _grid_row_range(bounds)
    for grid_row in row_range:
        col_min = (
            math.floor(
                (min_x - HEX_FLAT_TO_FLAT_M / 2.0 - GRID_ANCHOR_X_M) / HEX_CENTER_X_STEP_M
                - grid_row / 2.0
            )
            - 1
        )
        col_max = (
            math.ceil(
                (max_x + HEX_FLAT_TO_FLAT_M / 2.0 - GRID_ANCHOR_X_M) / HEX_CENTER_X_STEP_M
                - grid_row / 2.0
            )
            + 1
        )
        for grid_col in range(col_min, col_max + 1):
            geometry = hex_polygon(grid_col, grid_row)
            if geometry.intersects(extent):
                records.append(
                    {
                        "hex_id": f"h_{grid_col}_{grid_row}",
                        "grid_col": grid_col,
                        "grid_row": grid_row,
                        "geometry": geometry,
                    }
                )
    records.sort(key=lambda record: (record["grid_col"], record["grid_row"]))
    return gpd.GeoDataFrame(records, geometry="geometry", crs=GRID_CRS)


def pixel_centers_to_grid_indices(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Assign points to pointy-top axial hexes using their centers.

    The transform is the standard pointy-top axial-coordinate transform. Cube
    rounding selects the unique containing cell except for points exactly on a
    shared boundary; the deterministic NumPy tie behavior still assigns each
    such point once.
    """

    q_float = (math.sqrt(3.0) / 3.0 * x - 1.0 / 3.0 * y) / HEX_SIDE_M
    r_float = (2.0 / 3.0 * y) / HEX_SIDE_M
    s_float = -q_float - r_float

    q_round = np.rint(q_float)
    r_round = np.rint(r_float)
    s_round = np.rint(s_float)
    q_diff = np.abs(q_round - q_float)
    r_diff = np.abs(r_round - r_float)
    s_diff = np.abs(s_round - s_float)

    q_result = q_round.copy()
    r_result = r_round.copy()
    q_is_largest = (q_diff > r_diff) & (q_diff > s_diff)
    r_is_largest = (~q_is_largest) & (r_diff > s_diff)
    q_result[q_is_largest] = -r_round[q_is_largest] - s_round[q_is_largest]
    r_result[r_is_largest] = -q_round[r_is_largest] - s_round[r_is_largest]
    return q_result.astype(np.int64), r_result.astype(np.int64)


def _read_study_geometry(study_area_path: Path | None) -> Any | None:
    if study_area_path is None:
        return None
    study = gpd.read_file(study_area_path, layer=STUDY_AREA_LAYER)
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise AnalysisUnitsError("Study area must contain a non-empty geometry")
    if study.crs is None or str(study.crs) != GRID_CRS:
        raise AnalysisUnitsError(f"Study-area CRS is {study.crs}; expected {GRID_CRS}")
    return study.geometry.union_all()


def _validate_raster_metadata(dataset: rasterio.io.DatasetReader) -> float:
    if str(dataset.crs) != GRID_CRS:
        raise AnalysisUnitsError(f"NMD raster CRS is {dataset.crs}; expected {GRID_CRS}")
    if dataset.count != 1:
        raise AnalysisUnitsError(f"NMD raster must have one band; found {dataset.count}")
    if dataset.transform.b != 0 or dataset.transform.d != 0:
        raise AnalysisUnitsError("Rotated NMD rasters are not supported")
    pixel_area_m2 = abs(float(dataset.transform.a * dataset.transform.e))
    if pixel_area_m2 <= 0:
        raise AnalysisUnitsError("NMD raster has a non-positive pixel area")
    if not np.allclose(dataset.res, (10.0, 10.0), rtol=0, atol=1e-9):
        raise AnalysisUnitsError(f"NMD raster resolution is {dataset.res}; expected 10 m")
    if not math.isclose(pixel_area_m2, 100.0, rel_tol=0, abs_tol=PIXEL_AREA_TOLERANCE_M2):
        raise AnalysisUnitsError(f"NMD raster pixel area is {pixel_area_m2}; expected 100 m²")
    return pixel_area_m2


def _role_masks(codes: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    factual = factual_group_masks(codes)
    roles = {
        role: np.logical_or.reduce([factual[group] for group in groups])
        for role, groups in ROLE_FACTUAL_GROUPS.items()
    }
    return roles, factual


def _accumulate_counts(
    dataset: rasterio.io.DatasetReader,
    grid: gpd.GeoDataFrame,
    study_geometry: Any | None,
) -> tuple[dict[str, np.ndarray], dict[str, int], float]:
    """Aggregate center-assigned pixels block-wise into the generated grid."""

    count_names = (
        "nmd_valid_pixels",
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
    counts = {name: np.zeros(len(grid), dtype=np.int64) for name in count_names}
    aggregate_totals = {name: 0 for name in count_names}
    pixel_area_m2 = _validate_raster_metadata(dataset)
    col_min = int(grid["grid_col"].min())
    col_max = int(grid["grid_col"].max())
    row_min = int(grid["grid_row"].min())
    row_max = int(grid["grid_row"].max())
    lookup = np.full((col_max - col_min + 1, row_max - row_min + 1), -1, dtype=np.int64)
    lookup[
        grid["grid_col"].to_numpy(dtype=np.int64) - col_min,
        grid["grid_row"].to_numpy(dtype=np.int64) - row_min,
    ] = np.arange(len(grid), dtype=np.int64)

    for _, window in dataset.block_windows(1):
        data = dataset.read(1, window=window, masked=False)
        raster_mask = dataset.read_masks(1, window=window) > 0
        if study_geometry is not None:
            study_mask = rasterize(
                [(study_geometry, 1)],
                out_shape=data.shape,
                transform=window_transform(window, dataset.transform),
                fill=0,
                dtype="uint8",
            ).astype(bool)
            raster_mask &= study_mask

        valid = raster_mask & (data != 0)
        if not np.any(valid):
            continue
        flat_indices = np.flatnonzero(valid)
        row_indices, col_indices = np.divmod(flat_indices, data.shape[1])
        x = dataset.transform.c + (window.col_off + col_indices + 0.5) * dataset.transform.a
        y = dataset.transform.f + (window.row_off + row_indices + 0.5) * dataset.transform.e
        grid_col, grid_row = pixel_centers_to_grid_indices(x, y)
        col_offset = grid_col - col_min
        row_offset = grid_row - row_min
        in_range = (
            (col_offset >= 0)
            & (col_offset < lookup.shape[0])
            & (row_offset >= 0)
            & (row_offset < lookup.shape[1])
        )
        cell_indices = np.full(grid_col.shape, -1, dtype=np.int64)
        cell_indices[in_range] = lookup[col_offset[in_range], row_offset[in_range]]
        if np.any(cell_indices < 0):
            raise AnalysisUnitsError("A valid NMD pixel center was not assigned to a generated hex")

        codes = data[valid]
        role_masks, factual_masks = _role_masks(codes)
        masks = {
            "nmd_valid_pixels": np.ones(codes.shape, dtype=bool),
            "terrestrial_pixels": role_masks[TERRESTRIAL_LAND],
            "candidate_pixels": role_masks[PRIMARY_CANDIDATE],
            "habitat_context_pixels": role_masks[HABITAT_CONTEXT_PROXY],
            "wetland_context_pixels": role_masks[WETLAND_CONTEXT],
            "inland_water_pixels": role_masks[INLAND_WATER_CONTEXT],
            "artificial_constraint_pixels": role_masks[ARTIFICIAL_CONSTRAINT],
            "transitional_forest_pixels": role_masks["transitional_forest"],
            "peat_extraction_pixels": factual_masks[PEAT_EXTRACTION],
            "sea_pixels": factual_masks[SEA],
        }
        for name, mask in masks.items():
            block_counts = np.bincount(cell_indices[mask], minlength=len(grid))
            counts[name] += block_counts
            aggregate_totals[name] += int(np.count_nonzero(mask))

    return counts, aggregate_totals, pixel_area_m2


def _load_semantic_audit(audit_path: Path) -> dict[str, Any]:
    if not audit_path.exists():
        raise AnalysisUnitsError(
            f"Missing NMD semantic audit: {audit_path}; run "
            "python -m restoration_prioritizer.nmd_semantics first"
        )
    try:
        return json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisUnitsError(f"Could not read semantic audit: {audit_path}: {exc}") from exc


def _expected_audit_totals(audit: dict[str, Any]) -> dict[str, int]:
    try:
        role_summary = audit["analytical_role_summary"]
        factual_summary = audit["factual_group_summary"]
        return {
            "terrestrial_pixels": int(role_summary[TERRESTRIAL_LAND]["pixel_count"]),
            "candidate_pixels": int(role_summary[PRIMARY_CANDIDATE]["pixel_count"]),
            "habitat_context_pixels": int(role_summary[HABITAT_CONTEXT_PROXY]["pixel_count"]),
            "wetland_context_pixels": int(role_summary[WETLAND_CONTEXT]["pixel_count"]),
            "inland_water_pixels": int(role_summary[INLAND_WATER_CONTEXT]["pixel_count"]),
            "artificial_constraint_pixels": int(role_summary[ARTIFICIAL_CONSTRAINT]["pixel_count"]),
            "transitional_forest_pixels": int(role_summary["transitional_forest"]["pixel_count"]),
            "peat_extraction_pixels": int(factual_summary[PEAT_EXTRACTION]["pixel_count"]),
            "sea_pixels": int(factual_summary[SEA]["pixel_count"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisUnitsError("NMD semantic audit has an unexpected schema") from exc


def _reconcile_totals(
    aggregate_totals: dict[str, int], expected: dict[str, int]
) -> dict[str, dict[str, int | bool]]:
    reconciliation: dict[str, dict[str, int | bool]] = {}
    for name, expected_count in expected.items():
        observed_count = int(aggregate_totals[name])
        delta = observed_count - expected_count
        reconciliation[name] = {
            "observed_pixels": observed_count,
            "expected_pixels": expected_count,
            "delta_pixels": delta,
            "matches": delta == 0,
        }
        if delta != 0:
            raise AnalysisUnitsError(
                f"Aggregate reconciliation failed for {name}: "
                f"observed={observed_count}, expected={expected_count}, delta={delta}"
            )
    return reconciliation


def _quantiles(values: np.ndarray) -> dict[str, float | None]:
    if values.size == 0:
        return {"p25": None, "median": None, "p75": None, "p90": None}
    p25, median, p75, p90 = np.quantile(values, [0.25, 0.5, 0.75, 0.9])
    return {
        "p25": float(p25),
        "median": float(median),
        "p75": float(p75),
        "p90": float(p90),
    }


def _count_bins(
    values: np.ndarray, boundaries: Iterable[tuple[str, float, float | None]]
) -> list[dict[str, Any]]:
    result = []
    for label, lower, upper in boundaries:
        if upper is None:
            mask = values >= lower
        elif lower == 0:
            mask = (values > lower) & (values <= upper)
        else:
            mask = (values > lower) & (values <= upper)
        result.append({"label": label, "count": int(np.count_nonzero(mask))})
    return result


def _distributions(
    terrestrial_fraction: np.ndarray,
    candidate_fraction: np.ndarray,
    candidate_area_ha: np.ndarray,
) -> dict[str, Any]:
    coverage_bins = _count_bins(
        terrestrial_fraction,
        (
            (">0–5%", 0.0, 0.05),
            (">5–25%", 0.05, 0.25),
            (">25–50%", 0.25, 0.50),
            (">50–75%", 0.50, 0.75),
            (">75–95%", 0.75, 0.95),
            (">95–<100%", 0.95, 1.0),
            ("≥100% (raw pixelization value)", 1.0, None),
        ),
    )
    candidate_bins = [
        {"label": "0%", "count": int(np.count_nonzero(candidate_fraction == 0.0))},
        *_count_bins(
            candidate_fraction,
            (
                (">0–10%", 0.0, 0.10),
                (">10–25%", 0.10, 0.25),
                (">25–50%", 0.25, 0.50),
                (">50–75%", 0.50, 0.75),
                (">75–90%", 0.75, 0.90),
                (">90–100%", 0.90, 1.0),
            ),
        ),
    ]
    with_candidate = candidate_fraction > 0
    with_candidate_area = candidate_area_ha > 0
    fraction_thresholds = [0.05, 0.10, 0.20, 0.25, 0.33, 0.50]
    area_thresholds = [1.0, 2.0, 5.0, 10.0]
    combinations = [(0.10, 2.0), (0.20, 2.0), (0.20, 5.0), (0.25, 5.0), (0.33, 5.0), (0.50, 5.0)]
    return {
        "terrestrial_coverage_bins": coverage_bins,
        "candidate_fraction_bins": candidate_bins,
        "candidate_fraction_quantiles_with_any_candidate": _quantiles(
            candidate_fraction[with_candidate]
        ),
        "candidate_area_ha_quantiles_with_any_candidate": {
            **_quantiles(candidate_area_ha[with_candidate_area]),
            "maximum": (
                float(np.max(candidate_area_ha[with_candidate_area]))
                if np.any(with_candidate_area)
                else None
            ),
        },
        "threshold_sensitivity_diagnostic_only": {
            "candidate_fraction_of_terrestrial": [
                {
                    "threshold": threshold,
                    "retained_cells": int(np.count_nonzero(candidate_fraction >= threshold)),
                }
                for threshold in fraction_thresholds
            ],
            "candidate_area_ha": [
                {
                    "threshold": threshold,
                    "retained_cells": int(np.count_nonzero(candidate_area_ha >= threshold)),
                }
                for threshold in area_thresholds
            ],
            "combined": [
                {
                    "candidate_fraction_threshold": fraction_threshold,
                    "candidate_area_ha_threshold": area_threshold,
                    "retained_cells": int(
                        np.count_nonzero(
                            (candidate_fraction >= fraction_threshold)
                            & (candidate_area_ha >= area_threshold)
                        )
                    ),
                }
                for fraction_threshold, area_threshold in combinations
            ],
        },
    }


def _validate_geometry(grid: gpd.GeoDataFrame) -> dict[str, Any]:
    areas = grid.geometry.area.to_numpy(dtype=float)
    invalid_count = int((~grid.geometry.is_valid).sum())
    duplicate_id_count = int(grid["hex_id"].duplicated().sum())
    if invalid_count or duplicate_id_count:
        raise AnalysisUnitsError(
            f"Invalid analysis-unit geometry or IDs: invalid={invalid_count}, "
            f"duplicate_ids={duplicate_id_count}"
        )
    if len(areas) and not np.allclose(areas, THEORETICAL_HEX_AREA_M2, rtol=0, atol=1e-6):
        raise AnalysisUnitsError("Analysis-unit geometries are not complete regular hexagons")

    overlap_pair_count = 0
    max_overlap_area_m2 = 0.0
    if len(grid):
        tree = STRtree(grid.geometry.to_numpy())
        pairs = tree.query(grid.geometry.to_numpy(), predicate="overlaps")
        if pairs.size:
            unique_pairs = pairs[:, pairs[0] < pairs[1]]
            overlap_areas = [
                grid.geometry.iloc[left].intersection(grid.geometry.iloc[right]).area
                for left, right in unique_pairs.T
            ]
            max_overlap_area_m2 = float(max(overlap_areas, default=0.0))
            overlap_pair_count = sum(area > 1e-6 for area in overlap_areas)
    if overlap_pair_count:
        raise AnalysisUnitsError(f"Analysis-unit interiors overlap in {overlap_pair_count} pairs")
    return {
        "feature_count": int(len(grid)),
        "invalid_geometry_count": invalid_count,
        "duplicate_hex_id_count": duplicate_id_count,
        "complete_hex_area_m2": {
            "theoretical": THEORETICAL_HEX_AREA_M2,
            "minimum": float(np.min(areas)) if len(areas) else None,
            "maximum": float(np.max(areas)) if len(areas) else None,
            "mean": float(np.mean(areas)) if len(areas) else None,
            "max_absolute_difference": (
                float(np.max(np.abs(areas - THEORETICAL_HEX_AREA_M2))) if len(areas) else None
            ),
        },
        "interior_overlap_pair_count": overlap_pair_count,
        "maximum_overlap_area_m2": max_overlap_area_m2,
    }


def _write_geopackage(grid: gpd.GeoDataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.stem}.part{output_path.suffix}")
    temporary_path.unlink(missing_ok=True)
    grid.to_file(temporary_path, layer=OUTPUT_LAYER, driver="GPKG", index=False)
    os.replace(temporary_path, output_path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_path, path)


def build_analysis_units(
    raster_path: Path = NMD_RASTER_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
    audit_path: Path | None = NMD_SEMANTIC_AUDIT_PATH,
    study_area_path: Path | None = STUDY_AREA_PATH,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Build, validate, write, and summarize the durable analysis-unit layer."""

    start = time.perf_counter()
    with rasterio.open(raster_path) as dataset:
        bounds = tuple(float(value) for value in dataset.bounds)
        grid = generate_grid(bounds)
        if grid.empty:
            raise AnalysisUnitsError("Raster extent generated no hexagons")
        study_geometry = _read_study_geometry(study_area_path)
        counts, aggregate_totals, pixel_area_m2 = _accumulate_counts(dataset, grid, study_geometry)

    nmd_valid = counts["nmd_valid_pixels"]
    terrestrial = counts["terrestrial_pixels"]
    retained_mask = terrestrial > 0
    retained_grid = grid.loc[retained_mask].copy().reset_index(drop=True)
    retained_indices = np.flatnonzero(retained_mask)
    if retained_grid.empty:
        raise AnalysisUnitsError("No generated hex contains terrestrial NMD pixels")

    for name, _ in ROLE_FIELDS:
        pixel_field = f"{name}_pixels"
        area_field = f"{name}_area_m2"
        retained_grid[pixel_field] = counts[pixel_field][retained_indices]
        retained_grid[area_field] = counts[pixel_field][retained_indices] * pixel_area_m2
    for name in FACTUAL_FIELDS:
        pixel_field = f"{name}_pixels"
        area_field = f"{name}_area_m2"
        retained_grid[pixel_field] = counts[pixel_field][retained_indices]
        retained_grid[area_field] = counts[pixel_field][retained_indices] * pixel_area_m2
    retained_grid["nmd_valid_pixels"] = nmd_valid[retained_indices]
    retained_grid["nmd_valid_area_m2"] = nmd_valid[retained_indices] * pixel_area_m2
    retained_grid["terrestrial_pixels"] = terrestrial[retained_indices]
    retained_grid["terrestrial_area_m2"] = terrestrial[retained_indices] * pixel_area_m2
    retained_grid["terrestrial_fraction"] = (
        retained_grid["terrestrial_area_m2"] / THEORETICAL_HEX_AREA_M2
    )
    retained_grid["candidate_fraction_of_terrestrial"] = (
        retained_grid["candidate_pixels"] / retained_grid["terrestrial_pixels"]
    )
    retained_grid["habitat_context_fraction_of_terrestrial"] = (
        retained_grid["habitat_context_pixels"] / retained_grid["terrestrial_pixels"]
    )

    for field in (
        "nmd_valid_pixels",
        "terrestrial_pixels",
        "candidate_pixels",
        "habitat_context_pixels",
        "wetland_context_pixels",
        "inland_water_pixels",
        "artificial_constraint_pixels",
        "transitional_forest_pixels",
        "peat_extraction_pixels",
        "sea_pixels",
    ):
        if np.any(retained_grid[field].to_numpy(dtype=np.int64) < 0):
            raise AnalysisUnitsError(f"Negative pixel count in {field}")
    if np.any(retained_grid["candidate_pixels"] > retained_grid["terrestrial_pixels"]):
        raise AnalysisUnitsError("Candidate pixels exceed terrestrial pixels")
    terrestrial_fraction = retained_grid["terrestrial_fraction"].to_numpy(dtype=float)
    if np.any(terrestrial_fraction <= 0) or np.any(terrestrial_fraction > 1 + FRACTION_TOLERANCE):
        raise AnalysisUnitsError(
            "Terrestrial fractions are outside (0, 1 + pixelization tolerance]; "
            f"min={terrestrial_fraction.min()}, max={terrestrial_fraction.max()}"
        )
    for field in ("candidate_fraction_of_terrestrial", "habitat_context_fraction_of_terrestrial"):
        values = retained_grid[field].to_numpy(dtype=float)
        if np.any(values < 0) or np.any(values > 1):
            raise AnalysisUnitsError(f"{field} is outside [0, 1]")

    geometry_validation = _validate_geometry(retained_grid)
    reconciliation: dict[str, Any] | None = None
    retained_reconciliation: dict[str, Any] | None = None
    audit_reference = None
    if audit_path is not None:
        audit = _load_semantic_audit(audit_path)
        expected_totals = _expected_audit_totals(audit)
        reconciliation = _reconcile_totals(aggregate_totals, expected_totals)
        retained_reconciliation = {
            name: {
                "observed_pixels": int(retained_grid[name].sum()),
                "expected_pixels": expected_count,
                "delta_pixels": int(retained_grid[name].sum()) - expected_count,
                "matches": int(retained_grid[name].sum()) == expected_count,
            }
            for name, expected_count in expected_totals.items()
        }
        terrestrial_role_fields = tuple(
            name for name in expected_totals if name not in {"inland_water_pixels", "sea_pixels"}
        )
        if any(not retained_reconciliation[name]["matches"] for name in terrestrial_role_fields):
            raise AnalysisUnitsError(
                "Retained output lost terrestrial semantic pixels unexpectedly: "
                f"{[name for name in terrestrial_role_fields if not retained_reconciliation[name]['matches']]}"
            )
        audit_reference = str(audit_path)

    candidate_fraction = retained_grid["candidate_fraction_of_terrestrial"].to_numpy(dtype=float)
    candidate_area_ha = retained_grid["candidate_area_m2"].to_numpy(dtype=float) / 10_000.0
    distributions = _distributions(terrestrial_fraction, candidate_fraction, candidate_area_ha)
    distributions["terrestrial_fraction_raw_pixelization_over_100"] = {
        "count": int(np.count_nonzero(terrestrial_fraction > 1.0)),
        "maximum": float(np.max(terrestrial_fraction)) if len(terrestrial_fraction) else None,
        "representation": "raw terrestrial pixel area divided by theoretical complete-hex area; not clamped",
    }
    counts_summary = {
        "initial_generated_hex_count": int(len(grid)),
        "nmd_valid_hex_count": int(np.count_nonzero(nmd_valid > 0)),
        "retained_terrestrial_hex_count": int(len(retained_grid)),
        "zero_terrestrial_hexes_removed": int(len(grid) - len(retained_grid)),
    }
    output_fields = [column for column in retained_grid.columns if column != "geometry"]
    _write_geopackage(retained_grid, output_path)
    output_size = output_path.stat().st_size
    elapsed_seconds = time.perf_counter() - start
    provenance: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "grid": {
            "crs": GRID_CRS,
            "orientation": "pointy-top; one vertex points north; flat-to-flat width is horizontal",
            "flat_to_flat_width_m": HEX_FLAT_TO_FLAT_M,
            "side_length_m": HEX_SIDE_M,
            "theoretical_full_hex_area_m2": THEORETICAL_HEX_AREA_M2,
            "anchor": {
                "x_m": GRID_ANCHOR_X_M,
                "y_m": GRID_ANCHOR_Y_M,
                "convention": (
                    "hex center = (anchor_x + (col + row / 2) * flat_to_flat, "
                    "anchor_y + row * 1.5 * side); "
                    "IDs are h_<grid_col>_<grid_row>"
                ),
            },
        },
        "source": {
            "nmd_raster_path": str(raster_path),
            "nmd_version": "NMD2023 v2.1",
            "study_area_path": str(study_area_path) if study_area_path else None,
            "semantic_audit_path": audit_reference,
            "raster_resolution_m": [10.0, 10.0],
            "pixel_area_m2": pixel_area_m2,
        },
        "pixel_assignment": {
            "rule": "NMD pixel centers assigned to standard pointy-top axial hexes",
            "all_touched": False,
            "outside_study_mask_excluded": study_area_path is not None,
            "valid_pixel_definition": "raster mask true and NMD code is not 0",
        },
        "counts": counts_summary,
        "output": {
            "path": str(output_path),
            "layer": OUTPUT_LAYER,
            "feature_count": int(len(retained_grid)),
            "size_bytes": int(output_size),
            "geometry_type": sorted({geometry.geom_type for geometry in retained_grid.geometry}),
            "fields": output_fields,
        },
        "geometry_validation": geometry_validation,
        "composition_aggregate_reconciliation": {
            "full_generated_grid_before_zero_terrestrial_filter": reconciliation,
            "retained_output_layer_after_zero_terrestrial_filter": retained_reconciliation,
            "retained_layer_note": (
                "Terrestrial semantic roles must still match exactly. Inland-water and sea totals "
                "may be lower in the durable layer because cells containing no terrestrial land "
                "are intentionally removed; full-grid totals above provide the conservation check."
            ),
        },
        "distributions": distributions,
        "runtime_seconds": elapsed_seconds,
    }
    _write_json(provenance_path, provenance)
    return retained_grid, provenance


def main() -> None:
    """Build the real-data analysis-unit artifact and print a concise summary."""

    _, provenance = build_analysis_units()
    counts = provenance["counts"]
    output = provenance["output"]
    print(f"Analysis units: {output['path']} [{output['layer']}] ({output['size_bytes']:,} bytes)")
    print(
        f"Generated {counts['initial_generated_hex_count']:,} hexes; "
        f"NMD-valid {counts['nmd_valid_hex_count']:,}; "
        f"retained terrestrial {counts['retained_terrestrial_hex_count']:,}; "
        f"removed zero-terrestrial {counts['zero_terrestrial_hexes_removed']:,}"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")
    print("Threshold sensitivity is diagnostic only; no eligibility threshold was selected.")


if __name__ == "__main__":
    main()
