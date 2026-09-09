"""Derive raw terrestrial Protected-Area Reinforcement indicators.

Step 17 deliberately keeps the approved protected-area footprint as source
geometry.  Its analytical support is the pixel-center intersection of that
footprint with the approved NMD ``terrestrial_land`` role.  The module stops
at raw focal/context/proximity diagnostics: it does not choose a scale,
normalize, score, or calculate overall prioritization.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import transform as window_transform
from shapely.geometry import box
from shapely.prepared import prep

from restoration_prioritizer.analysis_units import (
    generate_grid,
    hex_center,
    pixel_centers_to_grid_indices,
)
from restoration_prioritizer.config import TARGET_CRS
from restoration_prioritizer.habitat_context import (
    LOCAL_OFFSETS,
    RING_1_OFFSETS,
    calculate_boundary_edge_flags,
)
from restoration_prioritizer.nmd_semantics import (
    NMD_RASTER_PATH,
    TERRESTRIAL_LAND,
    analytical_role_masks,
    validate_contract_definition,
)

NMD_VERSION = "NMD2023 v2.1"
PROTECTED_FOOTPRINT_PATH = Path("data/processed/protected_areas.gpkg")
PROTECTED_FOOTPRINT_LAYER = "protected_footprint"
STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"
ANALYSIS_UNITS_PATH = Path("data/processed/analysis_units.gpkg")
ANALYSIS_UNITS_LAYER = "analysis_units"
CANDIDATE_UNITS_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_UNITS_LAYER = "candidate_units"
HABITAT_RAW_PATH = Path("data/processed/indicators/habitat_context.csv")
HABITAT_SCORE_PATH = Path("data/processed/components/habitat_context.csv")
NETWORK_SCORE_PATH = Path("data/processed/components/ecological_network.csv")
RIPARIAN_RAW_PATH = Path("data/processed/indicators/riparian_opportunity.csv")
RIPARIAN_SCORE_PATH = Path("data/processed/components/riparian_opportunity.csv")
STEP16_PROVENANCE_PATH = Path("data/processed/protected_areas.provenance.json")
OUTPUT_PATH = Path("data/processed/indicators/protected_area_reinforcement.csv")
GRID_OUTPUT_PATH = Path("data/processed/protected_terrestrial_grid.csv")
PROVENANCE_PATH = Path("data/processed/indicators/protected_area_reinforcement.provenance.json")

PIXEL_AREA_M2 = 100.0
HEX_STEP_NOMINAL_M = 500.0
BOUNDARY_EDGE_DISTANCE_M = 1_000.0
CSV_FLOAT_PRECISION = 10

GRID_COLUMNS = (
    "grid_col",
    "grid_row",
    "terrestrial_pixels",
    "protected_terrestrial_pixels",
    "protected_terrestrial_fraction",
)
OUTPUT_COLUMNS = (
    "hex_id",
    "protected_focal_fraction",
    "protected_adjacent_fraction",
    "protected_local_fraction",
    "nearest_protected_hex_steps",
    "nearest_protected_nominal_m",
    "boundary_edge_flag",
)


class ProtectedAreaReinforcementError(ValueError):
    """Raised when the Step 17 source or analytical contract is malformed."""


def protected_terrestrial_mask(
    codes: np.ndarray | list[int], protected_mask: np.ndarray | list[bool]
) -> np.ndarray:
    """Return pixels that are both formally protected and approved terrestrial land."""

    values = np.asarray(codes)
    protected = np.asarray(protected_mask, dtype=bool)
    if values.shape != protected.shape:
        raise ProtectedAreaReinforcementError("NMD codes and protected mask must have equal shape")
    terrestrial = analytical_role_masks(values)[TERRESTRIAL_LAND]
    return protected & terrestrial


def fraction_for_counts(numerator: int | float, denominator: int | float) -> float:
    """Return a raw fraction, with a missing value for a zero denominator."""

    return float(numerator / denominator) if denominator > 0 else float("nan")


def _read_study_geometry(path: Path) -> Any:
    study = gpd.read_file(path, layer=STUDY_AREA_LAYER)
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise ProtectedAreaReinforcementError("Study area must contain non-empty geometry")
    if study.crs is None or str(study.crs) != TARGET_CRS:
        raise ProtectedAreaReinforcementError(
            f"Study-area CRS is {study.crs}; expected {TARGET_CRS}"
        )
    geometry = study.geometry.union_all()
    if not geometry.is_valid or geometry.is_empty:
        raise ProtectedAreaReinforcementError("Study area geometry must be valid and non-empty")
    return geometry


def _read_footprint(path: Path) -> Any:
    footprint = gpd.read_file(path, layer=PROTECTED_FOOTPRINT_LAYER)
    required = {"geometry"}
    if footprint.empty or not required.issubset(footprint.columns):
        raise ProtectedAreaReinforcementError("Protected footprint layer is empty or malformed")
    if footprint.crs is None or str(footprint.crs) != TARGET_CRS:
        raise ProtectedAreaReinforcementError(f"Protected footprint CRS is {footprint.crs}")
    if len(footprint) != 1:
        raise ProtectedAreaReinforcementError(
            f"Protected footprint must remain the one-feature Step 16 layer; found {len(footprint)}"
        )
    geometry = footprint.geometry.iloc[0]
    if geometry.is_empty or not geometry.is_valid:
        raise ProtectedAreaReinforcementError("Protected footprint geometry is empty or invalid")
    return geometry


def _validate_raster(dataset: rasterio.io.DatasetReader) -> None:
    if dataset.count != 1 or str(dataset.crs) != TARGET_CRS:
        raise ProtectedAreaReinforcementError("NMD raster must be one-band EPSG:3006")
    if dataset.transform.b != 0 or dataset.transform.d != 0:
        raise ProtectedAreaReinforcementError("Rotated NMD rasters are not supported")
    if not np.allclose(dataset.res, (10.0, 10.0), rtol=0, atol=1e-9):
        raise ProtectedAreaReinforcementError(
            f"NMD raster resolution is {dataset.res}; expected 10 m"
        )
    pixel_area = abs(float(dataset.transform.a * dataset.transform.e))
    if not math.isclose(pixel_area, PIXEL_AREA_M2, rel_tol=0, abs_tol=1e-6):
        raise ProtectedAreaReinforcementError("NMD raster pixel area is not 100 m²")


def _coordinate_pairs(frame: pd.DataFrame | gpd.GeoDataFrame) -> set[tuple[int, int]]:
    return {
        (int(col), int(row))
        for col, row in zip(
            frame["grid_col"].to_numpy(dtype=int),
            frame["grid_row"].to_numpy(dtype=int),
            strict=True,
        )
    }


def reconcile_terrestrial_counts(
    derived_grid: pd.DataFrame, analysis_units: pd.DataFrame
) -> dict[str, Any]:
    """Compare derived terrestrial counts to every Step 6 terrestrial position."""

    required = {"grid_col", "grid_row", "terrestrial_pixels"}
    for label, frame in (("derived grid", derived_grid), ("analysis units", analysis_units)):
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ProtectedAreaReinforcementError(f"{label} is missing fields: {missing}")
    derived = derived_grid[["grid_col", "grid_row", "terrestrial_pixels"]].copy()
    expected = analysis_units[["grid_col", "grid_row", "terrestrial_pixels"]].copy()
    derived["grid_col"] = derived["grid_col"].astype(int)
    derived["grid_row"] = derived["grid_row"].astype(int)
    expected["grid_col"] = expected["grid_col"].astype(int)
    expected["grid_row"] = expected["grid_row"].astype(int)
    merged = expected.merge(
        derived,
        on=["grid_col", "grid_row"],
        how="outer",
        suffixes=("_analysis", "_derived"),
        indicator=True,
    )
    mismatch = merged["_merge"].ne("both")
    both = merged["_merge"].eq("both")
    mismatch |= both & merged["terrestrial_pixels_analysis"].ne(
        merged["terrestrial_pixels_derived"]
    )
    mismatch_rows = merged.loc[mismatch, ["grid_col", "grid_row", "_merge"]].copy()
    mismatch_rows["analysis_terrestrial_pixels"] = merged.loc[
        mismatch, "terrestrial_pixels_analysis"
    ].astype("Int64")
    mismatch_rows["derived_terrestrial_pixels"] = merged.loc[
        mismatch, "terrestrial_pixels_derived"
    ].astype("Int64")
    expected_total = int(expected["terrestrial_pixels"].sum())
    derived_total = int(derived["terrestrial_pixels"].sum())
    result = {
        "analysis_position_count": int(len(expected)),
        "derived_terrestrial_position_count": int(len(derived)),
        "analysis_total_terrestrial_pixels": expected_total,
        "derived_total_terrestrial_pixels": derived_total,
        "delta_pixels": derived_total - expected_total,
        "position_mismatch_count": int(len(mismatch_rows)),
        "position_mismatches": mismatch_rows.to_dict(orient="records"),
        "matches_exactly": False,
    }
    result["matches_exactly"] = bool(
        result["delta_pixels"] == 0 and result["position_mismatch_count"] == 0
    )
    return result


def aggregate_protected_terrestrial_grid(
    raster_path: Path = NMD_RASTER_PATH,
    footprint_path: Path = PROTECTED_FOOTPRINT_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate NMD terrestrial and protected-terrestrial pixels block by block."""

    validate_contract_definition()
    footprint = _read_footprint(footprint_path)
    study_geometry = _read_study_geometry(study_area_path)
    prepared_footprint = prep(footprint)
    with rasterio.open(raster_path) as dataset:
        _validate_raster(dataset)
        grid = generate_grid(dataset.bounds)
        if grid.empty:
            raise ProtectedAreaReinforcementError("NMD extent generated no grid positions")
        col_min = int(grid["grid_col"].min())
        col_max = int(grid["grid_col"].max())
        row_min = int(grid["grid_row"].min())
        row_max = int(grid["grid_row"].max())
        lookup = np.full((col_max - col_min + 1, row_max - row_min + 1), -1, dtype=np.int64)
        lookup[
            grid["grid_col"].to_numpy(dtype=np.int64) - col_min,
            grid["grid_row"].to_numpy(dtype=np.int64) - row_min,
        ] = np.arange(len(grid), dtype=np.int64)
        terrestrial_counts = np.zeros(len(grid), dtype=np.int64)
        protected_counts = np.zeros(len(grid), dtype=np.int64)
        terrestrial_total = 0
        protected_total = 0
        protected_blocks = 0
        processed_blocks = 0
        for _, window in dataset.block_windows(1):
            processed_blocks += 1
            data = dataset.read(1, window=window, masked=False)
            raster_mask = dataset.read_masks(1, window=window) > 0
            study_mask = rasterize(
                [(study_geometry, 1)],
                out_shape=data.shape,
                transform=window_transform(window, dataset.transform),
                fill=0,
                dtype="uint8",
                all_touched=False,
            ).astype(bool)
            valid = raster_mask & study_mask
            if not np.any(valid):
                continue
            terrestrial = analytical_role_masks(data)[TERRESTRIAL_LAND]
            terrestrial_valid = valid & terrestrial
            if not np.any(terrestrial_valid):
                continue
            flat = np.flatnonzero(terrestrial_valid)
            rows, cols = np.divmod(flat, data.shape[1])
            x = dataset.transform.c + (window.col_off + cols + 0.5) * dataset.transform.a
            y = dataset.transform.f + (window.row_off + rows + 0.5) * dataset.transform.e
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
                raise ProtectedAreaReinforcementError(
                    "A terrestrial NMD pixel center was not assigned to the generated hex grid"
                )
            terrestrial_counts += np.bincount(cell_indices, minlength=len(grid))
            terrestrial_total += int(len(cell_indices))
            block_bounds = rasterio.windows.bounds(window, dataset.transform)
            if not prepared_footprint.intersects(box(*block_bounds)):
                continue
            protected_blocks += 1
            protected_mask = rasterize(
                [(footprint, 1)],
                out_shape=data.shape,
                transform=window_transform(window, dataset.transform),
                fill=0,
                dtype="uint8",
                all_touched=False,
            ).astype(bool)
            protected_terrestrial = terrestrial_valid & protected_mask
            if np.any(protected_terrestrial):
                protected_flat = np.flatnonzero(protected_terrestrial)
                protected_rows, protected_cols = np.divmod(protected_flat, data.shape[1])
                protected_x = (
                    dataset.transform.c
                    + (window.col_off + protected_cols + 0.5) * dataset.transform.a
                )
                protected_y = (
                    dataset.transform.f
                    + (window.row_off + protected_rows + 0.5) * dataset.transform.e
                )
                protected_col, protected_row = pixel_centers_to_grid_indices(
                    protected_x, protected_y
                )
                protected_indices = lookup[
                    protected_col - col_min,
                    protected_row - row_min,
                ]
                protected_counts += np.bincount(protected_indices, minlength=len(grid))
                protected_total += int(len(protected_indices))
        retained = terrestrial_counts > 0
        derived = pd.DataFrame(
            {
                "grid_col": grid.loc[retained, "grid_col"].to_numpy(dtype=np.int64),
                "grid_row": grid.loc[retained, "grid_row"].to_numpy(dtype=np.int64),
                "terrestrial_pixels": terrestrial_counts[retained],
                "protected_terrestrial_pixels": protected_counts[retained],
            }
        )
        derived["protected_terrestrial_fraction"] = np.divide(
            derived["protected_terrestrial_pixels"].to_numpy(dtype=float),
            derived["terrestrial_pixels"].to_numpy(dtype=float),
        )
    if derived.empty:
        raise ProtectedAreaReinforcementError("No terrestrial grid positions were derived")
    return derived, {
        "initial_grid_position_count": int(len(grid)),
        "terrestrial_grid_position_count": int(len(derived)),
        "zero_terrestrial_positions_removed": int((~retained).sum()),
        "processed_raster_block_count": processed_blocks,
        "blocks_intersecting_protected_footprint": protected_blocks,
        "total_terrestrial_pixels": terrestrial_total,
        "total_protected_terrestrial_pixels": protected_total,
        "pixel_area_m2": PIXEL_AREA_M2,
    }


