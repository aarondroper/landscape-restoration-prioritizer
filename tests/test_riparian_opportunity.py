from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.habitat_context import LOCAL_OFFSETS, RING_1_OFFSETS
from restoration_prioritizer.riparian_opportunity import (
    calculate_indicators,
    fraction_for_counts,
    hydrologic_counts_from_codes,
)


def grid_frame(records: list[dict[str, int]]) -> pd.DataFrame:
    defaults = {
        "nmd_valid_pixels": 100,
        "terrestrial_land_pixels": 100,
        "wetland_context_pixels": 0,
        "inland_water_pixels": 0,
        "sea_pixels": 0,
    }
    rows = []
    for record in records:
        row = {**defaults, **record}
        row["hex_id"] = f"h_{row['grid_col']}_{row['grid_row']}"
        row["hydrologic_context_pixels"] = (
            row["wetland_context_pixels"] + row["inland_water_pixels"]
        )
        row["nonmarine_context_pixels"] = (
            row["terrestrial_land_pixels"] + row["inland_water_pixels"]
        )
        rows.append(row)
    return pd.DataFrame(rows)


def candidate_frame(coordinates: list[tuple[int, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hex_id": [f"h_{col}_{row}" for col, row in coordinates],
            "grid_col": [col for col, _ in coordinates],
            "grid_row": [row for _, row in coordinates],
            "candidate_fraction_of_terrestrial": [0.5] * len(coordinates),
            "candidate_area_m2": [50_000.0] * len(coordinates),
        }
    )


def test_hydrologic_union_and_denominator_use_approved_mutually_exclusive_roles() -> None:
    counts = hydrologic_counts_from_codes([121, 128, 200, 61, 3, 62, 0])

    assert counts["wetland_context_pixels"] == 3
    assert counts["inland_water_pixels"] == 1
    assert counts["hydrologic_context_pixels"] == 4
    assert counts["terrestrial_land_pixels"] == 4
    assert counts["nonmarine_context_pixels"] == 5
    assert counts["sea_pixels"] == 1


def test_water_only_cell_has_fraction_one_and_sea_only_has_missing_fraction() -> None:
    water_only = hydrologic_counts_from_codes([61])
    sea_only = hydrologic_counts_from_codes([62])

    assert fraction_for_counts(
        water_only["hydrologic_context_pixels"], water_only["nonmarine_context_pixels"]
    ) == pytest.approx(1.0)
    assert sea_only["nonmarine_context_pixels"] == 0
    assert np.isnan(
        fraction_for_counts(
            sea_only["hydrologic_context_pixels"], sea_only["nonmarine_context_pixels"]
        )
    )


def test_focal_calculation_uses_only_focal_grid_pixels_and_split_sums() -> None:
    grid = grid_frame(
        [
            {
                "grid_col": 0,
                "grid_row": 0,
                "terrestrial_land_pixels": 7,
                "wetland_context_pixels": 2,
                "inland_water_pixels": 1,
            },
        ]
    )
    result = calculate_indicators(candidate_frame([(0, 0)]), grid).iloc[0]

    assert result.riparian_focal_fraction == pytest.approx(3 / 8)
    assert result.focal_wetland_fraction == pytest.approx(2 / 8)
    assert result.focal_inland_water_fraction == pytest.approx(1 / 8)
    assert result.riparian_adjacent_fraction != result.riparian_focal_fraction
    assert pd.isna(result.riparian_adjacent_fraction)


def test_adjacent_and_local_are_pixel_weighted_and_focal_excluded() -> None:
    records = [
        {"grid_col": 0, "grid_row": 0, "terrestrial_land_pixels": 10, "wetland_context_pixels": 10},
    ]
    records.extend(
        {
            "grid_col": col,
            "grid_row": row,
            "terrestrial_land_pixels": 100,
            "wetland_context_pixels": 10,
        }
        for col, row in RING_1_OFFSETS
    )
    records.extend(
        {
            "grid_col": col,
            "grid_row": row,
            "terrestrial_land_pixels": 20,
            "wetland_context_pixels": 20,
        }
        for col, row in LOCAL_OFFSETS
        if (col, row) not in RING_1_OFFSETS
    )
    result = calculate_indicators(candidate_frame([(0, 0)]), grid_frame(records)).iloc[0]

    assert result.riparian_adjacent_fraction == pytest.approx(60 / 600)
    assert result.riparian_local_fraction == pytest.approx((60 + 240) / (600 + 240))
    assert result.adjacent_wetland_fraction == pytest.approx(result.riparian_adjacent_fraction)
    assert result.local_wetland_fraction == pytest.approx(result.riparian_local_fraction)


def test_water_only_neighbor_contributes_and_missing_or_sea_only_does_not_become_land() -> None:
    water_neighbor = RING_1_OFFSETS[0]
    land_neighbor = RING_1_OFFSETS[1]
    sea_neighbor = RING_1_OFFSETS[2]
    records = [
        {"grid_col": 0, "grid_row": 0, "terrestrial_land_pixels": 100},
        {
            "grid_col": water_neighbor[0],
            "grid_row": water_neighbor[1],
            "terrestrial_land_pixels": 0,
            "inland_water_pixels": 10,
        },
        {"grid_col": land_neighbor[0], "grid_row": land_neighbor[1], "terrestrial_land_pixels": 10},
        {
            "grid_col": sea_neighbor[0],
            "grid_row": sea_neighbor[1],
            "terrestrial_land_pixels": 0,
            "sea_pixels": 20,
        },
    ]
    result = calculate_indicators(candidate_frame([(0, 0)]), grid_frame(records)).iloc[0]

    assert result.adjacent_nonmarine_context_pixels == 20
    assert result.riparian_adjacent_fraction == pytest.approx(10 / 20)
    assert result.adjacent_positions_present == 3
    assert result.adjacent_water_only_positions == 1

    only_sea = grid_frame(
        [
            {"grid_col": 0, "grid_row": 0, "terrestrial_land_pixels": 100},
            {
                "grid_col": sea_neighbor[0],
                "grid_row": sea_neighbor[1],
                "terrestrial_land_pixels": 0,
                "sea_pixels": 20,
            },
        ]
    )
    missing_result = calculate_indicators(candidate_frame([(0, 0)]), only_sea).iloc[0]
    assert missing_result.adjacent_positions_present == 1
    assert missing_result.adjacent_nonmarine_context_pixels == 0
    assert pd.isna(missing_result.riparian_adjacent_fraction)


def test_zero_denominators_ids_and_fractions_are_stable() -> None:
    candidates = candidate_frame([(0, 0), (1, 0)])
    grid = grid_frame(
        [
            {"grid_col": 0, "grid_row": 0, "terrestrial_land_pixels": 100},
            {"grid_col": 1, "grid_row": 0, "terrestrial_land_pixels": 100},
        ]
    )
    first = calculate_indicators(candidates, grid)
    second = calculate_indicators(candidates, grid)

    assert first.hex_id.tolist() == ["h_0_0", "h_1_0"]
    pd.testing.assert_frame_equal(first, second)
    fraction_columns = [
        "riparian_focal_fraction",
        "riparian_adjacent_fraction",
        "riparian_local_fraction",
        "focal_wetland_fraction",
        "focal_inland_water_fraction",
        "adjacent_wetland_fraction",
        "adjacent_inland_water_fraction",
        "local_wetland_fraction",
        "local_inland_water_fraction",
    ]
    for column in fraction_columns:
        values = first[column].dropna()
        assert ((values >= 0) & (values <= 1)).all()


def test_near_fraction_uses_max_focal_and_adjacent() -> None:
    adjacent_coordinate = RING_1_OFFSETS[0]
    grid = grid_frame(
        [
            {"grid_col": 0, "grid_row": 0, "wetland_context_pixels": 20},
            {
                "grid_col": adjacent_coordinate[0],
                "grid_row": adjacent_coordinate[1],
                "wetland_context_pixels": 5,
            },
        ]
    )
    result = calculate_indicators(candidate_frame([(0, 0)]), grid).iloc[0]

    assert result.riparian_focal_fraction == pytest.approx(0.20)
    assert result.riparian_adjacent_fraction == pytest.approx(0.05)
    assert result.riparian_near_fraction == pytest.approx(0.20)


def test_near_fraction_uses_adjacent_when_adjacent_is_greater() -> None:
    adjacent_coordinate = RING_1_OFFSETS[0]
    grid = grid_frame(
        [
            {"grid_col": 0, "grid_row": 0, "wetland_context_pixels": 1},
            {
                "grid_col": adjacent_coordinate[0],
                "grid_row": adjacent_coordinate[1],
                "wetland_context_pixels": 30,
            },
        ]
    )
    result = calculate_indicators(candidate_frame([(0, 0)]), grid).iloc[0]

    assert result.riparian_near_fraction == pytest.approx(0.30)


def test_near_fraction_equality_zero_bounds_and_local_independence() -> None:
    adjacent_coordinate = RING_1_OFFSETS[0]
    second_ring_coordinate = next(
        coordinate for coordinate in LOCAL_OFFSETS if coordinate not in RING_1_OFFSETS
    )

    equality_grid = grid_frame(
        [
            {"grid_col": 0, "grid_row": 0, "wetland_context_pixels": 20},
            {
                "grid_col": adjacent_coordinate[0],
                "grid_row": adjacent_coordinate[1],
                "wetland_context_pixels": 20,
            },
        ]
    )
    equality = calculate_indicators(candidate_frame([(0, 0)]), equality_grid).iloc[0]
    assert equality.riparian_near_fraction == pytest.approx(0.20)
    assert equality.riparian_near_fraction == equality.riparian_focal_fraction
    assert equality.riparian_near_fraction == equality.riparian_adjacent_fraction

    zero_grid = grid_frame([{"grid_col": 0, "grid_row": 0}])
    zero = calculate_indicators(candidate_frame([(0, 0)]), zero_grid).iloc[0]
    assert zero.riparian_near_fraction == pytest.approx(0.0)

    bounded_grid = grid_frame(
        [
            {
                "grid_col": 0,
                "grid_row": 0,
                "terrestrial_land_pixels": 0,
                "inland_water_pixels": 100,
            },
            {
                "grid_col": adjacent_coordinate[0],
                "grid_row": adjacent_coordinate[1],
                "terrestrial_land_pixels": 0,
                "inland_water_pixels": 100,
            },
        ]
    )
    bounded = calculate_indicators(candidate_frame([(0, 0)]), bounded_grid).iloc[0]
    assert 0 <= bounded.riparian_near_fraction <= 1

    local_low = calculate_indicators(candidate_frame([(0, 0)]), equality_grid).iloc[0]
    local_high_grid = grid_frame(
        [
            *equality_grid.to_dict("records"),
            {
                "grid_col": second_ring_coordinate[0],
                "grid_row": second_ring_coordinate[1],
                "wetland_context_pixels": 100,
            },
        ]
    )
    local_high = calculate_indicators(candidate_frame([(0, 0)]), local_high_grid).iloc[0]
    assert local_high.riparian_near_fraction == local_low.riparian_near_fraction
    assert local_high.riparian_local_fraction > local_low.riparian_local_fraction
