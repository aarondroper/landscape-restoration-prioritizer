from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from restoration_prioritizer.analysis_units import hex_center, hex_polygon
from restoration_prioritizer.habitat_context import (
    LOCAL_OFFSETS,
    RING_1_OFFSETS,
    RING_2_OFFSETS,
    axial_distance,
    calculate_boundary_edge_flags,
    calculate_indicators,
)


def make_units(
    coordinates: list[tuple[int, int]],
    habitat: dict[tuple[int, int], int] | None = None,
    terrestrial: dict[tuple[int, int], int] | None = None,
) -> gpd.GeoDataFrame:
    habitat = habitat or {}
    terrestrial = terrestrial or {}
    rows = []
    for grid_col, grid_row in coordinates:
        terrestrial_pixels = terrestrial.get((grid_col, grid_row), 100)
        habitat_pixels = habitat.get((grid_col, grid_row), 0)
        rows.append(
            {
                "hex_id": f"h_{grid_col}_{grid_row}",
                "grid_col": grid_col,
                "grid_row": grid_row,
                "terrestrial_pixels": terrestrial_pixels,
                "habitat_context_pixels": habitat_pixels,
                "candidate_fraction_of_terrestrial": 0.5,
                "candidate_area_m2": 50_000.0,
                "habitat_context_fraction_of_terrestrial": 0.25,
                "geometry": hex_polygon(grid_col, grid_row),
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3006")


def test_axial_distance_and_exact_ring_offsets_match_grid_geometry() -> None:
    assert axial_distance((0, 0)) == 0
    assert axial_distance((1, -1)) == 1
    assert axial_distance((2, -1)) == 2
    assert len(RING_1_OFFSETS) == 6
    assert len(RING_2_OFFSETS) == 12
    assert len(LOCAL_OFFSETS) == 18
    assert set(RING_1_OFFSETS).isdisjoint(RING_2_OFFSETS)
    assert all(axial_distance(offset) == 1 for offset in RING_1_OFFSETS)
    assert all(axial_distance(offset) == 2 for offset in RING_2_OFFSETS)

    focal = hex_center(0, 0)
    ring_1_distances = [math.dist(focal, hex_center(*offset)) for offset in RING_1_OFFSETS]
    ring_2_distances = [math.dist(focal, hex_center(*offset)) for offset in RING_2_OFFSETS]
    assert ring_1_distances == pytest.approx([500.0] * 6)
    assert min(ring_2_distances) == pytest.approx(500.0 * math.sqrt(3.0))
    assert max(ring_2_distances) == pytest.approx(1_000.0)


def test_focal_cell_is_excluded_and_full_grid_noncandidates_contribute() -> None:
    coordinates = [(0, 0), *RING_1_OFFSETS, *RING_2_OFFSETS]
    habitat = {(0, 0): 100}
    habitat.update({offset: 10 for offset in RING_1_OFFSETS})
    habitat.update({offset: 20 for offset in RING_2_OFFSETS})
    full_grid = make_units(coordinates, habitat=habitat)
    candidates = full_grid.loc[full_grid.hex_id == "h_0_0"].copy()

    indicators = calculate_indicators(candidates, full_grid)
    result = indicators.iloc[0]

    assert len(indicators) == 1
    assert result.adjacent_habitat_pixels == 60
    assert result.adjacent_terrestrial_pixels == 600
    assert result.habitat_context_adjacent_fraction == pytest.approx(0.1)
    assert result.local_habitat_pixels == 300
    assert result.local_terrestrial_pixels == 1_800
    assert result.habitat_context_local_fraction == pytest.approx(1 / 6)
    assert result.adjacent_terrestrial_cells_present == 6
    assert result.local_terrestrial_cells_present == 18


def test_missing_positions_are_not_zero_and_zero_denominators_are_missing() -> None:
    full_grid = make_units([(0, 0)], habitat={(0, 0): 100})
    candidates = full_grid.copy()

    indicators = calculate_indicators(candidates, full_grid)
    result = indicators.iloc[0]

    assert result.adjacent_terrestrial_cells_present == 0
    assert result.local_terrestrial_cells_present == 0
    assert result.adjacent_terrestrial_pixels == 0
    assert result.local_terrestrial_pixels == 0
    assert pd.isna(result.habitat_context_adjacent_fraction)
    assert pd.isna(result.habitat_context_local_fraction)


def test_aggregate_pixels_conserve_and_fractions_stay_in_range() -> None:
    coordinates = [(0, 0), *RING_1_OFFSETS]
    full_grid = make_units(
        coordinates,
        habitat={(offset): 25 for offset in RING_1_OFFSETS},
        terrestrial={offset: 100 for offset in RING_1_OFFSETS},
    )
    indicators = calculate_indicators(full_grid.iloc[[0]].copy(), full_grid)

    result = indicators.iloc[0]
    assert result.adjacent_habitat_pixels <= result.adjacent_terrestrial_pixels
    assert result.local_habitat_pixels <= result.local_terrestrial_pixels
    assert 0 <= result.habitat_context_adjacent_fraction <= 1
    assert 0 <= result.habitat_context_local_fraction <= 1
    assert result.adjacent_habitat_pixels == 150
    assert result.local_habitat_pixels == 150


def test_output_is_one_row_per_candidate_with_stable_ids() -> None:
    coordinates = [(0, 0), (2, 0), *RING_1_OFFSETS]
    full_grid = make_units(coordinates)
    candidates = full_grid.loc[full_grid.hex_id.isin(["h_0_0", "h_2_0"])].copy()

    first = calculate_indicators(candidates, full_grid)
    second = calculate_indicators(candidates, full_grid)

    assert first.columns.tolist() == [
        "hex_id",
        "adjacent_habitat_pixels",
        "adjacent_terrestrial_pixels",
        "habitat_context_adjacent_fraction",
        "adjacent_terrestrial_cells_present",
        "local_habitat_pixels",
        "local_terrestrial_pixels",
        "habitat_context_local_fraction",
        "local_terrestrial_cells_present",
    ]
    assert first.hex_id.tolist() == ["h_0_0", "h_2_0"]
    pd.testing.assert_frame_equal(first, second)


def test_boundary_edge_flag_uses_centroid_distance_to_boundary() -> None:
    candidates = gpd.GeoDataFrame(
        {"hex_id": ["near", "far"]},
        geometry=[box(450, 450, 550, 550), box(5_000, 5_000, 5_100, 5_100)],
        crs="EPSG:3006",
    )
    flags = calculate_boundary_edge_flags(candidates, box(0, 0, 1_000, 1_000))

    assert flags.tolist() == [True, False]
