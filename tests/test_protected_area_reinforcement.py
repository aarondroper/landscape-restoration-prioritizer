import math

import numpy as np
import pandas as pd
from affine import Affine
from rasterio.features import rasterize
from shapely.geometry import box

from restoration_prioritizer.protected_area_reinforcement import (
    GRID_COLUMNS,
    calculate_indicators,
    fraction_for_counts,
    propagate_nearest_protected_steps,
    protected_terrestrial_mask,
    reconcile_terrestrial_counts,
)


def _grid(rows):
    frame = pd.DataFrame(rows, columns=GRID_COLUMNS[:4])
    frame["protected_terrestrial_fraction"] = (
        frame["protected_terrestrial_pixels"] / frame["terrestrial_pixels"]
    )
    return frame


def _candidates(coordinates):
    return pd.DataFrame(
        [
            {
                "hex_id": f"h_{col}_{row}",
                "grid_col": col,
                "grid_row": row,
                "terrestrial_pixels": 100,
            }
            for col, row in coordinates
        ]
    )


def test_protected_terrestrial_definition_requires_protected_and_terrestrial():
    codes = np.array([3, 61, 62, 51, 0])
    protected = np.array([True, True, True, True, True])
    assert protected_terrestrial_mask(codes, protected).tolist() == [
        True,
        False,
        False,
        True,
        False,
    ]


def test_protected_marine_and_inland_water_are_excluded_but_artificial_land_is_included():
    codes = np.array([62, 61, 52, 53])
    protected = np.ones(4, dtype=bool)
    result = protected_terrestrial_mask(codes, protected)
    assert result.tolist() == [False, False, True, True]


def test_pixel_center_rasterization_excludes_touched_but_not_centered_pixel():
    geometry = [(2.1, 0.0, 3.0, 1.0)]
    mask = rasterize(
        [(box(*geometry[0]), 1)],
        out_shape=(1, 3),
        transform=Affine(1, 0, 0, 0, -1, 1),
        all_touched=False,
    )
    assert mask.tolist() == [[0, 0, 1]]


def test_focal_fraction_and_fraction_bounds():
    candidates = _candidates([(0, 0)])
    grid = _grid([(0, 0, 100, 25)])
    indicators = calculate_indicators(candidates, grid, {(0, 0): 0})
    assert indicators.loc[0, "protected_focal_fraction"] == 0.25
    assert 0 <= indicators.loc[0, "protected_focal_fraction"] <= 1
    assert fraction_for_counts(0, 0) != fraction_for_counts(0, 1)


def test_adjacent_fraction_is_pixel_weighted_and_focal_excluded():
    rows = [(0, 0, 100, 100), (1, 0, 100, 0), (-1, 0, 10, 10)]
    indicators = calculate_indicators(_candidates([(0, 0)]), _grid(rows), {(0, 0): 0})
    assert indicators.loc[0, "protected_adjacent_fraction"] == 10 / 110
    assert indicators.loc[0, "protected_local_fraction"] == 10 / 110
    assert indicators.loc[0, "adjacent_protected_pixels"] == 10


def test_local_fraction_uses_ring_two_and_excludes_focal():
    rows = [(0, 0, 100, 100), (2, 0, 100, 50)]
    indicators = calculate_indicators(_candidates([(0, 0)]), _grid(rows), {(0, 0): 0})
    assert math.isnan(indicators.loc[0, "protected_adjacent_fraction"])
    assert indicators.loc[0, "protected_local_fraction"] == 0.5
    assert indicators.loc[0, "local_protected_pixels"] == 50


def test_missing_or_water_only_neighbor_is_not_an_unprotected_denominator():
    rows = [(0, 0, 100, 0), (1, 0, 0, 0)]
    indicators = calculate_indicators(_candidates([(0, 0)]), _grid(rows), {(0, 0): 0})
    assert math.isnan(indicators.loc[0, "protected_adjacent_fraction"])
    assert math.isnan(indicators.loc[0, "protected_local_fraction"])


def test_nearest_protected_steps_focal_neighbor_and_ring_two():
    distances, audit = propagate_nearest_protected_steps(
        {(0, 0)}, {(0, 0), (1, 0), (2, 0)}, {(0, 0)}
    )
    assert distances == {(0, 0): 0, (1, 0): 1, (2, 0): 2}
    assert audit["all_targets_reached"]


def test_distance_propagation_crosses_coordinates_without_terrestrial_rows():
    distances, audit = propagate_nearest_protected_steps({(0, 0)}, {(0, 3)}, {(0, 0), (0, 3)})
    assert distances[(0, 3)] == 3
    assert audit["visited_coordinates_without_terrestrial_grid_rows"] > 0


def test_distance_and_indicator_results_are_deterministic_and_ids_stable():
    candidates = _candidates([(0, 0), (1, 0)])
    grid = _grid([(0, 0, 100, 0), (1, 0, 100, 100)])
    distances = {(0, 0): 1, (1, 0): 0}
    first = calculate_indicators(candidates, grid, distances)
    second = calculate_indicators(candidates, grid, distances)
    pd.testing.assert_frame_equal(first, second)
    assert first["hex_id"].tolist() == ["h_0_0", "h_1_0"]
    assert first["nearest_protected_nominal_m"].tolist() == [500.0, 0.0]


def test_terrestrial_reconciliation_is_exact_and_detects_position_mismatch():
    derived = _grid([(0, 0, 10, 1), (1, 0, 20, 0)])
    analysis = pd.DataFrame(
        {"grid_col": [0, 1], "grid_row": [0, 0], "terrestrial_pixels": [10, 20]}
    )
    result = reconcile_terrestrial_counts(derived, analysis)
    assert result["matches_exactly"]
    derived.loc[1, "terrestrial_pixels"] = 19
    result = reconcile_terrestrial_counts(derived, analysis)
    assert not result["matches_exactly"]
    assert result["delta_pixels"] == -1