def _aggregate_neighborhood(
    coordinate: tuple[int, int],
    offsets: Iterable[tuple[int, int]],
    lookup: dict[tuple[int, int], tuple[int, int]],
) -> tuple[int, int]:
    terrestrial = 0
    protected = 0
    for delta_col, delta_row in offsets:
        values = lookup.get((coordinate[0] + delta_col, coordinate[1] + delta_row))
        if values is None:
            continue
        terrestrial += values[0]
        protected += values[1]
    return protected, terrestrial


def propagate_nearest_protected_steps(
    protected_coordinates: Iterable[tuple[int, int]],
    candidate_coordinates: Iterable[tuple[int, int]],
    terrestrial_coordinates: Iterable[tuple[int, int]] | None = None,
) -> tuple[dict[tuple[int, int], int], dict[str, Any]]:
    """Propagate integer distance from protected sources on the unrestricted axial lattice."""

    sources = set(protected_coordinates)
    targets = set(candidate_coordinates)
    if not sources:
        raise ProtectedAreaReinforcementError(
            "At least one protected terrestrial grid source is required"
        )
    distances: dict[tuple[int, int], int] = {coordinate: 0 for coordinate in sources}
    queue: deque[tuple[int, int]] = deque(sorted(sources))
    remaining = set(targets)
    while queue and remaining:
        coordinate = queue.popleft()
        remaining.discard(coordinate)
        distance = distances[coordinate]
        for delta_col, delta_row in RING_1_OFFSETS:
            neighbor = (coordinate[0] + delta_col, coordinate[1] + delta_row)
            if neighbor not in distances:
                distances[neighbor] = distance + 1
                queue.append(neighbor)
    if remaining:
        raise ProtectedAreaReinforcementError(
            f"Distance propagation did not reach {len(remaining)} candidate coordinates"
        )
    target_distances = {coordinate: distances[coordinate] for coordinate in targets}
    terrestrial_set = set(terrestrial_coordinates) if terrestrial_coordinates is not None else None
    nonterrestrial_visited = (
        len(set(distances) - terrestrial_set) if terrestrial_set is not None else None
    )
    return target_distances, {
        "source_position_count": len(sources),
        "target_position_count": len(targets),
        "visited_coordinate_count_until_all_targets_reached": len(distances),
        "maximum_visited_distance": max(distances.values(), default=0),
        "all_targets_reached": not remaining,
        "traversal_domain": (
            "unrestricted deterministic axial lattice; traversal is stopped only after all "
            "candidate targets are reached and may pass through omitted water-only coordinates"
        ),
        "visited_coordinates_without_terrestrial_grid_rows": nonterrestrial_visited,
    }


