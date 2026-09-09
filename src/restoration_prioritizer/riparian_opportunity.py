"""Calculate raw Riparian Opportunity context indicators from NMD2023.

The module deliberately stops at raw, pixel-weighted hydrologic-context
fractions.  It includes inland-water-only grid positions that are absent from
the durable Step 6 terrestrial analysis-unit layer, but it does not alter
candidate eligibility or select a scored riparian input.
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
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import transform as window_transform

from restoration_prioritizer.analysis_units import (
    generate_grid,
    pixel_centers_to_grid_indices,
)
from restoration_prioritizer.config import TARGET_CRS
from restoration_prioritizer.habitat_context import (
    LOCAL_OFFSETS,
    RING_1_OFFSETS,
    calculate_boundary_edge_flags,
)
from restoration_prioritizer.nmd_semantics import (
    FACTUAL_GROUP_CODES,
    INLAND_WATER,
    INLAND_WATER_CONTEXT,
    NO_DATA,
    SEA,
    TERRESTRIAL_LAND,
    WETLAND_CONTEXT,
    NMD_RASTER_PATH,
    ROLE_FACTUAL_GROUPS,
    analytical_role_masks,
    factual_group_masks,
    validate_contract_definition,
)

NMD_VERSION = "NMD2023 v2.1"
NMD_STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
NMD_STUDY_AREA_LAYER = "study_area"
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
OUTPUT_PATH = Path("data/processed/indicators/riparian_opportunity.csv")
PROVENANCE_PATH = Path("data/processed/indicators/riparian_opportunity.provenance.json")
HABITAT_RAW_PATH = Path("data/processed/indicators/habitat_context.csv")
HABITAT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
NETWORK_SCORE_PATH = Path("data/processed/components/ecological_network.csv")
STEP6_ANALYSIS_UNITS_PATH = Path("data/processed/analysis_units.gpkg")
STEP6_ANALYSIS_UNITS_LAYER = "analysis_units"
BOUNDARY_EDGE_DISTANCE_M = 1_000.0
PIXEL_AREA_M2 = 100.0
CSV_FLOAT_PRECISION = 10

GRID_COUNT_COLUMNS = (
    "hex_id",
    "grid_col",
    "grid_row",
    "nmd_valid_pixels",
    "terrestrial_land_pixels",
    "wetland_context_pixels",
    "inland_water_pixels",
    "hydrologic_context_pixels",
    "nonmarine_context_pixels",
    "sea_pixels",
)

OUTPUT_COLUMNS = (
    "hex_id",
    "riparian_focal_fraction",
    "riparian_adjacent_fraction",
    "riparian_near_fraction",
    "riparian_local_fraction",
    "focal_wetland_fraction",
    "focal_inland_water_fraction",
    "adjacent_wetland_fraction",
    "adjacent_inland_water_fraction",
    "local_wetland_fraction",
    "local_inland_water_fraction",
    "boundary_edge_flag",
)

SCALE_FIELDS = {
    "focal": {
        "fraction": "riparian_focal_fraction",
        "wetland_fraction": "focal_wetland_fraction",
        "water_fraction": "focal_inland_water_fraction",
        "wetland_pixels": "focal_wetland_pixels",
        "water_pixels": "focal_inland_water_pixels",
        "hydrologic_pixels": "focal_hydrologic_context_pixels",
        "nonmarine_pixels": "focal_nonmarine_context_pixels",
        "positions_present": "focal_positions_present",
        "nonmarine_positions": "focal_nonmarine_positions",
        "water_only_positions": "focal_water_only_positions",
    },
    "adjacent": {
        "fraction": "riparian_adjacent_fraction",
        "wetland_fraction": "adjacent_wetland_fraction",
        "water_fraction": "adjacent_inland_water_fraction",
        "wetland_pixels": "adjacent_wetland_pixels",
        "water_pixels": "adjacent_inland_water_pixels",
        "hydrologic_pixels": "adjacent_hydrologic_context_pixels",
        "nonmarine_pixels": "adjacent_nonmarine_context_pixels",
        "positions_present": "adjacent_positions_present",
        "nonmarine_positions": "adjacent_nonmarine_positions",
        "water_only_positions": "adjacent_water_only_positions",
    },
    "local": {
        "fraction": "riparian_local_fraction",
        "wetland_fraction": "local_wetland_fraction",
        "water_fraction": "local_inland_water_fraction",
        "wetland_pixels": "local_wetland_pixels",
        "water_pixels": "local_inland_water_pixels",
        "hydrologic_pixels": "local_hydrologic_context_pixels",
        "nonmarine_pixels": "local_nonmarine_context_pixels",
        "positions_present": "local_positions_present",
        "nonmarine_positions": "local_nonmarine_positions",
        "water_only_positions": "local_water_only_positions",
    },
}

RIPARIAN_SCALES = ("focal", "adjacent", "near", "local")
NEAR_FRACTION_FIELD = "riparian_near_fraction"


class RiparianOpportunityError(ValueError):
    """Raised when the Riparian Opportunity input or invariant is malformed."""


def hydrologic_counts_from_codes(codes: np.ndarray | list[int]) -> dict[str, int]:
    """Summarize one categorical NMD sample using the approved semantics."""

    array = np.asarray(codes)
    factual = factual_group_masks(array)
    roles = analytical_role_masks(array)
    wetland = int(roles[WETLAND_CONTEXT].sum())
    inland_water = int(factual[INLAND_WATER].sum())
    terrestrial = int(roles[TERRESTRIAL_LAND].sum())
    sea = int(factual[SEA].sum())
    nmd_valid = int((~factual[NO_DATA]).sum())
    return {
        "nmd_valid_pixels": nmd_valid,
        "terrestrial_land_pixels": terrestrial,
        "wetland_context_pixels": wetland,
        "inland_water_pixels": inland_water,
        "hydrologic_context_pixels": wetland + inland_water,
        "nonmarine_context_pixels": terrestrial + inland_water,
        "sea_pixels": sea,
    }


def fraction_for_counts(numerator: int | float, denominator: int | float) -> float:
    """Return a raw fraction, preserving zero denominators as missing."""

    if denominator <= 0:
        return float("nan")
    return float(numerator / denominator)


def _require_columns(frame: pd.DataFrame, required: tuple[str, ...], label: str) -> None:
    missing = [field for field in required if field not in frame.columns]
    if missing:
        raise RiparianOpportunityError(f"{label} is missing required fields: {missing}")


def _validate_coordinate_fields(frame: pd.DataFrame, label: str) -> None:
    for field in ("grid_col", "grid_row"):
        values = frame[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)) or not np.all(values == np.floor(values)):
            raise RiparianOpportunityError(f"{label} {field} values must be finite integers")


def _validate_grid_counts(grid_counts: pd.DataFrame) -> None:
    _require_columns(grid_counts, GRID_COUNT_COLUMNS, "Hydrologic grid counts")
    if grid_counts.empty:
        raise RiparianOpportunityError("Hydrologic grid counts are empty")
    if grid_counts["hex_id"].isna().any() or grid_counts["hex_id"].duplicated().any():
        raise RiparianOpportunityError("Hydrologic grid hex_id values must be non-null and unique")
    _validate_coordinate_fields(grid_counts, "Hydrologic grid counts")
    pairs = list(
        zip(
            grid_counts["grid_col"].to_numpy(dtype=int),
            grid_counts["grid_row"].to_numpy(dtype=int),
            strict=True,
        )
    )
    if len(set(pairs)) != len(pairs):
        raise RiparianOpportunityError("Hydrologic grid coordinates must be unique")
    for field in GRID_COUNT_COLUMNS[3:]:
        values = grid_counts[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)) or np.any(values < 0):
            raise RiparianOpportunityError(f"{field} must be finite and non-negative")
        if not np.all(values == np.floor(values)):
            raise RiparianOpportunityError(f"{field} must contain integer pixel counts")
    terrestrial = grid_counts["terrestrial_land_pixels"].to_numpy(dtype=np.int64)
    wetland = grid_counts["wetland_context_pixels"].to_numpy(dtype=np.int64)
    inland = grid_counts["inland_water_pixels"].to_numpy(dtype=np.int64)
    if np.any(wetland > terrestrial):
        raise RiparianOpportunityError("Wetland pixels cannot exceed terrestrial pixels")
    if not np.array_equal(
        grid_counts["hydrologic_context_pixels"].to_numpy(dtype=np.int64), wetland + inland
    ):
        raise RiparianOpportunityError("Hydrologic pixels must equal wetland plus inland water")
    if not np.array_equal(
        grid_counts["nonmarine_context_pixels"].to_numpy(dtype=np.int64), terrestrial + inland
    ):
        raise RiparianOpportunityError("Non-marine pixels must equal terrestrial plus inland water")
    if np.any(
        grid_counts["hydrologic_context_pixels"].to_numpy(dtype=np.int64)
        > grid_counts["nonmarine_context_pixels"].to_numpy(dtype=np.int64)
    ):
        raise RiparianOpportunityError("Hydrologic pixels cannot exceed non-marine pixels")


def _validate_candidate_frame(candidates: pd.DataFrame) -> None:
    required = (
        "hex_id",
        "grid_col",
        "grid_row",
        "candidate_fraction_of_terrestrial",
        "candidate_area_m2",
    )
    _require_columns(candidates, required, "Candidate units")
    if candidates.empty:
        raise RiparianOpportunityError("Candidate population is empty")
    if candidates["hex_id"].isna().any() or candidates["hex_id"].duplicated().any():
        raise RiparianOpportunityError("Candidate hex_id values must be non-null and unique")
    _validate_coordinate_fields(candidates, "Candidate units")
    for field in ("candidate_fraction_of_terrestrial", "candidate_area_m2"):
        values = candidates[field].to_numpy(dtype=float)
        if not np.all(np.isfinite(values)) or np.any(values < 0):
            raise RiparianOpportunityError(f"Candidate {field} must be finite and non-negative")
    fractions = candidates["candidate_fraction_of_terrestrial"].to_numpy(dtype=float)
    if np.any(fractions > 1):
        raise RiparianOpportunityError("Candidate fractions must lie in [0, 1]")


def _aggregate_neighborhood(
    coordinate: tuple[int, int],
    offsets: tuple[tuple[int, int], ...],
    lookup: dict[tuple[int, int], dict[str, int]],
) -> dict[str, int]:
    result = {
        "wetland_pixels": 0,
        "water_pixels": 0,
        "hydrologic_pixels": 0,
        "nonmarine_pixels": 0,
        "positions_present": 0,
        "nonmarine_positions": 0,
        "water_only_positions": 0,
    }
    for delta_col, delta_row in offsets:
        values = lookup.get((coordinate[0] + delta_col, coordinate[1] + delta_row))
        if values is None:
            continue
        result["positions_present"] += 1
        result["wetland_pixels"] += values["wetland_context_pixels"]
        result["water_pixels"] += values["inland_water_pixels"]
        result["hydrologic_pixels"] += values["hydrologic_context_pixels"]
        result["nonmarine_pixels"] += values["nonmarine_context_pixels"]
        if values["nonmarine_context_pixels"] > 0:
            result["nonmarine_positions"] += 1
        if values["terrestrial_land_pixels"] == 0 and values["inland_water_pixels"] > 0:
            result["water_only_positions"] += 1
    return result


def calculate_indicators(
    candidate_units: pd.DataFrame,
    grid_counts: pd.DataFrame,
    edge_flags: np.ndarray | list[bool] | None = None,
) -> pd.DataFrame:
    """Calculate focal, adjacent, and local pixel-weighted raw indicators.

    The returned frame includes internal pixel-count and presence diagnostics;
    callers should select ``OUTPUT_COLUMNS`` for the durable CSV.
    """

    _validate_candidate_frame(candidate_units)
    _validate_grid_counts(grid_counts)
    if edge_flags is not None and len(edge_flags) != len(candidate_units):
        raise RiparianOpportunityError("Boundary edge flags must match candidate row count")

    lookup: dict[tuple[int, int], dict[str, int]] = {}
    for row in grid_counts.itertuples(index=False):
        lookup[(int(row.grid_col), int(row.grid_row))] = {
            field: int(getattr(row, field)) for field in GRID_COUNT_COLUMNS[3:]
        }

    records: list[dict[str, Any]] = []
    for row in candidate_units.itertuples(index=False):
        coordinate = (int(row.grid_col), int(row.grid_row))
        focal_values = lookup.get(coordinate)
        if focal_values is None:
            raise RiparianOpportunityError(
                f"Candidate {row.hex_id} has no NMD-valid focal grid position"
            )
        adjacent_values = _aggregate_neighborhood(coordinate, RING_1_OFFSETS, lookup)
        local_values = _aggregate_neighborhood(coordinate, LOCAL_OFFSETS, lookup)
        scale_values = {
            "focal": {
                "wetland_pixels": focal_values["wetland_context_pixels"],
                "water_pixels": focal_values["inland_water_pixels"],
                "hydrologic_pixels": focal_values["hydrologic_context_pixels"],
                "nonmarine_pixels": focal_values["nonmarine_context_pixels"],
                "positions_present": 1,
                "nonmarine_positions": int(focal_values["nonmarine_context_pixels"] > 0),
                "water_only_positions": int(
                    focal_values["terrestrial_land_pixels"] == 0
                    and focal_values["inland_water_pixels"] > 0
                ),
            },
            "adjacent": adjacent_values,
            "local": local_values,
        }
        record: dict[str, Any] = {"hex_id": str(row.hex_id)}
        for scale, values in scale_values.items():
            fields = SCALE_FIELDS[scale]
            record[fields["wetland_pixels"]] = values["wetland_pixels"]
            record[fields["water_pixels"]] = values["water_pixels"]
            record[fields["hydrologic_pixels"]] = values["hydrologic_pixels"]
            record[fields["nonmarine_pixels"]] = values["nonmarine_pixels"]
            record[fields["positions_present"]] = values["positions_present"]
            record[fields["nonmarine_positions"]] = values["nonmarine_positions"]
            record[fields["water_only_positions"]] = values["water_only_positions"]
            record[fields["fraction"]] = fraction_for_counts(
                values["hydrologic_pixels"], values["nonmarine_pixels"]
            )
            record[fields["wetland_fraction"]] = fraction_for_counts(
                values["wetland_pixels"], values["nonmarine_pixels"]
            )
            record[fields["water_fraction"]] = fraction_for_counts(
                values["water_pixels"], values["nonmarine_pixels"]
            )
        record.update(
            {
                "focal_has_wetland": scale_values["focal"]["wetland_pixels"] > 0,
                "focal_has_inland_water": scale_values["focal"]["water_pixels"] > 0,
                "focal_has_hydrologic_context": scale_values["focal"]["hydrologic_pixels"] > 0,
                "adjacent_has_wetland": scale_values["adjacent"]["wetland_pixels"] > 0,
                "adjacent_has_inland_water": scale_values["adjacent"]["water_pixels"] > 0,
                "adjacent_has_hydrologic_context": scale_values["adjacent"]["hydrologic_pixels"]
                > 0,
                "local_has_wetland": scale_values["local"]["wetland_pixels"] > 0,
                "local_has_inland_water": scale_values["local"]["water_pixels"] > 0,
                "local_has_hydrologic_context": scale_values["local"]["hydrologic_pixels"] > 0,
            }
        )
        record[NEAR_FRACTION_FIELD] = float(
            np.fmax(
                record["riparian_focal_fraction"],
                record["riparian_adjacent_fraction"],
            )
        )
        records.append(record)

    indicators = pd.DataFrame.from_records(records)
    if edge_flags is not None:
        indicators["boundary_edge_flag"] = np.asarray(edge_flags, dtype=bool)
    else:
        indicators["boundary_edge_flag"] = False
    _validate_indicator_values(indicators)
    return indicators


def _validate_indicator_values(indicators: pd.DataFrame) -> None:
    for scale in SCALE_FIELDS:
        fields = SCALE_FIELDS[scale]
        values = indicators[fields["fraction"]].to_numpy(dtype=float)
        valid = np.isfinite(values)
        if np.any((values[valid] < 0) | (values[valid] > 1)):
            raise RiparianOpportunityError(f"{scale} riparian fractions must lie in [0, 1]")
        for fraction_field in (fields["wetland_fraction"], fields["water_fraction"]):
            split = indicators[fraction_field].to_numpy(dtype=float)
            valid_split = np.isfinite(split)
            if np.any((split[valid_split] < 0) | (split[valid_split] > 1)):
                raise RiparianOpportunityError(f"{fraction_field} must lie in [0, 1]")
        combined = indicators[fields["wetland_fraction"]].to_numpy(dtype=float) + indicators[
            fields["water_fraction"]
        ].to_numpy(dtype=float)
        raw = indicators[fields["fraction"]].to_numpy(dtype=float)
        valid = np.isfinite(raw) & np.isfinite(combined)
        if not np.allclose(combined[valid], raw[valid], rtol=0, atol=1e-12):
            raise RiparianOpportunityError(f"{scale} wetland and inland-water fractions do not sum")
    near = indicators[NEAR_FRACTION_FIELD].to_numpy(dtype=float)
    focal = indicators["riparian_focal_fraction"].to_numpy(dtype=float)
    adjacent = indicators["riparian_adjacent_fraction"].to_numpy(dtype=float)
    valid_near = np.isfinite(near)
    if np.any((near[valid_near] < 0) | (near[valid_near] > 1)):
        raise RiparianOpportunityError("Near riparian fractions must lie in [0, 1]")
    valid_max = valid_near & (np.isfinite(focal) | np.isfinite(adjacent))
    expected = np.fmax(focal[valid_max], adjacent[valid_max])
    if not np.allclose(near[valid_max], expected, rtol=0, atol=1e-12, equal_nan=True):
        raise RiparianOpportunityError("Near riparian fraction must equal max(focal, adjacent)")
    if np.any(valid_max & np.isfinite(focal) & (near < focal)):
        raise RiparianOpportunityError("Near riparian fraction cannot be below focal fraction")
    if np.any(valid_max & np.isfinite(adjacent) & (near < adjacent)):
        raise RiparianOpportunityError("Near riparian fraction cannot be below adjacent fraction")


def _read_study_geometry(path: Path) -> Any:
    study = gpd.read_file(path, layer=NMD_STUDY_AREA_LAYER)
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise RiparianOpportunityError("Study area must contain a non-empty geometry")
    if study.crs is None or str(study.crs) != TARGET_CRS:
        raise RiparianOpportunityError(f"Study-area CRS is {study.crs}; expected {TARGET_CRS}")
    return study.geometry.union_all()


def _validate_raster(dataset: rasterio.io.DatasetReader) -> None:
    if dataset.count != 1 or str(dataset.crs) != TARGET_CRS:
        raise RiparianOpportunityError("NMD raster must be one-band EPSG:3006")
    if dataset.transform.b != 0 or dataset.transform.d != 0:
        raise RiparianOpportunityError("Rotated NMD rasters are not supported")
    if not np.allclose(dataset.res, (10.0, 10.0), rtol=0, atol=1e-9):
        raise RiparianOpportunityError(f"NMD raster resolution is {dataset.res}; expected 10 m")
    if not math.isclose(
        abs(float(dataset.transform.a * dataset.transform.e)),
        PIXEL_AREA_M2,
        rel_tol=0,
        abs_tol=1e-6,
    ):
        raise RiparianOpportunityError("NMD raster pixel area is not 100 m²")


def aggregate_nmd_raster(
    raster_path: Path = NMD_RASTER_PATH,
    study_area_path: Path | None = NMD_STUDY_AREA_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate semantic hydrologic counts to every NMD-valid grid position."""

    validate_contract_definition()
    with rasterio.open(raster_path) as dataset:
        _validate_raster(dataset)
        grid = generate_grid(dataset.bounds)
        study_geometry = _read_study_geometry(study_area_path) if study_area_path else None
        col_min = int(grid["grid_col"].min())
        col_max = int(grid["grid_col"].max())
        row_min = int(grid["grid_row"].min())
        row_max = int(grid["grid_row"].max())
        lookup = np.full((col_max - col_min + 1, row_max - row_min + 1), -1, dtype=np.int64)
        lookup[
            grid["grid_col"].to_numpy(dtype=np.int64) - col_min,
            grid["grid_row"].to_numpy(dtype=np.int64) - row_min,
        ] = np.arange(len(grid), dtype=np.int64)
        count_names = GRID_COUNT_COLUMNS[3:]
        counts = {name: np.zeros(len(grid), dtype=np.int64) for name in count_names}
        total_valid_pixels = 0
        total_outside_grid = 0

        for _, window in dataset.block_windows(1):
            data = dataset.read(1, window=window, masked=False)
            valid = dataset.read_masks(1, window=window) > 0
            if study_geometry is not None:
                valid &= rasterize(
                    [(study_geometry, 1)],
                    out_shape=data.shape,
                    transform=window_transform(window, dataset.transform),
                    fill=0,
                    dtype="uint8",
                ).astype(bool)
            valid &= data != 0
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
                total_outside_grid += int(np.count_nonzero(cell_indices < 0))
                keep = cell_indices >= 0
                cell_indices = cell_indices[keep]
                codes = data[valid][keep]
            else:
                codes = data[valid]
            if cell_indices.size == 0:
                continue
            total_valid_pixels += int(cell_indices.size)
            positions, inverse = np.unique(cell_indices, return_inverse=True)
            roles = analytical_role_masks(codes)
            factual = factual_group_masks(codes)
            masks = {
                "nmd_valid_pixels": np.ones(codes.shape, dtype=bool),
                "terrestrial_land_pixels": roles[TERRESTRIAL_LAND],
                "wetland_context_pixels": roles[WETLAND_CONTEXT],
                "inland_water_pixels": roles[INLAND_WATER_CONTEXT],
                "hydrologic_context_pixels": roles[WETLAND_CONTEXT] | roles[INLAND_WATER_CONTEXT],
                "nonmarine_context_pixels": roles[TERRESTRIAL_LAND] | roles[INLAND_WATER_CONTEXT],
                "sea_pixels": factual[SEA],
            }
            for name, mask in masks.items():
                counts[name][positions] += np.bincount(
                    inverse,
                    weights=mask.astype(np.int64),
                    minlength=len(positions),
                ).astype(np.int64)

    valid_positions = counts["nmd_valid_pixels"] > 0
    grid_counts = grid.loc[valid_positions, ["hex_id", "grid_col", "grid_row"]].copy()
    for name in count_names:
        grid_counts[name] = counts[name][valid_positions]
    grid_counts = grid_counts[list(GRID_COUNT_COLUMNS)].reset_index(drop=True)
    _validate_grid_counts(grid_counts)
    if total_outside_grid:
        raise RiparianOpportunityError(
            f"{total_outside_grid} valid NMD pixels fell outside generated grid"
        )
    audit = {
        "nmd_valid_grid_positions": int(len(grid_counts)),
        "nmd_valid_pixels": int(total_valid_pixels),
        "aggregate_counts": {name: int(grid_counts[name].sum()) for name in count_names},
        "study_area_mask_applied": study_geometry is not None,
    }
    return grid_counts, audit


