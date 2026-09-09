from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box

from restoration_prioritizer.candidate_units import (
    CandidateUnitsError,
    calculate_diagnostics,
    eligibility_mask,
)


def make_units(rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    defaults = {
        "grid_col": 0,
        "grid_row": 0,
        "terrestrial_pixels": 1000,
        "terrestrial_area_m2": 100_000.0,
        "terrestrial_fraction": 0.46,
        "candidate_pixels": 500,
        "candidate_area_m2": 50_000.0,
        "candidate_fraction_of_terrestrial": 0.5,
        "habitat_context_pixels": 0,
        "habitat_context_area_m2": 0.0,
        "habitat_context_fraction_of_terrestrial": 0.0,
        "wetland_context_pixels": 0,
        "wetland_context_area_m2": 0.0,
        "inland_water_pixels": 0,
        "inland_water_area_m2": 0.0,
        "artificial_constraint_pixels": 0,
        "artificial_constraint_area_m2": 0.0,
        "transitional_forest_pixels": 0,
        "transitional_forest_area_m2": 0.0,
        "peat_extraction_pixels": 0,
        "peat_extraction_area_m2": 0.0,
        "sea_pixels": 0,
        "sea_area_m2": 0.0,
    }
    records = []
    for index, row in enumerate(rows):
        record = {**defaults, **row}
        record.setdefault("hex_id", f"h_{index}_0")
        record["geometry"] = box(index * 500.0, 0, index * 500.0 + 400, 400)
        records.append(record)
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:3006")


def test_eligibility_mask_uses_exact_two_inclusive_conditions() -> None:
    units = make_units(
        [
            {"hex_id": "both_pass"},
            {
                "hex_id": "exact_thresholds",
                "terrestrial_pixels": 2000,
                "terrestrial_area_m2": 200_000.0,
                "candidate_area_m2": 50_000.0,
                "candidate_fraction_of_terrestrial": 0.25,
            },
            {
                "hex_id": "fraction_fail",
                "candidate_pixels": 500,
                "terrestrial_pixels": 2100,
                "candidate_area_m2": 50_000.0,
                "candidate_fraction_of_terrestrial": 500 / 2100,
            },
            {
                "hex_id": "area_fail",
                "candidate_pixels": 499,
                "candidate_area_m2": 49_900.0,
                "candidate_fraction_of_terrestrial": 499 / 1000,
            },
            {
                "hex_id": "both_fail",
                "candidate_pixels": 499,
                "terrestrial_pixels": 2100,
                "candidate_area_m2": 49_900.0,
                "candidate_fraction_of_terrestrial": 499 / 2100,
            },
            {
                "hex_id": "zero_candidate",
                "candidate_pixels": 0,
                "candidate_area_m2": 0.0,
                "candidate_fraction_of_terrestrial": 0.0,
            },
        ]
    )

    assert units.loc[eligibility_mask(units), "hex_id"].tolist() == [
        "both_pass",
        "exact_thresholds",
    ]


def test_failure_reason_accounting_and_arable_conservation() -> None:
    units = make_units(
        [
            {"hex_id": "eligible_exact", "candidate_pixels": 500},
            {
                "hex_id": "area_only",
                "candidate_pixels": 499,
                "candidate_area_m2": 49_900.0,
                "candidate_fraction_of_terrestrial": 499 / 1000,
            },
            {
                "hex_id": "fraction_only",
                "candidate_pixels": 500,
                "candidate_area_m2": 50_000.0,
                "terrestrial_pixels": 2100,
                "candidate_fraction_of_terrestrial": 500 / 2100,
            },
            {
                "hex_id": "both_fail",
                "candidate_pixels": 499,
                "candidate_area_m2": 49_900.0,
                "terrestrial_pixels": 2100,
                "candidate_fraction_of_terrestrial": 499 / 2100,
            },
            {
                "hex_id": "no_arable",
                "candidate_pixels": 0,
                "candidate_area_m2": 0.0,
                "candidate_fraction_of_terrestrial": 0.0,
            },
            {
                "hex_id": "eligible_context",
                "candidate_pixels": 600,
                "candidate_area_m2": 60_000.0,
                "candidate_fraction_of_terrestrial": 0.6,
                "terrestrial_fraction": 0.2,
                "sea_pixels": 2,
                "inland_water_pixels": 3,
                "artificial_constraint_pixels": 4,
                "habitat_context_pixels": 5,
                "wetland_context_pixels": 6,
            },
        ]
    )

    diagnostics = calculate_diagnostics(units)

    assert diagnostics["population"] == {
        "total_terrestrial_analysis_units": 6,
        "units_containing_any_arable_land": 5,
        "eligible_candidate_units": 2,
        "ineligible_units": 4,
        "ineligible_units_containing_some_arable_land": 3,
    }
    assert diagnostics["failure_reasons_among_units_with_any_arable"] == {
        "area_below_5ha_only": 1,
        "fraction_below_25_percent_only": 1,
        "both_area_and_fraction_fail": 1,
        "pass_both": 2,
    }
    conservation = diagnostics["candidate_pixel_conservation"]
    assert conservation["eligible_candidate_pixels"] == 1100
    assert conservation["excluded_terrestrial_candidate_pixels"] == 1498
    assert conservation["full_analysis_unit_candidate_pixels"] == 2598
    assert conservation["matches_exactly"] is True
    assert (
        diagnostics["edge_case_diagnostics"]["eligible_terrestrial_fraction_below_25_percent"] == 1
    )
    assert diagnostics["edge_case_diagnostics"]["eligible_containing_sea_pixels"] == 1
    assert diagnostics["edge_case_diagnostics"]["eligible_containing_inland_water_pixels"] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda units: units.assign(candidate_fraction_of_terrestrial=1.01),
        lambda units: units.assign(hex_id="duplicate"),
        lambda units: units.drop(columns="candidate_area_m2"),
    ],
    ids=["malformed_fraction", "duplicate_ids", "missing_schema"],
)
def test_malformed_analysis_units_are_rejected(change) -> None:
    units = make_units([{}, {}])

    with pytest.raises(CandidateUnitsError):
        eligibility_mask(change(units))