def calculate_indicators(
    candidate_units: gpd.GeoDataFrame,
    terrestrial_grid: pd.DataFrame,
    nearest_steps: dict[tuple[int, int], int],
    edge_flags: Iterable[bool] | None = None,
) -> pd.DataFrame:
    """Calculate raw focal, adjacent, local, and nearest-step diagnostics."""

    required_grid = set(GRID_COLUMNS[:4])
    missing = sorted(required_grid.difference(terrestrial_grid.columns))
    if missing:
        raise ProtectedAreaReinforcementError(f"Terrestrial grid is missing fields: {missing}")
    required_candidates = {"hex_id", "grid_col", "grid_row", "terrestrial_pixels"}
    missing = sorted(required_candidates.difference(candidate_units.columns))
    if missing:
        raise ProtectedAreaReinforcementError(f"Candidate units are missing fields: {missing}")
    if candidate_units["hex_id"].duplicated().any():
        raise ProtectedAreaReinforcementError("Candidate hex_id values must be unique")
    if edge_flags is not None:
        edge_values = np.asarray(list(edge_flags), dtype=bool)
        if len(edge_values) != len(candidate_units):
            raise ProtectedAreaReinforcementError("Boundary edge flags must match candidates")
    else:
        edge_values = np.zeros(len(candidate_units), dtype=bool)
    lookup = {
        (int(row.grid_col), int(row.grid_row)): (
            int(row.terrestrial_pixels),
            int(row.protected_terrestrial_pixels),
        )
        for row in terrestrial_grid.itertuples(index=False)
    }
    records: list[dict[str, Any]] = []
    for position, row in enumerate(candidate_units.itertuples(index=False)):
        coordinate = (int(row.grid_col), int(row.grid_row))
        focal = lookup.get(coordinate)
        if focal is None or focal[0] <= 0:
            raise ProtectedAreaReinforcementError(
                f"Candidate {row.hex_id} has no positive terrestrial grid denominator"
            )
        adjacent_protected, adjacent_terrestrial = _aggregate_neighborhood(
            coordinate, RING_1_OFFSETS, lookup
        )
        local_protected, local_terrestrial = _aggregate_neighborhood(
            coordinate, LOCAL_OFFSETS, lookup
        )
        if coordinate not in nearest_steps:
            raise ProtectedAreaReinforcementError(f"No nearest protected distance for {row.hex_id}")
        steps = int(nearest_steps[coordinate])
        records.append(
            {
                "hex_id": str(row.hex_id),
                "protected_focal_fraction": fraction_for_counts(focal[1], focal[0]),
                "protected_adjacent_fraction": fraction_for_counts(
                    adjacent_protected, adjacent_terrestrial
                ),
                "protected_local_fraction": fraction_for_counts(local_protected, local_terrestrial),
                "nearest_protected_hex_steps": steps,
                "nearest_protected_nominal_m": float(steps * HEX_STEP_NOMINAL_M),
                "boundary_edge_flag": bool(edge_values[position]),
                "focal_terrestrial_pixels": focal[0],
                "focal_protected_terrestrial_pixels": focal[1],
                "adjacent_terrestrial_pixels": adjacent_terrestrial,
                "adjacent_protected_pixels": adjacent_protected,
                "local_terrestrial_pixels": local_terrestrial,
                "local_protected_pixels": local_protected,
            }
        )
    result = pd.DataFrame.from_records(records)
    if result.empty:
        raise ProtectedAreaReinforcementError("Candidate population is empty")
    for field in (
        "protected_focal_fraction",
        "protected_adjacent_fraction",
        "protected_local_fraction",
    ):
        values = result[field].to_numpy(dtype=float)
        valid = np.isfinite(values)
        if np.any((values[valid] < 0) | (values[valid] > 1)):
            raise ProtectedAreaReinforcementError(f"{field} must lie in [0, 1]")
    return result