def _distribution(values: pd.Series) -> dict[str, float | int | None]:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return {
            "n": 0,
            "missing": int(numeric.isna().sum()),
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
    quantiles = valid.quantile([0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
        "n": int(len(valid)),
        "missing": int(numeric.isna().sum()),
        "min": float(valid.min()),
        "p10": float(quantiles.loc[0.10]),
        "p25": float(quantiles.loc[0.25]),
        "median": float(quantiles.loc[0.50]),
        "mean": float(valid.mean()),
        "p75": float(quantiles.loc[0.75]),
        "p90": float(quantiles.loc[0.90]),
        "p95": float(quantiles.loc[0.95]),
        "p99": float(quantiles.loc[0.99]),
        "max": float(valid.max()),
    }


def _fraction_bins(values: pd.Series) -> dict[str, int]:
    numeric = pd.to_numeric(values, errors="coerce")
    return {
        "0": int((numeric == 0).sum()),
        ">0–1%": int(((numeric > 0) & (numeric <= 0.01)).sum()),
        ">1–5%": int(((numeric > 0.01) & (numeric <= 0.05)).sum()),
        ">5–10%": int(((numeric > 0.05) & (numeric <= 0.10)).sum()),
        ">10–25%": int(((numeric > 0.10) & (numeric <= 0.25)).sum()),
        ">25–50%": int(((numeric > 0.25) & (numeric <= 0.50)).sum()),
        ">50%": int((numeric > 0.50).sum()),
        "missing": int(numeric.isna().sum()),
    }


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, float | int | None]:
    pair = pd.concat([first, second], axis=1).apply(pd.to_numeric, errors="coerce").dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    first_values = pair.iloc[:, 0]
    second_values = pair.iloc[:, 1]
    return {
        "n": int(len(pair)),
        "pearson": float(first_values.corr(second_values, method="pearson")),
        "spearman": float(
            first_values.rank(method="average").corr(
                second_values.rank(method="average"), method="pearson"
            )
        ),
    }


def _percentage(count: int, total: int) -> float:
    return float(100.0 * count / total) if total else 0.0


def _fraction_field(scale: str) -> str:
    return NEAR_FRACTION_FIELD if scale == "near" else SCALE_FIELDS[scale]["fraction"]


def _near_control_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    focal = frame["riparian_focal_fraction"].to_numpy(dtype=float)
    adjacent = frame["riparian_adjacent_fraction"].to_numpy(dtype=float)
    valid = np.isfinite(focal) & np.isfinite(adjacent)
    focal_wins = valid & (focal > adjacent)
    adjacent_wins = valid & (adjacent > focal)
    ties = valid & (focal == adjacent)
    differences = np.abs(focal[valid] - adjacent[valid])
    return {
        "focal_gt_adjacent": {
            "count": int(focal_wins.sum()),
            "percent": _percentage(int(focal_wins.sum()), len(frame)),
        },
        "adjacent_gt_focal": {
            "count": int(adjacent_wins.sum()),
            "percent": _percentage(int(adjacent_wins.sum()), len(frame)),
        },
        "focal_eq_adjacent": {
            "count": int(ties.sum()),
            "percent": _percentage(int(ties.sum()), len(frame)),
        },
        "comparison_missing_count": int((~valid).sum()),
        "absolute_focal_adjacent_difference": _distribution(pd.Series(differences)),
        "absolute_difference_mean": float(np.mean(differences)) if differences.size else None,
    }


def _threshold_diagnostics(values: pd.Series) -> dict[str, dict[str, float | int]]:
    numeric = pd.to_numeric(values, errors="coerce")
    total = len(numeric)
    thresholds = {
        "near_eq_0": numeric == 0,
        "near_gt_0": numeric > 0,
        "near_ge_1_percent": numeric >= 0.01,
        "near_ge_5_percent": numeric >= 0.05,
        "near_ge_10_percent": numeric >= 0.10,
        "near_ge_25_percent": numeric >= 0.25,
        "near_ge_50_percent": numeric >= 0.50,
    }
    return {
        label: {"count": int(mask.sum()), "percent": _percentage(int(mask.sum()), total)}
        for label, mask in thresholds.items()
    }


def _rank_change_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    pair = (
        frame[["riparian_focal_fraction", NEAR_FRACTION_FIELD]]
        .apply(pd.to_numeric, errors="coerce")
        .dropna()
    )
    if len(pair) < 2:
        return {
            "n": int(len(pair)),
            "spearman": None,
            "median_absolute_percentile_rank_change": None,
            "p90_absolute_percentile_rank_change": None,
            "moved_ge_10_percentile_points": {"count": 0, "percent": 0.0},
            "moved_ge_25_percentile_points": {"count": 0, "percent": 0.0},
        }
    n = len(pair)
    focal_rank = (pair["riparian_focal_fraction"].rank(method="average") - 1) / (n - 1) * 100
    near_rank = (pair[NEAR_FRACTION_FIELD].rank(method="average") - 1) / (n - 1) * 100
    change = (near_rank - focal_rank).abs()
    return {
        "n": int(n),
        "spearman": float(focal_rank.corr(near_rank, method="pearson")),
        "temporary_rank_definition": "100 * (average ascending rank - 1) / (N - 1), calculated in memory for diagnostics only",
        "median_absolute_percentile_rank_change": float(change.median()),
        "p90_absolute_percentile_rank_change": float(change.quantile(0.90)),
        "moved_ge_10_percentile_points": {
            "count": int((change >= 10).sum()),
            "percent": _percentage(int((change >= 10).sum()), n),
        },
        "moved_ge_25_percentile_points": {
            "count": int((change >= 25).sum()),
            "percent": _percentage(int((change >= 25).sum()), n),
        },
    }


def _character_diagnostics(
    frame: pd.DataFrame, scale: str, mask: pd.Series | np.ndarray | None = None
) -> dict[str, Any]:
    fields = SCALE_FIELDS[scale]
    subset = frame if mask is None else frame.loc[mask]
    wetland = subset[fields["wetland_pixels"]].to_numpy(dtype=float)
    water = subset[fields["water_pixels"]].to_numpy(dtype=float)
    categories = {
        "wetland": wetland > water,
        "inland_water": water > wetland,
        "mixed_tie": wetland == water,
    }
    return {
        "scale": scale,
        "candidate_count": int(len(subset)),
        "wetland_pixel_sum": int(wetland.sum()),
        "inland_water_pixel_sum": int(water.sum()),
        "wetland_fraction_mean": float(subset[fields["wetland_fraction"]].mean())
        if len(subset)
        else None,
        "inland_water_fraction_mean": float(subset[fields["water_fraction"]].mean())
        if len(subset)
        else None,
        "primarily": {
            label: {
                "count": int(category.sum()),
                "percent": _percentage(int(category.sum()), len(subset)),
            }
            for label, category in categories.items()
        },
        "rule": "wetland contribution > inland-water contribution => wetland; inland-water contribution > wetland contribution => inland water; equality => mixed/tie",
    }


def _same_habitat_context_contrasts(joined: pd.DataFrame) -> dict[str, Any]:
    tolerance = 0.5
    ordered = joined.sort_values(["habitat_context_score", "hex_id"], kind="mergesort").reset_index(
        drop=True
    )
    pairs: list[dict[str, Any]] = []
    for index in range(len(ordered) - 1):
        first = ordered.iloc[index]
        second = ordered.iloc[index + 1]
        if (
            abs(float(first.habitat_context_score) - float(second.habitat_context_score))
            <= tolerance
        ):
            pairs.append(
                {
                    "habitat_score_difference": abs(
                        float(first.habitat_context_score) - float(second.habitat_context_score)
                    ),
                    "focal_fraction_difference": abs(
                        float(first.riparian_focal_fraction) - float(second.riparian_focal_fraction)
                    ),
                    "near_fraction_difference": abs(
                        float(first.riparian_near_fraction) - float(second.riparian_near_fraction)
                    ),
                    "first": {
                        "hex_id": str(first.hex_id),
                        "habitat_context_score": float(first.habitat_context_score),
                        "riparian_focal_fraction": float(first.riparian_focal_fraction),
                        "riparian_near_fraction": float(first.riparian_near_fraction),
                    },
                    "second": {
                        "hex_id": str(second.hex_id),
                        "habitat_context_score": float(second.habitat_context_score),
                        "riparian_focal_fraction": float(second.riparian_focal_fraction),
                        "riparian_near_fraction": float(second.riparian_near_fraction),
                    },
                }
            )
    return {
        "habitat_score_tolerance_points": tolerance,
        "method": "adjacent rows after sorting by Habitat Context score; retain pairs within tolerance and show largest riparian differences",
        "focal": sorted(pairs, key=lambda item: item["focal_fraction_difference"], reverse=True)[
            :3
        ],
        "near": sorted(pairs, key=lambda item: item["near_fraction_difference"], reverse=True)[:3],
    }


def _top_tail_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    ordered = joined.sort_values(
        [NEAR_FRACTION_FIELD, "hex_id"], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)
    for label, proportion in (
        ("top_10_percent", 0.10),
        ("top_5_percent", 0.05),
        ("top_1_percent", 0.01),
    ):
        count = max(1, int(math.ceil(len(ordered) * proportion)))
        subset = ordered.head(count)
        diagnostics[label] = {
            "candidate_count": int(len(subset)),
            "near_cutoff_included": float(subset[NEAR_FRACTION_FIELD].min()),
            "habitat_context_score": _distribution(subset["habitat_context_score"]),
            "focal_contributions": _character_diagnostics(subset, "focal"),
            "adjacent_contributions": _character_diagnostics(subset, "adjacent"),
        }
    return diagnostics


def _boundary_top_tail_diagnostics(joined: pd.DataFrame) -> dict[str, Any]:
    ordered = joined.sort_values(
        [NEAR_FRACTION_FIELD, "hex_id"], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)
    result: dict[str, Any] = {}
    for label, proportion in (
        ("top_10_percent", 0.10),
        ("top_5_percent", 0.05),
        ("top_1_percent", 0.01),
    ):
        count = max(1, int(math.ceil(len(ordered) * proportion)))
        subset = ordered.head(count)
        edge_count = int(subset["boundary_edge_flag"].sum())
        result[label] = {
            "top_candidate_count": int(len(subset)),
            "boundary_edge_count": edge_count,
            "boundary_edge_percent_of_top": _percentage(edge_count, len(subset)),
            "boundary_edge_percent_of_population": _percentage(edge_count, len(joined)),
        }
    return result


def _scale_summary(frame: pd.DataFrame, scale: str) -> dict[str, Any]:
    fields = SCALE_FIELDS[scale]
    fraction = frame[fields["fraction"]]
    wetland = frame[fields["wetland_pixels"]]
    water = frame[fields["water_pixels"]]
    categories = {
        "wetland_only": (wetland > 0) & (water == 0),
        "inland_water_only": (wetland == 0) & (water > 0),
        "both_wetland_and_inland_water": (wetland > 0) & (water > 0),
        "neither": (wetland == 0) & (water == 0),
    }
    presence = {
        "any_wetland": wetland > 0,
        "any_inland_water": water > 0,
        "any_hydrologic_context": frame[fields["hydrologic_pixels"]] > 0,
    }
    return {
        "fraction_distribution": _distribution(fraction),
        "bins": _fraction_bins(fraction),
        "wetland_fraction_mean": float(frame[fields["wetland_fraction"]].mean()),
        "wetland_fraction_median": float(frame[fields["wetland_fraction"]].median()),
        "inland_water_fraction_mean": float(frame[fields["water_fraction"]].mean()),
        "inland_water_fraction_median": float(frame[fields["water_fraction"]].median()),
        "hydrologic_numerator_categories": {
            label: {"count": int(mask.sum()), "percent": _percentage(int(mask.sum()), len(frame))}
            for label, mask in categories.items()
        },
        "presence_counts": {
            label: {"count": int(mask.sum()), "percent": _percentage(int(mask.sum()), len(frame))}
            for label, mask in presence.items()
        },
        "position_presence": {
            "positions_present": _distribution(frame[fields["positions_present"]]),
            "nonmarine_positions_present": _distribution(frame[fields["nonmarine_positions"]]),
        },
    }


def _load_joined_artifact(
    path: Path,
    required_columns: tuple[str, ...],
    candidate_ids: set[str],
    label: str,
) -> pd.DataFrame:
    frame = pd.read_csv(path)
    _require_columns(frame, ("hex_id", *required_columns), label)
    ids = frame["hex_id"].astype(str)
    if ids.duplicated().any() or set(ids) != candidate_ids or len(ids) != len(candidate_ids):
        raise RiparianOpportunityError(f"{label} IDs do not reconcile with candidates")
    return frame.assign(hex_id=ids).set_index("hex_id")[list(required_columns)]


def _record(row: pd.Series, fields: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = row[field]
        if pd.isna(value):
            result[field] = None
        elif field == "hex_id":
            result[field] = str(value)
        elif isinstance(value, (np.bool_, bool)):
            result[field] = bool(value)
        elif isinstance(value, (np.integer, int)):
            result[field] = int(value)
        else:
            result[field] = float(value)
    return result


def _top_near_examples(joined: pd.DataFrame, limit: int = 10) -> list[dict[str, Any]]:
    sorted_rows = joined.sort_values(
        [NEAR_FRACTION_FIELD, "hex_id"], ascending=[False, True], kind="mergesort"
    ).head(limit)
    examples: list[dict[str, Any]] = []
    for _, row in sorted_rows.iterrows():
        focal = float(row["riparian_focal_fraction"])
        adjacent = float(row["riparian_adjacent_fraction"])
        controlling = "focal" if focal > adjacent else "adjacent" if adjacent > focal else "tie"
        contribution_scale = "focal" if controlling in ("focal", "tie") else "adjacent"
        contribution_fields = SCALE_FIELDS[contribution_scale]
        examples.append(
            {
                "hex_id": str(row["hex_id"]),
                "riparian_focal_fraction": focal,
                "riparian_adjacent_fraction": adjacent,
                "riparian_near_fraction": float(row[NEAR_FRACTION_FIELD]),
                "riparian_local_fraction": float(row["riparian_local_fraction"]),
                "controlling_scale": controlling,
                "relevant_wetland_contribution_pixels": int(
                    row[contribution_fields["wetland_pixels"]]
                ),
                "relevant_inland_water_contribution_pixels": int(
                    row[contribution_fields["water_pixels"]]
                ),
                "focal_wetland_contribution_pixels": int(
                    row[SCALE_FIELDS["focal"]["wetland_pixels"]]
                ),
                "focal_inland_water_contribution_pixels": int(
                    row[SCALE_FIELDS["focal"]["water_pixels"]]
                ),
                "adjacent_wetland_contribution_pixels": int(
                    row[SCALE_FIELDS["adjacent"]["wetland_pixels"]]
                ),
                "adjacent_inland_water_contribution_pixels": int(
                    row[SCALE_FIELDS["adjacent"]["water_pixels"]]
                ),
                "habitat_context_score": float(row["habitat_context_score"]),
                "ecological_network_score": float(row["ecological_network_score"]),
                "candidate_fraction_of_terrestrial": float(
                    row["candidate_fraction_of_terrestrial"]
                ),
                "boundary_edge_flag": bool(row["boundary_edge_flag"]),
                "sea_presence": bool(row["contains_sea_pixels"]),
            }
        )
    return examples


def _top_examples(joined: pd.DataFrame, scale: str, limit: int = 10) -> list[dict[str, Any]]:
    fields = SCALE_FIELDS[scale]
    sorted_rows = joined.sort_values(
        [fields["fraction"], "hex_id"], ascending=[False, True], kind="mergesort"
    ).head(limit)
    return [
        _record(
            row,
            (
                "hex_id",
                "riparian_focal_fraction",
                "riparian_adjacent_fraction",
                "riparian_local_fraction",
                "focal_wetland_fraction",
                "focal_inland_water_fraction",
                "adjacent_wetland_fraction",
                "adjacent_inland_water_fraction",
                "local_wetland_fraction",
                "local_inland_water_fraction",
                "habitat_context_score",
                "ecological_network_score",
                "candidate_fraction_of_terrestrial",
                "boundary_edge_flag",
                "contains_sea_pixels",
            ),
        )
        for _, row in sorted_rows.iterrows()
    ]


def _group_diagnostics(joined: pd.DataFrame, group_field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, mask in (("true", joined[group_field]), ("false", ~joined[group_field])):
        subset = joined.loc[mask]
        result[label] = {
            "candidate_count": int(len(subset)),
            "candidate_percent": _percentage(len(subset), len(joined)),
            "indicator_distributions": {
                scale: _distribution(subset[_fraction_field(scale)]) for scale in RIPARIAN_SCALES
            },
        }
    return result


def _step6_comparison(
    candidates: pd.DataFrame,
    full_grid: pd.DataFrame,
    step6_grid: pd.DataFrame,
) -> dict[str, Any]:
    full = calculate_indicators(candidates, full_grid)
    terrestrial_only = calculate_indicators(candidates, step6_grid)
    comparison: dict[str, Any] = {}
    all_affected = np.zeros(len(candidates), dtype=bool)
    for scale in ("adjacent", "local"):
        field = SCALE_FIELDS[scale]["fraction"]
        differences = full[field].to_numpy(dtype=float) - terrestrial_only[field].to_numpy(
            dtype=float
        )
        finite = np.isfinite(differences)
        affected = finite & (np.abs(differences) > 1e-12)
        absolute = np.abs(differences[affected])
        all_affected |= affected
        comparison[scale] = {
            "affected_candidate_count": int(affected.sum()),
            "affected_candidate_percent": _percentage(int(affected.sum()), len(candidates)),
            "median_absolute_difference": float(np.median(absolute)) if absolute.size else 0.0,
            "maximum_absolute_difference": float(np.max(absolute)) if absolute.size else 0.0,
            "zero_denominator_full_count": int(full[field].isna().sum()),
            "zero_denominator_step6_only_count": int(terrestrial_only[field].isna().sum()),
        }
    comparison["any_adjacent_or_local_difference_count"] = int(all_affected.sum())
    comparison["any_adjacent_or_local_difference_percent"] = _percentage(
        int(all_affected.sum()), len(candidates)
    )
    return comparison


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    frame.to_csv(
        temporary_path,
        index=False,
        columns=list(OUTPUT_COLUMNS),
        float_format=f"%.{CSV_FLOAT_PRECISION}f",
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


def build_riparian_opportunity(
    raster_path: Path = NMD_RASTER_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    study_area_path: Path = NMD_STUDY_AREA_PATH,
    output_path: Path = OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
    habitat_raw_path: Path = HABITAT_RAW_PATH,
    habitat_score_path: Path = HABITAT_SCORE_PATH,
    network_score_path: Path = NETWORK_SCORE_PATH,
    step6_analysis_units_path: Path = STEP6_ANALYSIS_UNITS_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Generate raw riparian indicators, audit relationships, and write artifacts."""

    start = time.perf_counter()
    candidates_geo = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
    _validate_candidate_frame(candidates_geo)
    if candidates_geo.crs is None or str(candidates_geo.crs) != TARGET_CRS:
        raise RiparianOpportunityError("Candidate CRS must be EPSG:3006")
    candidate_ids = set(candidates_geo["hex_id"].astype(str))
    candidate_attributes = pd.DataFrame(candidates_geo.drop(columns="geometry")).copy()
    candidate_attributes["hex_id"] = candidate_attributes["hex_id"].astype(str)

    grid_counts, grid_audit = aggregate_nmd_raster(raster_path, study_area_path)
    study_geometry = _read_study_geometry(study_area_path)
    edge_flags = calculate_boundary_edge_flags(
        candidates_geo, study_geometry, threshold_m=BOUNDARY_EDGE_DISTANCE_M
    )
    internal = calculate_indicators(candidate_attributes, grid_counts, edge_flags=edge_flags)
    output = internal.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    output = output[list(OUTPUT_COLUMNS)]
    output_ids = set(output["hex_id"])
    missing_ids = sorted(candidate_ids - output_ids)
    extra_ids = sorted(output_ids - candidate_ids)
    if (
        missing_ids
        or extra_ids
        or len(output) != len(candidates_geo)
        or output["hex_id"].duplicated().any()
    ):
        raise RiparianOpportunityError("Riparian output IDs do not exactly match candidates")

    raw_sorted = internal.sort_values("hex_id", kind="mergesort").reset_index(drop=True)
    joined = candidate_attributes.merge(raw_sorted, on="hex_id", how="left", validate="one_to_one")
    joined["contains_sea_pixels"] = joined["sea_pixels"].to_numpy(dtype=float) > 0
    joined["artificial_constraint_fraction_of_terrestrial"] = joined[
        "artificial_constraint_pixels"
    ].to_numpy(dtype=float) / joined["terrestrial_pixels"].to_numpy(dtype=float)
    habitat_raw = _load_joined_artifact(
        habitat_raw_path, ("habitat_context_local_fraction",), candidate_ids, "Habitat Context raw"
    )
    habitat_score = _load_joined_artifact(
        habitat_score_path, ("habitat_context_score",), candidate_ids, "Habitat Context score"
    )
    network_score = _load_joined_artifact(
        network_score_path, ("ecological_network_score",), candidate_ids, "Ecological Network score"
    )
    joined = joined.set_index("hex_id").join(habitat_raw, how="left")
    joined = joined.join(habitat_score, how="left")
    joined = joined.join(network_score, how="left").reset_index()
    joined["boundary_edge_flag"] = joined["boundary_edge_flag"].astype(bool)

    scales = list(SCALE_FIELDS)
    audit_scales = list(RIPARIAN_SCALES)
    scale_summaries = {scale: _scale_summary(joined, scale) for scale in scales}
    near_summary = {
        "fraction_distribution": _distribution(joined[NEAR_FRACTION_FIELD]),
        "bins": _fraction_bins(joined[NEAR_FRACTION_FIELD]),
    }
    zero_denominators = {
        scale: int(joined[SCALE_FIELDS[scale]["fraction"]].isna().sum()) for scale in scales
    }
    scale_correlations = {
        "focal_vs_adjacent": _correlation(
            joined["riparian_focal_fraction"], joined["riparian_adjacent_fraction"]
        ),
        "near_vs_focal": _correlation(
            joined[NEAR_FRACTION_FIELD], joined["riparian_focal_fraction"]
        ),
        "near_vs_adjacent": _correlation(
            joined[NEAR_FRACTION_FIELD], joined["riparian_adjacent_fraction"]
        ),
        "near_vs_local": _correlation(
            joined[NEAR_FRACTION_FIELD], joined["riparian_local_fraction"]
        ),
        "focal_vs_local": _correlation(
            joined["riparian_focal_fraction"], joined["riparian_local_fraction"]
        ),
        "adjacent_vs_local": _correlation(
            joined["riparian_adjacent_fraction"], joined["riparian_local_fraction"]
        ),
    }
    distinctness = {
        scale: {
            "habitat_context_local_fraction": _correlation(
                joined[_fraction_field(scale)], joined["habitat_context_local_fraction"]
            ),
            "habitat_context_score": _correlation(
                joined[_fraction_field(scale)], joined["habitat_context_score"]
            ),
            "ecological_network_context_score": _correlation(
                joined[_fraction_field(scale)], joined["ecological_network_score"]
            ),
        }
        for scale in audit_scales
    }
    composition_fields = {
        "candidate_fraction_of_terrestrial": "candidate_fraction_of_terrestrial",
        "candidate_area_m2": "candidate_area_m2",
        "habitat_context_fraction_of_terrestrial": "habitat_context_fraction_of_terrestrial",
        "artificial_constraint_fraction_of_terrestrial": "artificial_constraint_fraction_of_terrestrial",
    }
    composition_correlations = {
        scale: {
            field: _correlation(joined[_fraction_field(scale)], joined[source_field])
            for field, source_field in composition_fields.items()
        }
        for scale in audit_scales
    }

    near_controls = _near_control_diagnostics(joined)
    near_thresholds = _threshold_diagnostics(joined[NEAR_FRACTION_FIELD])
    rank_changes = _rank_change_diagnostics(joined)
    focal_gt_adjacent_mask = (
        joined["riparian_focal_fraction"] > joined["riparian_adjacent_fraction"]
    )
    adjacent_gt_focal_mask = (
        joined["riparian_adjacent_fraction"] > joined["riparian_focal_fraction"]
    )
    controlling_character = {
        "focal_gt_adjacent": _character_diagnostics(joined, "focal", focal_gt_adjacent_mask),
        "adjacent_gt_focal": _character_diagnostics(joined, "adjacent", adjacent_gt_focal_mask),
        "rule": "For each controlling group, compare that scale's wetland and inland-water pixel contributions; larger contribution is primary and equality is mixed/tie.",
    }

    rescue_masks = {
        "focal_le_1_percent_adjacent_ge_10_percent": (joined["riparian_focal_fraction"] <= 0.01)
        & (joined["riparian_adjacent_fraction"] >= 0.10),
        "focal_le_1_percent_adjacent_ge_25_percent": (joined["riparian_focal_fraction"] <= 0.01)
        & (joined["riparian_adjacent_fraction"] >= 0.25),
        "focal_ge_10_percent_adjacent_le_1_percent": (joined["riparian_focal_fraction"] >= 0.10)
        & (joined["riparian_adjacent_fraction"] <= 0.01),
    }
    rescue_diagnostics = {
        label: {
            "candidate_count": int(mask.sum()),
            "candidate_percent": _percentage(int(mask.sum()), len(joined)),
            "median_habitat_context_score": float(
                joined.loc[mask, "habitat_context_score"].median()
            )
            if mask.any()
            else None,
            "median_network_context_score": float(
                joined.loc[mask, "ecological_network_score"].median()
            )
            if mask.any()
            else None,
        }
        for label, mask in rescue_masks.items()
    }

    step6_units = gpd.read_file(step6_analysis_units_path, layer=STEP6_ANALYSIS_UNITS_LAYER)
    _require_columns(
        step6_units,
        (
            "hex_id",
            "grid_col",
            "grid_row",
            "terrestrial_pixels",
            "wetland_context_pixels",
            "inland_water_pixels",
        ),
        "Step 6 analysis units",
    )
    step6_grid = step6_units[
        [
            "hex_id",
            "grid_col",
            "grid_row",
            "terrestrial_pixels",
            "wetland_context_pixels",
            "inland_water_pixels",
        ]
    ].rename(
        columns={
            "terrestrial_pixels": "terrestrial_land_pixels",
            "wetland_context_pixels": "wetland_context_pixels",
            "inland_water_pixels": "inland_water_pixels",
        }
    )
    step6_grid["nmd_valid_pixels"] = (
        step6_grid["terrestrial_land_pixels"]
        + step6_grid["inland_water_pixels"]
        + step6_units.loc[step6_grid.index, "sea_pixels"]
    )
    step6_grid["hydrologic_context_pixels"] = (
        step6_grid["wetland_context_pixels"] + step6_grid["inland_water_pixels"]
    )
    step6_grid["nonmarine_context_pixels"] = (
        step6_grid["terrestrial_land_pixels"] + step6_grid["inland_water_pixels"]
    )
    step6_grid["sea_pixels"] = step6_units.loc[step6_grid.index, "sea_pixels"].to_numpy()
    step6_grid = step6_grid[list(GRID_COUNT_COLUMNS)].reset_index(drop=True)
    _validate_grid_counts(step6_grid)
    water_only_mask = (grid_counts["terrestrial_land_pixels"] == 0) & (
        grid_counts["inland_water_pixels"] > 0
    )
    water_only_support = {
        "nmd_valid_grid_positions_represented": int(len(grid_counts)),
        "water_only_inland_grid_positions": int(water_only_mask.sum()),
        "water_only_inland_pixels": int(
            grid_counts.loc[water_only_mask, "inland_water_pixels"].sum()
        ),
        "step6_terrestrial_analysis_unit_positions": int(len(step6_grid)),
        "step6_positions_omitted_from_component_grid": int(
            len(set(grid_counts.hex_id) - set(step6_grid.hex_id))
        ),
        "difference_vs_step6_terrestrial_only": _step6_comparison(
            candidate_attributes, grid_counts, step6_grid
        ),
    }

    offset_sanity = {
        "ring_1_offsets": [list(offset) for offset in RING_1_OFFSETS],
        "local_offsets": [list(offset) for offset in LOCAL_OFFSETS],
        "ring_1_count": len(RING_1_OFFSETS),
        "local_count": len(LOCAL_OFFSETS),
        "ring_1_offsets_match_step8_convention": RING_1_OFFSETS
        == ((-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0)),
        "local_excludes_focal": (0, 0) not in LOCAL_OFFSETS,
        "focal_uses_only_focal_grid_pixels": True,
        "water_only_adjacent_candidate_count": int(
            (joined[SCALE_FIELDS["adjacent"]["water_only_positions"]] > 0).sum()
        ),
    }
    boundary_diagnostics = {
        "definition": "Candidate centroid within 1,000 m of the dissolved Skåne study-area boundary; reused Step 8 rule.",
        "threshold_m": BOUNDARY_EDGE_DISTANCE_M,
        "candidate_count": int(len(joined)),
        "edge_count": int(joined["boundary_edge_flag"].sum()),
        "edge_percent": _percentage(int(joined["boundary_edge_flag"].sum()), len(joined)),
        "by_boundary_flag": _group_diagnostics(joined, "boundary_edge_flag"),
        "top_tail_boundary_counts": _boundary_top_tail_diagnostics(joined),
    }
    all_raw_fraction_fields = [
        "riparian_focal_fraction",
        "riparian_adjacent_fraction",
        NEAR_FRACTION_FIELD,
        "riparian_local_fraction",
    ]
    raw_fraction_values = joined[all_raw_fraction_fields].to_numpy(dtype=float)
    near_values = joined[NEAR_FRACTION_FIELD].to_numpy(dtype=float)
    focal_values = joined["riparian_focal_fraction"].to_numpy(dtype=float)
    adjacent_values = joined["riparian_adjacent_fraction"].to_numpy(dtype=float)
    finite_pair = np.isfinite(focal_values) & np.isfinite(adjacent_values)
    expected_near = np.fmax(focal_values[finite_pair], adjacent_values[finite_pair])
    near_validation = {
        "near_formula": "riparian_near_fraction = max(riparian_focal_fraction, riparian_adjacent_fraction)",
        "near_range": [0, 1],
        "all_raw_fractions_finite": bool(np.isfinite(raw_fraction_values).all()),
        "all_raw_fractions_in_0_1": bool(
            ((raw_fraction_values >= 0) & (raw_fraction_values <= 1)).all()
        ),
        "near_equals_exact_max_count": int(
            np.count_nonzero(near_values[finite_pair] == expected_near)
        ),
        "near_equals_exact_max": bool(np.array_equal(near_values[finite_pair], expected_near)),
        "near_ge_focal_count": int(
            np.count_nonzero(near_values[finite_pair] >= focal_values[finite_pair])
        ),
        "near_ge_focal": bool(np.all(near_values[finite_pair] >= focal_values[finite_pair])),
        "near_ge_adjacent_count": int(
            np.count_nonzero(near_values[finite_pair] >= adjacent_values[finite_pair])
        ),
        "near_ge_adjacent": bool(np.all(near_values[finite_pair] >= adjacent_values[finite_pair])),
        "focal_eq_adjacent_near_eq_both_count": int(
            np.count_nonzero(
                (focal_values[finite_pair] == adjacent_values[finite_pair])
                & (near_values[finite_pair] == focal_values[finite_pair])
            )
        ),
        "no_nan_or_inf_in_real_output": bool(np.isfinite(near_values).all()),
    }
    same_context_contrasts = _same_habitat_context_contrasts(joined)
    high_near_diagnostics = _top_tail_diagnostics(joined)
    selection_comparison = {
        "focal": {
            "distribution": _distribution(joined["riparian_focal_fraction"]),
            "bins": _fraction_bins(joined["riparian_focal_fraction"]),
            "zero_rate": _percentage(
                int((joined["riparian_focal_fraction"] == 0).sum()), len(joined)
            ),
            "habitat_context_correlations": distinctness["focal"],
            "network_context_correlations": distinctness["focal"][
                "ecological_network_context_score"
            ],
            "candidate_composition_correlations": composition_correlations["focal"],
            "boundary_rescue_behavior": rescue_diagnostics,
            "rank_change_against_near": rank_changes,
            "ease_of_interpretation": "Direct hydrologic-context fraction inside the candidate hex.",
            "known_weaknesses": [
                "Sensitive to whether a hydrologic feature falls inside the arbitrary focal hex boundary.",
                "Can miss strong immediately adjacent context when the focal cell contains little hydrologic context.",
            ],
        },
        "near_max_focal_adjacent": {
            "formula": "max(riparian_focal_fraction, riparian_adjacent_fraction)",
            "distribution": near_summary["fraction_distribution"],
            "bins": near_summary["bins"],
            "zero_rate": _percentage(int((joined[NEAR_FRACTION_FIELD] == 0).sum()), len(joined)),
            "habitat_context_correlations": distinctness["near"],
            "network_context_correlations": distinctness["near"][
                "ecological_network_context_score"
            ],
            "candidate_composition_correlations": composition_correlations["near"],
            "boundary_rescue_behavior": rescue_diagnostics,
            "rank_change_against_focal": rank_changes,
            "ease_of_interpretation": "The stronger raw hydrologic-context signal observed in the focal or six directly adjacent positions.",
            "known_weaknesses": [
                "May be more correlated with Habitat Context than focal alone.",
                "Can favor noisy extreme values and overlap with wetlands already represented by Habitat Context.",
                "Does not distinguish focal from adjacent support after taking the maximum.",
            ],
        },
        "final_scale_selected": False,
    }
    provenance: dict[str, Any] = {
        "component_working_name": "Riparian Opportunity",
        "status": "RAW INDICATORS UNDER AUDIT; NO FINAL SCALE OR SCORE SELECTED",
        "source": {
            "nmd_raster_path": str(raster_path),
            "nmd_version": NMD_VERSION,
            "candidate_source_path": str(candidate_units_path),
            "candidate_source_layer": CANDIDATE_UNITS_LAYER,
            "study_area_path": str(study_area_path),
            "semantic_role_source": "src/restoration_prioritizer/nmd_semantics.py",
        },
        "grid_convention": {
            "crs": TARGET_CRS,
            "flat_to_flat_width_m": 500.0,
            "fixed_origin_m": [0.0, 0.0],
            "center_formula": "x=(grid_col + grid_row/2)*500; y=grid_row*1.5*(500/sqrt(3))",
            "pixel_assignment": "Reuse analysis_units.pixel_centers_to_grid_indices; NMD pixel centers assigned exactly once; all_touched=false.",
            "grid_assignment_logic_reused": True,
        },
        "semantic_definition": {
            "wetland_context_codes": sorted(
                code
                for group in ROLE_FACTUAL_GROUPS[WETLAND_CONTEXT]
                for code in FACTUAL_GROUP_CODES[group]
            ),
            "wetland_context_factual_groups": list(ROLE_FACTUAL_GROUPS[WETLAND_CONTEXT]),
            "inland_water_code": sorted(FACTUAL_GROUP_CODES[INLAND_WATER])[0],
            "inland_water_factual_group": INLAND_WATER,
            "sea_code_excluded": sorted(FACTUAL_GROUP_CODES[SEA])[0],
            "no_data_code_excluded": sorted(FACTUAL_GROUP_CODES[NO_DATA])[0],
            "hydrologic_union": "wetland_context_pixels + inland_water_pixels; mutually exclusive factual roles, no double count",
            "source_contract_unchanged": True,
        },
        "denominator_definition": "nonmarine_context_pixels = terrestrial_land_pixels + inland_water_pixels; wetland is already terrestrial; sea and code 0/no-data are excluded.",
        "neighborhood_definition": {
            "focal": "pixels assigned to the focal candidate grid cell",
            "adjacent": "six positions with hex distance exactly 1; focal excluded",
            "local": "18 positions with 1 <= hex distance <= 2; focal excluded",
            "aggregation": "sum raw pixel counts first, then divide; no averaging of per-cell fractions",
            "missing_positions": "no numerator or denominator",
            "sea_only_positions": "no numerator or denominator",
            "water_only_positions": "included when NMD-valid and inland-water pixels are positive, even if terrestrial pixels are zero",
            "zero_denominator": "fraction is missing/NaN and is reported; never silently assigned zero",
        },
        "near_indicator": {
            "field": NEAR_FRACTION_FIELD,
            "formula": "riparian_near_fraction = max(riparian_focal_fraction, riparian_adjacent_fraction)",
            "range": [0, 1],
            "interpretation": "The stronger hydrologic-context signal observed either within the candidate hex itself or across its six directly adjacent hex positions.",
            "rationale": "Reduce sensitivity to arbitrary placement of a wetland/lake relative to a 500 m hex boundary without inventing weights, diluting focal context, or adding distance weighting.",
            "not_used": [
                "average",
                "weighted average",
                "sum",
                "union/probability formula",
                "multiplication",
                "Habitat Context adjustment",
                "distance weighting",
            ],
        },
        "candidate_population_validation": {
            "candidate_input_count": int(len(candidates_geo)),
            "output_rows": int(len(output)),
            "duplicate_output_id_count": int(output["hex_id"].duplicated().sum()),
            "missing_candidate_ids": len(missing_ids),
            "extra_output_ids": len(extra_ids),
            "ids_reconcile_exactly": not missing_ids
            and not extra_ids
            and len(output) == len(candidates_geo),
        },
        "zero_denominator_counts": zero_denominators,
        "indicator_distributions": scale_summaries,
        "near_distribution": near_summary,
        "near_control_diagnostics": near_controls,
        "near_threshold_diagnostics": near_thresholds,
        "near_validation": near_validation,
        "scale_correlations": scale_correlations,
        "distinctness_from_finalized_components": distinctness,
        "wetland_vs_inland_water_contributions": scale_summaries,
        "candidate_composition_relationships": composition_correlations,
        "controlling_scale_wetland_vs_inland_water": controlling_character,
        "focal_vs_near_rank_change_diagnostics": rank_changes,
        "hex_boundary_rescue_diagnostics": rescue_diagnostics,
        "sea_effect_diagnostics": _group_diagnostics(joined, "contains_sea_pixels"),
        "boundary_diagnostics": boundary_diagnostics,
        "hydrologic_presence_counts": {
            scale: scale_summaries[scale]["presence_counts"] for scale in scales
        },
        "water_only_grid_support_check": water_only_support,
        "top_raw_riparian_examples": {scale: _top_examples(joined, scale) for scale in scales},
        "top_near_examples": _top_near_examples(joined),
        "same_habitat_context_contrasts": same_context_contrasts,
        "high_near_low_habitat_diagnostics": high_near_diagnostics,
        "factual_focal_vs_near_selection_comparison": selection_comparison,
        "spatial_sanity": offset_sanity,
        "output": {
            "path": str(output_path),
            "schema": list(OUTPUT_COLUMNS),
            "row_count": int(len(output)),
        },
        "caveats": [
            "Narrow streams may be underrepresented by NMD2023 at 10 m categorical resolution.",
            "No separate stream vector network is used.",
            "These are hydrologic land-cover context indicators, not functional hydrology, flood risk, water quality, stream order, catchment function, groundwater, connectivity, buffer suitability, or feasibility.",
            "NMD inland water and wetland are adequate for the contained MVP context audit but are not equivalent to a detailed hydrographic dataset.",
            "The current approximately 1 km county-edge limitation remains; context outside Skåne is unseen and is not corrected here.",
            "No final riparian scale, normalization, score, weights, or overall restoration score is selected in Step 14.",
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - start,
        "dependencies": "No new dependencies; NumPy, pandas, Rasterio, GeoPandas, and existing project modules only.",
        "environmental_data_downloaded": False,
    }
    _write_csv(output, output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    _write_json(provenance_path, provenance)
    return output, provenance


def main() -> None:
    """Generate and summarize the real-data raw Riparian Opportunity artifact."""

    indicators, provenance = build_riparian_opportunity()
    output = provenance["output"]
    print(f"Riparian Opportunity indicators: {output['path']} ({output['size_bytes']:,} bytes)")
    print(
        f"Candidates {len(indicators):,}; zero denominators {provenance['zero_denominator_counts']}"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")


if __name__ == "__main__":
    main()
