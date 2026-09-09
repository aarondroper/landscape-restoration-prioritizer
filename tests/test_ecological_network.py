from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
import pytest

from restoration_prioritizer.analysis_units import hex_center, hex_polygon
from restoration_prioritizer.ecological_network import (
    FIRST_RING_OFFSETS,
    OPPOSITE_AXIS_PAIRS,
    EcologicalNetworkError,
    calculate_indicators,
    validate_grid_inputs,
)


def make_units(
    coordinates: list[tuple[int, int]],
    habitat: dict[tuple[int, int], float] | None = None,
    candidate_fraction: dict[tuple[int, int], float] | None = None,
) -> gpd.GeoDataFrame:
    habitat = habitat or {}
    candidate_fraction = candidate_fraction or {}
    rows = []
    for grid_col, grid_row in coordinates:
        rows.append(
            {
                "hex_id": f"h_{grid_col}_{grid_row}",
                "grid_col": grid_col,
                "grid_row": grid_row,
                "terrestrial_pixels": 100,
                "habitat_context_pixels": 0,
                "candidate_pixels": 50,
                "candidate_area_m2": 50_000.0,
                "candidate_fraction_of_terrestrial": candidate_fraction.get(
                    (grid_col, grid_row), 0.5
                ),
                "habitat_context_fraction_of_terrestrial": habitat.get((grid_col, grid_row), 0.0),
                "geometry": hex_polygon(grid_col, grid_row),
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3006")


def test_opposite_pair_definition_is_exact_and_complete() -> None:
    assert FIRST_RING_OFFSETS == (
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
    )
    assert OPPOSITE_AXIS_PAIRS == {
        "axis_a": ((-1, 0), (1, 0)),
        "axis_b": ((0, -1), (0, 1)),
        "axis_c": ((-1, 1), (1, -1)),
    }


def test_pair_vectors_are_mathematically_opposite_on_hex_grid() -> None:
    focal = hex_center(0, 0)
    for side_1, side_2 in OPPOSITE_AXIS_PAIRS.values():
        first = hex_center(*side_1)
        second = hex_center(*side_2)
        assert (first[0] - focal[0]) + (second[0] - focal[0]) == pytest.approx(0)
        assert (first[1] - focal[1]) + (second[1] - focal[1]) == pytest.approx(0)


def test_axis_strength_uses_limiting_side_minimum() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(
        coordinates,
        habitat={(-1, 0): 0.8, (1, 0): 0.7, (0, -1): 0.9, (0, 1): 0.1},
    )
    result = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    assert result.bridge_axis_a_strength == pytest.approx(0.7)
    assert result.bridge_axis_b_strength == pytest.approx(0.1)
    assert result.bridge_strength_max == pytest.approx(0.7)
    assert result.bridge_strength_mean == pytest.approx((0.7 + 0.1) / 3)


def test_strongest_max_and_mean_calculations() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    habitat = dict(zip(FIRST_RING_OFFSETS, [0.2, 0.8, 0.4, 0.4, 0.6, 0.2], strict=True))
    result = calculate_indicators(
        make_units(coordinates, habitat=habitat).iloc[[0]], make_units(coordinates, habitat=habitat)
    ).iloc[0]
    assert result.bridge_axis_a_strength == pytest.approx(0.2)
    assert result.bridge_axis_b_strength == pytest.approx(0.4)
    assert result.bridge_axis_c_strength == pytest.approx(0.6)
    assert result.bridge_strength_max == pytest.approx(0.6)
    assert result.bridge_strength_mean == pytest.approx(0.4)
    assert result.strongest_axis == "axis_c"


def test_one_sided_habitat_has_zero_bridge_strength() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(coordinates, habitat={(-1, 0): 0.9})
    result = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    assert result.bridge_strength_max == 0
    assert result.bridge_strength_mean == 0


def test_habitat_on_opposing_sides_produces_expected_bridge() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(coordinates, habitat={(-1, 0): 0.8, (1, 0): 0.7})
    result = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    assert result.bridge_axis_a_strength == pytest.approx(0.7)
    assert result.bridge_strength_max == pytest.approx(0.7)


def test_adjacent_nonopposing_habitat_does_not_create_same_bridge_signal() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(coordinates, habitat={(-1, 0): 0.8, (0, -1): 0.8})
    result = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    assert result.bridge_strength_max == 0
    opposing = make_units(coordinates, habitat={(-1, 0): 0.8, (1, 0): 0.8})
    opposing_result = calculate_indicators(opposing.iloc[[0]], opposing).iloc[0]
    assert opposing_result.bridge_strength_max == pytest.approx(0.8)


def test_missing_neighbor_is_zero_and_counted() -> None:
    full_grid = make_units([(0, 0), (-1, 0), (1, 0)], habitat={(-1, 0): 0.8, (1, 0): 0.7})
    result = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    assert result.bridge_axis_a_strength == pytest.approx(0.7)
    assert result.bridge_axis_b_strength == 0
    assert result.bridge_strength_max == pytest.approx(0.7)
    assert result.adjacent_cells_missing == 4


def test_noncandidate_surrounding_cells_contribute_and_focal_is_excluded() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(coordinates, habitat={(1, 0): 0.6})
    candidates = full_grid.iloc[[0]].copy()
    candidates["candidate_fraction_of_terrestrial"] = 0.9
    result = calculate_indicators(candidates, full_grid).iloc[0]
    assert result.bridge_strength_max == 0
    full_grid.loc[full_grid.hex_id == "h_0_0", "habitat_context_fraction_of_terrestrial"] = 1.0
    focal_changed = calculate_indicators(candidates, full_grid).iloc[0]
    assert focal_changed.bridge_strength_max == result.bridge_strength_max


def test_output_has_one_stable_row_per_candidate() -> None:
    coordinates = [(0, 0), (2, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(coordinates, habitat={(1, 0): 0.6})
    candidates = full_grid.loc[full_grid.hex_id.isin(["h_2_0", "h_0_0"])].copy()
    result = calculate_indicators(candidates, full_grid)
    assert len(result) == 2
    assert result.hex_id.tolist() == ["h_0_0", "h_2_0"]
    assert result.hex_id.is_unique


def test_strongest_axis_tie_is_deterministic_and_counted() -> None:
    coordinates = [(0, 0), *FIRST_RING_OFFSETS]
    full_grid = make_units(coordinates, habitat={offset: 0.5 for offset in FIRST_RING_OFFSETS})
    first = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    second = calculate_indicators(full_grid.iloc[[0]], full_grid).iloc[0]
    assert first.strongest_axis == second.strongest_axis == "axis_a"
    assert first.strongest_axis_tie_count == 3


def test_ids_are_reconciled_and_missing_candidate_is_rejected() -> None:
    full_grid = make_units([(0, 0), *FIRST_RING_OFFSETS])
    missing = make_units([(9, 9)])
    with pytest.raises(EcologicalNetworkError, match="missing from full analysis grid"):
        validate_grid_inputs(full_grid, missing)
    duplicate = pd.concat([full_grid, full_grid.iloc[[0]]], ignore_index=True)
    duplicate = gpd.GeoDataFrame(duplicate, geometry="geometry", crs="EPSG:3006")
    with pytest.raises(EcologicalNetworkError, match="unique"):
        validate_grid_inputs(duplicate, duplicate)


def test_fractions_must_stay_in_zero_to_one() -> None:
    full_grid = make_units([(0, 0), *FIRST_RING_OFFSETS])
    full_grid.loc[1, "habitat_context_fraction_of_terrestrial"] = 1.01
    with pytest.raises(EcologicalNetworkError, match=r"\[0, 1\]"):
        validate_grid_inputs(full_grid, full_grid.iloc[[0]])


def test_neighbor_centers_have_expected_first_ring_geometry() -> None:
    focal = hex_center(0, 0)
    distances = [math.dist(focal, hex_center(*offset)) for offset in FIRST_RING_OFFSETS]
    assert distances == pytest.approx([500.0] * 6)