def _quantile_summary(values: pd.Series | np.ndarray) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
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
    quantiles = np.quantile(array, [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])
    return {
        "n": int(len(array)),
        "min": float(array.min()),
        "p10": float(quantiles[0]),
        "p25": float(quantiles[1]),
        "median": float(quantiles[2]),
        "mean": float(array.mean()),
        "p75": float(quantiles[3]),
        "p90": float(quantiles[4]),
        "p95": float(quantiles[5]),
        "p99": float(quantiles[6]),
        "max": float(array.max()),
    }


def _fraction_bins(values: pd.Series) -> dict[str, int]:
    valid = values.dropna()
    return {
        "0": int((valid == 0).sum()),
        ">0–1%": int(((valid > 0) & (valid <= 0.01)).sum()),
        ">1–5%": int(((valid > 0.01) & (valid <= 0.05)).sum()),
        ">5–10%": int(((valid > 0.05) & (valid <= 0.10)).sum()),
        ">10–25%": int(((valid > 0.10) & (valid <= 0.25)).sum()),
        ">25–50%": int(((valid > 0.25) & (valid <= 0.50)).sum()),
        ">50–75%": int(((valid > 0.50) & (valid <= 0.75)).sum()),
        ">75%": int((valid > 0.75).sum()),
        "missing": int(values.isna().sum()),
    }


def _distribution_with_bins(values: pd.Series) -> dict[str, Any]:
    return {**_quantile_summary(values), "bins": _fraction_bins(values)}


def _correlation(first: pd.Series, second: pd.Series) -> dict[str, float | int | None]:
    pair = pd.concat([first, second], axis=1).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return {"n": int(len(pair)), "pearson": None, "spearman": None}
    first_rank = pair.iloc[:, 0].rank(method="average")
    second_rank = pair.iloc[:, 1].rank(method="average")
    return {
        "n": int(len(pair)),
        "pearson": float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="pearson")),
        "spearman": float(first_rank.corr(second_rank, method="pearson")),
    }


def _load_csv(path: Path, required: Iterable[str]) -> pd.DataFrame:
    if not path.exists():
        raise ProtectedAreaReinforcementError(f"Missing required finalized artifact: {path}")
    frame = pd.read_csv(path)
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ProtectedAreaReinforcementError(f"{path} is missing fields: {missing}")
    if frame["hex_id"].duplicated().any():
        raise ProtectedAreaReinforcementError(f"{path} contains duplicate hex_id values")
    return frame


def _join_by_id(
    base: pd.DataFrame, other: pd.DataFrame, fields: Iterable[str], label: str
) -> pd.DataFrame:
    selected = other[["hex_id", *fields]].copy()
    joined = base.merge(selected, on="hex_id", how="left", validate="one_to_one")
    missing = int(joined[list(fields)].isna().all(axis=1).sum())
    if missing:
        raise ProtectedAreaReinforcementError(f"{label} is missing {missing} candidate IDs")
    return joined


def _safe_examples(
    frame: pd.DataFrame, mask: pd.Series, fields: list[str], limit: int = 5
) -> list[dict[str, Any]]:
    subset = frame.loc[mask].sort_values(
        fields[1] if len(fields) > 1 else fields[0], ascending=False
    )
    return subset[fields].head(limit).to_dict(orient="records")


def _coordinate_distribution(candidates: gpd.GeoDataFrame) -> dict[str, Any]:
    centroids = candidates.geometry.centroid
    x = centroids.x.to_numpy(dtype=float)
    y = centroids.y.to_numpy(dtype=float)
    x_edges = np.linspace(float(x.min()), float(x.max()), 4)
    y_edges = np.linspace(float(y.min()), float(y.max()), 4)
    x_bins = np.clip(np.digitize(x, x_edges[1:-1]), 0, 2)
    y_bins = np.clip(np.digitize(y, y_edges[1:-1]), 0, 2)
    return {
        "bounds_epsg_3006": [float(value) for value in candidates.total_bounds],
        "centroid_thirds": {
            f"x_third_{i + 1},y_third_{j + 1}": int(((x_bins == i) & (y_bins == j)).sum())
            for i in range(3)
            for j in range(3)
        },
    }


def _grid_coordinate_distribution(coordinates: Iterable[tuple[int, int]]) -> dict[str, Any]:
    pairs = sorted(set(coordinates))
    if not pairs:
        return {"count": 0, "bounds_epsg_3006": None, "centroid_thirds": {}}
    points = np.asarray([hex_center(col, row) for col, row in pairs], dtype=float)
    x = points[:, 0]
    y = points[:, 1]
    x_edges = np.linspace(float(x.min()), float(x.max()), 4)
    y_edges = np.linspace(float(y.min()), float(y.max()), 4)
    x_bins = np.clip(np.digitize(x, x_edges[1:-1]), 0, 2)
    y_bins = np.clip(np.digitize(y, y_edges[1:-1]), 0, 2)
    return {
        "count": len(pairs),
        "bounds_epsg_3006": [
            float(x.min()),
            float(y.min()),
            float(x.max()),
            float(y.max()),
        ],
        "centroid_thirds": {
            f"x_third_{i + 1},y_third_{j + 1}": int(((x_bins == i) & (y_bins == j)).sum())
            for i in range(3)
            for j in range(3)
        },
    }


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
    frame.to_csv(temporary, index=False, float_format=f"%.{CSV_FLOAT_PRECISION}g")
    os.replace(temporary, path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_text(
        json.dumps(_safe_json(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _diagnostic_distributions(indicators: pd.DataFrame) -> dict[str, Any]:
    return {
        field: _distribution_with_bins(indicators[field])
        for field in (
            "protected_focal_fraction",
            "protected_adjacent_fraction",
            "protected_local_fraction",
        )
    }


def _presence_counts(indicators: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, field, count_field in (
        ("focal", "protected_focal_fraction", "focal_protected_terrestrial_pixels"),
        ("adjacent", "protected_adjacent_fraction", "adjacent_protected_pixels"),
        ("local", "protected_local_fraction", "local_protected_pixels"),
    ):
        present = indicators[count_field] > 0
        result[label] = {
            "any_protected_terrestrial_count": int(present.sum()),
            "any_protected_terrestrial_percent": float(100 * present.mean()),
            "fraction_equal_1_count": int((indicators[field] == 1).sum()),
        }
    focal = indicators["protected_focal_fraction"]
    result["focal_threshold_counts"] = {
        ">=25%": int((focal >= 0.25).sum()),
        ">=50%": int((focal >= 0.50).sum()),
        ">=75%": int((focal >= 0.75).sum()),
    }
    return result


def _distance_distribution(indicators: pd.DataFrame) -> dict[str, Any]:
    values = indicators["nearest_protected_hex_steps"].to_numpy(dtype=int)
    bands = {
        "0 steps": int((values == 0).sum()),
        "1": int((values == 1).sum()),
        "2": int((values == 2).sum()),
        "3": int((values == 3).sum()),
        "4": int((values == 4).sum()),
        "5": int((values == 5).sum()),
        "6–10": int(((values >= 6) & (values <= 10)).sum()),
        ">10": int((values > 10).sum()),
    }
    quantiles = _quantile_summary(values)
    return {
        "bands": bands,
        "steps": quantiles,
        "nominal_m_quantiles": {
            key: (value * HEX_STEP_NOMINAL_M if value is not None else None)
            for key, value in quantiles.items()
            if key != "n"
        },
        "nominal_m_note": "steps multiplied by 500 m; scale label, not exact Euclidean edge distance",
    }


def _raw_vector_diagnostics(
    candidates: gpd.GeoDataFrame,
    indicators: pd.DataFrame,
    footprint: Any,
    step16_provenance_path: Path,
) -> dict[str, Any]:
    distances = candidates.geometry.distance(footprint).to_numpy(dtype=float)
    frame = indicators.copy()
    frame["raw_vector_distance_m"] = distances
    frame["sea_containing"] = candidates["sea_pixels"].to_numpy(dtype=float) > 0
    frame["inland_water_containing"] = candidates["inland_water_pixels"].to_numpy(dtype=float) > 0
    zero = frame["raw_vector_distance_m"] <= 1e-9
    focal_zero = frame["protected_focal_fraction"] == 0
    edge = frame["boundary_edge_flag"].astype(bool)
    result: dict[str, Any] = {
        "raw_vector_geometry_distance_distribution_m": _quantile_summary(distances),
        "raw_vector_distance_zero_count": int(zero.sum()),
        "raw_distance_zero_focal_positive_count": int((zero & ~focal_zero).sum()),
        "raw_distance_zero_focal_zero_count": int((zero & focal_zero).sum()),
        "raw_distance_zero_focal_zero_sea_containing_count": int(
            (zero & focal_zero & frame["sea_containing"]).sum()
        ),
        "raw_distance_zero_focal_zero_inland_water_containing_count": int(
            (zero & focal_zero & frame["inland_water_containing"]).sum()
        ),
        "raw_distance_zero_focal_zero_boundary_count": int((zero & focal_zero & edge).sum()),
        "raw_distance_zero_focal_zero_coordinate_distribution": _coordinate_distribution(
            candidates.loc[zero & focal_zero]
        )
        if (zero & focal_zero).any()
        else None,
        "raw_distance_le_500_and_hex_steps_gt_1_count": int(
            (
                (frame["raw_vector_distance_m"] <= 500) & (frame["nearest_protected_hex_steps"] > 1)
            ).sum()
        ),
        "raw_distance_gt_500_and_hex_steps_le_1_count": int(
            (
                (frame["raw_vector_distance_m"] > 500) & (frame["nearest_protected_hex_steps"] <= 1)
            ).sum()
        ),
        "raw_distance_le_500_hex_steps_gt_1_examples": frame.loc[
            (frame["raw_vector_distance_m"] <= 500) & (frame["nearest_protected_hex_steps"] > 1),
            [
                "hex_id",
                "raw_vector_distance_m",
                "nearest_protected_hex_steps",
                "protected_focal_fraction",
            ],
        ]
        .head(10)
        .to_dict(orient="records"),
        "raw_distance_gt_500_hex_steps_le_1_examples": frame.loc[
            (frame["raw_vector_distance_m"] > 500) & (frame["nearest_protected_hex_steps"] <= 1),
            [
                "hex_id",
                "raw_vector_distance_m",
                "nearest_protected_hex_steps",
                "protected_focal_fraction",
            ],
        ]
        .head(10)
        .to_dict(orient="records"),
    }
    if step16_provenance_path.exists():
        try:
            previous = json.loads(step16_provenance_path.read_text(encoding="utf-8"))
            result["step16_reported_raw_intersect_count"] = previous.get(
                "candidate_relationship_diagnostics", {}
            ).get("candidate_intersect_count")
        except (OSError, json.JSONDecodeError):
            result["step16_reported_raw_intersect_count"] = None
    return result


def _scale_contrast_examples(joined: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    fields = [
        "hex_id",
        "protected_focal_fraction",
        "protected_adjacent_fraction",
        "protected_local_fraction",
        "nearest_protected_hex_steps",
        "habitat_context_score",
        "ecological_network_score",
        "riparian_opportunity_score",
        "candidate_fraction_of_terrestrial",
        "boundary_edge_flag",
    ]
    examples = {
        "high_focal_low_adjacent_local": (
            (joined["protected_focal_fraction"] >= 0.50)
            & (joined["protected_adjacent_fraction"] < 0.10)
            & (joined["protected_local_fraction"] < 0.10)
        ),
        "zero_focal_high_adjacent": (joined["protected_focal_fraction"] == 0)
        & (joined["protected_adjacent_fraction"] >= 0.50),
        "zero_focal_adjacent_high_local": (joined["protected_focal_fraction"] == 0)
        & (joined["protected_adjacent_fraction"] == 0)
        & (joined["protected_local_fraction"] >= 0.50),
    }
    sort_fields = {
        "high_focal_low_adjacent_local": ["protected_focal_fraction"],
        "zero_focal_high_adjacent": ["protected_adjacent_fraction"],
        "zero_focal_adjacent_high_local": ["protected_local_fraction"],
    }
    return {
        label: joined.loc[mask]
        .sort_values(sort_fields[label], ascending=False)[fields]
        .head(5)
        .to_dict(orient="records")
        for label, mask in examples.items()
    }


def build_protected_area_reinforcement(
    raster_path: Path = NMD_RASTER_PATH,
    footprint_path: Path = PROTECTED_FOOTPRINT_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
    analysis_units_path: Path = ANALYSIS_UNITS_PATH,
    candidate_units_path: Path = CANDIDATE_UNITS_PATH,
    output_path: Path = OUTPUT_PATH,
    grid_output_path: Path = GRID_OUTPUT_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the raw Step 17 candidate table, grid support, and audit manifest."""

    start = time.perf_counter()
    footprint = _read_footprint(footprint_path)
    candidates = gpd.read_file(candidate_units_path, layer=CANDIDATE_UNITS_LAYER)
    analysis_units = gpd.read_file(analysis_units_path, layer=ANALYSIS_UNITS_LAYER)
    if candidates.empty or candidates["hex_id"].duplicated().any():
        raise ProtectedAreaReinforcementError("Candidate layer is empty or has duplicate IDs")
    if len(candidates) != 26_395:
        raise ProtectedAreaReinforcementError(
            f"Unexpected candidate population {len(candidates)}; expected approved 26,395"
        )
    terrestrial_grid, grid_audit = aggregate_protected_terrestrial_grid(
        raster_path, footprint_path, study_area_path
    )
    reconciliation = reconcile_terrestrial_counts(terrestrial_grid, analysis_units)
    if not reconciliation["matches_exactly"]:
        raise ProtectedAreaReinforcementError(
            f"Step 6 terrestrial reconciliation failed: {reconciliation['position_mismatch_count']} position mismatches, "
            f"delta={reconciliation['delta_pixels']}"
        )
    sources = {
        (int(row.grid_col), int(row.grid_row))
        for row in terrestrial_grid.itertuples(index=False)
        if int(row.protected_terrestrial_pixels) > 0
    }
    candidate_coordinates = {
        (int(row.grid_col), int(row.grid_row)) for row in candidates.itertuples(index=False)
    }
    terrestrial_coordinates = {
        (int(row.grid_col), int(row.grid_row)) for row in terrestrial_grid.itertuples(index=False)
    }
    nearest_steps, distance_audit = propagate_nearest_protected_steps(
        sources, candidate_coordinates, terrestrial_coordinates
    )
    study_geometry = _read_study_geometry(study_area_path)
    edge_flags = calculate_boundary_edge_flags(
        candidates, study_geometry, threshold_m=BOUNDARY_EDGE_DISTANCE_M
    )
    indicators_internal = calculate_indicators(
        candidates, terrestrial_grid, nearest_steps, edge_flags=edge_flags
    )
    indicators = indicators_internal[list(OUTPUT_COLUMNS)].copy()
    if len(indicators) != len(candidates) or set(indicators["hex_id"]) != set(candidates["hex_id"]):
        raise ProtectedAreaReinforcementError("Candidate/output ID reconciliation failed")

    habitat_raw = _load_csv(HABITAT_RAW_PATH, ["hex_id", "habitat_context_local_fraction"])
    habitat_score = _load_csv(HABITAT_SCORE_PATH, ["hex_id", "habitat_context_score"])
    network_score = _load_csv(NETWORK_SCORE_PATH, ["hex_id", "ecological_network_score"])
    riparian_raw = _load_csv(RIPARIAN_RAW_PATH, ["hex_id", "riparian_focal_fraction"])
    riparian_score = _load_csv(RIPARIAN_SCORE_PATH, ["hex_id", "riparian_opportunity_score"])
    joined = _join_by_id(
        indicators_internal, habitat_score, ["habitat_context_score"], "Habitat score"
    )
    joined = _join_by_id(joined, network_score, ["ecological_network_score"], "Network score")
    joined = _join_by_id(joined, riparian_score, ["riparian_opportunity_score"], "Riparian score")
    joined = _join_by_id(joined, habitat_raw, ["habitat_context_local_fraction"], "Habitat raw")
    joined = _join_by_id(joined, riparian_raw, ["riparian_focal_fraction"], "Riparian raw")
    candidate_fields = [
        "candidate_fraction_of_terrestrial",
        "candidate_area_m2",
        "artificial_constraint_pixels",
        "terrestrial_pixels",
        "habitat_context_fraction_of_terrestrial",
    ]
    joined = _join_by_id(
        joined,
        candidates.drop(columns="geometry"),
        candidate_fields,
        "Candidate units",
    )
    joined["artificial_constraint_fraction"] = (
        joined["artificial_constraint_pixels"] / joined["terrestrial_pixels"]
    )
    joined["raw_vector_distance_m"] = candidates.geometry.distance(footprint).to_numpy(dtype=float)

    component_fields = {
        "Habitat Context score": "habitat_context_score",
        "Ecological Network Context score": "ecological_network_score",
        "Riparian Opportunity score": "riparian_opportunity_score",
    }
    serious_fields = (
        "protected_focal_fraction",
        "protected_adjacent_fraction",
        "protected_local_fraction",
        "nearest_protected_hex_steps",
    )
    distinctness = {
        indicator: {
            component: _correlation(joined[indicator], joined[field])
            for component, field in component_fields.items()
        }
        for indicator in serious_fields
    }
    habitat_relationships = {
        indicator: {
            "habitat_context_local_fraction": _correlation(
                joined[indicator], joined["habitat_context_local_fraction"]
            ),
            "focal_habitat_context_fraction_of_terrestrial": _correlation(
                joined[indicator], joined["habitat_context_fraction_of_terrestrial"]
            ),
        }
        for indicator in (
            "protected_focal_fraction",
            "protected_adjacent_fraction",
            "protected_local_fraction",
        )
    }
    composition_relationships = {
        indicator: {
            field: _correlation(joined[indicator], joined[field])
            for field in (
                "candidate_fraction_of_terrestrial",
                "candidate_area_m2",
                "artificial_constraint_fraction",
                "riparian_focal_fraction",
            )
        }
        for indicator in serious_fields
    }
    focal_positive = joined["protected_focal_fraction"] > 0
    overlap_diagnostics = {
        "count": int(focal_positive.sum()),
        "protected_focal_fraction": _quantile_summary(
            joined.loc[focal_positive, "protected_focal_fraction"]
        ),
        "candidate_fraction_of_terrestrial_median": float(
            joined.loc[focal_positive, "candidate_fraction_of_terrestrial"].median()
        )
        if focal_positive.any()
        else None,
        "habitat_context_score_median": float(
            joined.loc[focal_positive, "habitat_context_score"].median()
        )
        if focal_positive.any()
        else None,
        "ecological_network_score_median": float(
            joined.loc[focal_positive, "ecological_network_score"].median()
        )
        if focal_positive.any()
        else None,
        "riparian_opportunity_score_median": float(
            joined.loc[focal_positive, "riparian_opportunity_score"].median()
        )
        if focal_positive.any()
        else None,
        "focal_fraction_threshold_counts": {
            ">=25%": int((joined.loc[focal_positive, "protected_focal_fraction"] >= 0.25).sum()),
            ">=50%": int((joined.loc[focal_positive, "protected_focal_fraction"] >= 0.50).sum()),
            ">=75%": int((joined.loc[focal_positive, "protected_focal_fraction"] >= 0.75).sum()),
        },
    }
    zero_focal = joined["protected_focal_fraction"] == 0
    zero_near_counts = {
        "1": int((zero_focal & (joined["nearest_protected_hex_steps"] == 1)).sum()),
        "2": int((zero_focal & (joined["nearest_protected_hex_steps"] == 2)).sum()),
        "3": int((zero_focal & (joined["nearest_protected_hex_steps"] == 3)).sum()),
        "<=5": int((zero_focal & (joined["nearest_protected_hex_steps"] <= 5)).sum()),
    }
    raw_vector = _raw_vector_diagnostics(
        candidates, indicators_internal, footprint, STEP16_PROVENANCE_PATH
    )
    raw_overlap_count = int(candidates.geometry.intersects(footprint).sum())
    boundary = indicators_internal["boundary_edge_flag"].astype(bool)
    boundary_diagnostics = {
        "threshold_m": BOUNDARY_EDGE_DISTANCE_M,
        "edge_count": int(boundary.sum()),
        "non_edge_count": int((~boundary).sum()),
        "edge_fractions": {
            field: _distribution_with_bins(indicators_internal.loc[boundary, field])
            for field in (
                "protected_focal_fraction",
                "protected_adjacent_fraction",
                "protected_local_fraction",
            )
        },
        "non_edge_fractions": {
            field: _distribution_with_bins(indicators_internal.loc[~boundary, field])
            for field in (
                "protected_focal_fraction",
                "protected_adjacent_fraction",
                "protected_local_fraction",
            )
        },
        "edge_step_distance": _quantile_summary(
            indicators_internal.loc[boundary, "nearest_protected_hex_steps"]
        ),
        "non_edge_step_distance": _quantile_summary(
            indicators_internal.loc[~boundary, "nearest_protected_hex_steps"]
        ),
        "edge_raw_vector_distance_m": _quantile_summary(
            joined.loc[boundary, "raw_vector_distance_m"]
        ),
        "non_edge_raw_vector_distance_m": _quantile_summary(
            joined.loc[~boundary, "raw_vector_distance_m"]
        ),
    }
    spatial_sanity = {
        "protected_support_coordinate_distribution": _grid_coordinate_distribution(sources),
        "candidate_coordinate_distribution": _coordinate_distribution(candidates),
        "candidate_output_covers_full_candidate_extent": True,
        "distance_traversal_not_restricted_to_terrestrial_rows": True,
        "known_overlap_samples": joined.loc[
            joined["protected_focal_fraction"] > 0,
            ["hex_id", "protected_focal_fraction", "nearest_protected_hex_steps"],
        ]
        .sort_values("protected_focal_fraction", ascending=False)
        .head(5)
        .to_dict(orient="records"),
    }
    provenance: dict[str, Any] = {
        "component_working_name": "Protected-Area Reinforcement",
        "status": "RAW INDICATORS UNDER AUDIT; NO FINAL SCALE, DISTANCE, OR SCORE SELECTED",
        "source": {
            "protected_footprint_path": str(footprint_path),
            "protected_footprint_layer": PROTECTED_FOOTPRINT_LAYER,
            "nmd_raster_path": str(raster_path),
            "nmd_version": NMD_VERSION,
            "nmd_semantic_role_source": "src/restoration_prioritizer/nmd_semantics.py",
            "analysis_units_path": str(analysis_units_path),
            "candidate_units_path": str(candidate_units_path),
        },
        "protected_terrestrial_definition": {
            "definition": "NMD pixel center inside unified approved protected_footprint AND NMD code in terrestrial_land role",
            "terrestrial_role": TERRESTRIAL_LAND,
            "excluded": ["code 0/no-data", "inland water code 61", "sea code 62"],
            "artificial_land": "included as terrestrial_land",
            "habitat_or_candidate_restriction": False,
            "source_geometry_modified": False,
        },
        "rasterization_and_grid": {
            "pixel_center_rule": True,
            "all_touched": False,
            "pixel_assignment": "Reuse analysis_units.pixel_centers_to_grid_indices for deterministic Step 6 pointy-top axial assignment",
            "grid_convention": "500 m flat-to-flat, fixed origin (0,0), h_<grid_col>_<grid_row>",
            "aggregation": "raw pixel counts first, then fraction; no averaging per-cell fractions",
            "water_only_positions": "not durable in terrestrial grid; omitted positions contribute no denominator",
        },
        "grid_audit": {
            **grid_audit,
            "protected_terrestrial_area_km2": grid_audit["total_protected_terrestrial_pixels"]
            * PIXEL_AREA_M2
            / 1_000_000.0,
            "protected_share_of_nmd_terrestrial_skane": grid_audit[
                "total_protected_terrestrial_pixels"
            ]
            / grid_audit["total_terrestrial_pixels"],
            "terrestrial_reconciliation": reconciliation,
        },
        "distance_propagation_audit": distance_audit,
        "candidate_population_validation": {
            "input_count": int(len(candidates)),
            "output_count": int(len(indicators)),
            "duplicate_output_hex_id_count": int(indicators["hex_id"].duplicated().sum()),
            "ids_reconcile_exactly": True,
        },
        "indicator_definitions": {
            "protected_focal_fraction": "focal protected-terrestrial pixels / focal terrestrial pixels",
            "protected_adjacent_fraction": "sum protected-terrestrial pixels across six ring-1 positions / sum terrestrial pixels across those positions; focal excluded",
            "protected_local_fraction": "sum protected-terrestrial pixels across all 18 positions at hex distance 1 or 2 / sum terrestrial pixels across those positions; focal excluded",
            "missing_or_water_only_positions": "no numerator and no terrestrial denominator",
            "nearest_protected_hex_steps": "minimum axial hex distance to any terrestrial grid position with protected_terrestrial_pixels > 0",
            "axial_distance": "max(abs(dc), abs(dr), abs(dc + dr))",
            "nearest_protected_nominal_m": "nearest_protected_hex_steps * 500; interpretive scale label only, not exact Euclidean distance",
        },
        "distributions": _diagnostic_distributions(indicators_internal),
        "presence_counts": _presence_counts(indicators_internal),
        "scale_correlations": {
            "focal_vs_adjacent": _correlation(
                indicators_internal["protected_focal_fraction"],
                indicators_internal["protected_adjacent_fraction"],
            ),
            "focal_vs_local": _correlation(
                indicators_internal["protected_focal_fraction"],
                indicators_internal["protected_local_fraction"],
            ),
            "adjacent_vs_local": _correlation(
                indicators_internal["protected_adjacent_fraction"],
                indicators_internal["protected_local_fraction"],
            ),
        },
        "nearest_protected_grid_distance_distribution": _distance_distribution(indicators_internal),
        "raw_vector_distance_vs_terrestrial_support": raw_vector,
        "focal_overlap_reconciliation": {
            "step16_raw_geometry_intersect_count_expected": 1_627,
            "step16_raw_geometry_intersect_count_from_provenance": raw_vector.get(
                "step16_reported_raw_intersect_count"
            ),
            "observed_raw_geometry_intersect_count": raw_overlap_count,
            "protected_focal_fraction_positive_count": int(focal_positive.sum()),
            "difference_raw_intersect_minus_focal_support": raw_overlap_count
            - int(focal_positive.sum()),
            "explanation": [
                "Raw geometry intersection can be marine or inland-water overlap without protected terrestrial NMD pixel centers.",
                "Pixel-center rasterization can exclude sub-pixel boundary or sliver overlap.",
                "The two measures intentionally answer different questions and need not match.",
            ],
        },
        "distinctness_from_finalized_components": distinctness,
        "habitat_amount_relationships": habitat_relationships,
        "candidate_composition_relationships": composition_relationships,
        "protected_overlap_candidate_character": overlap_diagnostics,
        "zero_focal_near_protection_counts": zero_near_counts,
        "scale_contrast_examples": _scale_contrast_examples(joined),
        "boundary_diagnostics": boundary_diagnostics,
        "spatial_sanity": spatial_sanity,
        "marine_distortion_note": "Raw Step 16 geometry distance includes marine/inland-water protected geometry; this is diagnostic only and is not a selected final metric.",
        "caveats": [
            "Formal protection does not equal habitat quality.",
            "Candidate overlap with protected land is not automatically positive or negative.",
            "Marine and inland-water protected portions are excluded from terrestrial analytical support.",
            "The source legal geometry itself remains unaltered.",
            "Nearest hex-step distance is geometric grid proximity, not ecological connectivity.",
            "NMD terrestrial classification defines the analytical land mask.",
            "Cross-county terrestrial protected context outside the NMD Skåne raster is not represented in this derived terrestrial grid.",
            "No final focal, adjacent, local, distance, normalization, score, or weighting method is selected in Step 17.",
        ],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - start,
        "dependencies": "No new dependencies; existing NumPy, pandas, Rasterio, GeoPandas, Shapely, and project modules only.",
        "environmental_data_downloaded": False,
        "output": {
            "path": str(output_path),
            "schema": list(OUTPUT_COLUMNS),
            "row_count": int(len(indicators)),
        },
        "derived_grid_output": {
            "path": str(grid_output_path),
            "schema": list(GRID_COLUMNS),
            "row_count": int(len(terrestrial_grid)),
        },
    }
    _write_csv(indicators, output_path)
    _write_csv(terrestrial_grid[list(GRID_COLUMNS)], grid_output_path)
    provenance["output"]["size_bytes"] = int(output_path.stat().st_size)
    provenance["derived_grid_output"]["size_bytes"] = int(grid_output_path.stat().st_size)
    _write_json(provenance_path, provenance)
    return indicators, provenance


def main() -> None:
    """Generate and summarize the real-data raw Step 17 artifact."""

    indicators, provenance = build_protected_area_reinforcement()
    grid = provenance["grid_audit"]
    protected_pixels = grid["total_protected_terrestrial_pixels"]
    print(
        f"Protected-Area Reinforcement raw indicators: {provenance['output']['path']} "
        f"({provenance['output']['size_bytes']:,} bytes)"
    )
    print(
        f"Candidates {len(indicators):,}; terrestrial positions {grid['terrestrial_grid_position_count']:,}; "
        f"protected-terrestrial pixels {protected_pixels:,} ({protected_pixels * PIXEL_AREA_M2 / 1_000_000:.3f} km²)"
    )
    print(
        f"Step 6 reconciliation exact: {grid['terrestrial_reconciliation']['matches_exactly']}; "
        f"nearest-step median {provenance['nearest_protected_grid_distance_distribution']['steps']['median']}"
    )
    print(f"Runtime: {provenance['runtime_seconds']:.2f} seconds")
    print(f"Derived grid: {provenance['derived_grid_output']['path']}")
    print(f"Provenance: {PROVENANCE_PATH}")


if __name__ == "__main__":
    main()
