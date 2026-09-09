import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.analysis_units import hex_polygon
from restoration_prioritizer.habitat_context import LOCAL_OFFSETS, RING_1_OFFSETS
from restoration_prioritizer.land_restoration_feasibility import (
    OUTPUT_COLUMNS,
    LandRestorationFeasibilityError,
    calculate_indicators,
    candidate_area_to_hectares,
    validate_approved_semantics,
    validate_candidate_output_reconciliation,
)
from restoration_prioritizer.nmd_semantics import (
    ARABLE,
    ARTIFICIAL_BUILDING,
    ARTIFICIAL_CONSTRAINT,
    ARTIFICIAL_OTHER,
    ARTIFICIAL_TRANSPORT,
    PEAT_EXTRACTION,
    ROLE_FACTUAL_GROUPS,
)


def _row(
    col: int,
    row: int,
    *,
    terrestrial: int = 100,
    artificial: int = 0,
    candidate: int = 50,
    candidate_fraction: float = 0.5,
) -> dict:
    return {
        "hex_id": f"h_{col}_{row}",
        "grid_col": col,
        "grid_row": row,
        "candidate_pixels": candidate,
        "candidate_area_m2": float(candidate * 100),
        "candidate_fraction_of_terrestrial": candidate_fraction,
        "terrestrial_pixels": terrestrial,
        "terrestrial_area_m2": float(terrestrial * 100),
        "terrestrial_fraction": terrestrial / 100,
        "artificial_constraint_pixels": artificial,
        "artificial_constraint_area_m2": float(artificial * 100),
        "peat_extraction_pixels": 0,
        "sea_pixels": 0,
        "geometry": hex_polygon(col, row),
    }


def _frames(analysis_rows: list[dict], candidate_coords: list[tuple[int, int]]):
    analysis = gpd.GeoDataFrame(analysis_rows, geometry="geometry", crs="EPSG:3006")
    candidates = analysis.loc[
        analysis[["grid_col", "grid_row"]].apply(tuple, axis=1).isin(candidate_coords)
    ].copy()
    return candidates, analysis


def test_candidate_area_conversion_m2_to_hectares() -> None:
    np.testing.assert_allclose(
        candidate_area_to_hectares(pd.Series([50_200.0, 216_600.0])), [5.02, 21.66]
    )


def test_candidate_fraction_is_preserved_without_recalculation() -> None:
    candidates, analysis = _frames([_row(0, 0, candidate_fraction=0.37)], [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert result.loc[0, "candidate_land_fraction"] == 0.37


def test_focal_artificial_fraction_uses_step6_counts() -> None:
    candidates, analysis = _frames([_row(0, 0, terrestrial=100, artificial=20)], [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert result.loc[0, "artificial_focal_fraction"] == pytest.approx(0.2)


def test_adjacent_fraction_is_pixel_weighted() -> None:
    rows = [_row(0, 0)]
    rows += [
        _row(*RING_1_OFFSETS[0], terrestrial=100, artificial=20),
        _row(*RING_1_OFFSETS[1], terrestrial=10, artificial=0),
    ]
    candidates, analysis = _frames(rows, [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert result.loc[0, "artificial_adjacent_fraction"] == pytest.approx(20 / 110)


def test_local_fraction_is_pixel_weighted_across_both_rings() -> None:
    rows = [_row(0, 0)]
    rows += [_row(dc, dr, terrestrial=100, artificial=20) for dc, dr in RING_1_OFFSETS]
    rows += [
        _row(dc, dr, terrestrial=10, artificial=0)
        for dc, dr in LOCAL_OFFSETS
        if (dc, dr) not in RING_1_OFFSETS
    ]
    candidates, analysis = _frames(rows, [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert result.loc[0, "artificial_local_fraction"] == pytest.approx(120 / 720)


def test_focal_is_excluded_from_adjacent_and_local() -> None:
    rows = [_row(0, 0, artificial=100, terrestrial=100)]
    rows += [_row(dc, dr, terrestrial=100, artificial=0) for dc, dr in LOCAL_OFFSETS]
    candidates, analysis = _frames(rows, [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert result.loc[0, "artificial_focal_fraction"] == 1
    assert result.loc[0, "artificial_adjacent_fraction"] == 0
    assert result.loc[0, "artificial_local_fraction"] == 0


def test_missing_or_water_only_neighbor_adds_no_denominator() -> None:
    rows = [_row(0, 0), _row(*RING_1_OFFSETS[0], terrestrial=0, artificial=0)]
    candidates, analysis = _frames(rows, [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert pd.isna(result.loc[0, "artificial_adjacent_fraction"])
    assert pd.isna(result.loc[0, "artificial_local_fraction"])


def test_noncandidate_surrounding_terrestrial_cells_contribute() -> None:
    neighbor = RING_1_OFFSETS[0]
    rows = [_row(0, 0), _row(*neighbor, terrestrial=100, artificial=30)]
    candidates, analysis = _frames(rows, [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    assert result.loc[0, "artificial_adjacent_fraction"] == pytest.approx(0.3)


def test_artificial_role_reuses_only_approved_semantics() -> None:
    validate_approved_semantics()
    assert ROLE_FACTUAL_GROUPS[ARTIFICIAL_CONSTRAINT] == (
        ARTIFICIAL_BUILDING,
        ARTIFICIAL_OTHER,
        ARTIFICIAL_TRANSPORT,
    )
    assert PEAT_EXTRACTION not in ROLE_FACTUAL_GROUPS[ARTIFICIAL_CONSTRAINT]
    assert ROLE_FACTUAL_GROUPS["primary_candidate"] == (ARABLE,)


def test_fractions_remain_in_range_when_denominators_are_positive() -> None:
    rows = [_row(0, 0, terrestrial=100, artificial=10)]
    rows += [_row(dc, dr, terrestrial=100, artificial=25) for dc, dr in RING_1_OFFSETS]
    candidates, analysis = _frames(rows, [(0, 0)])
    result = calculate_indicators(candidates, analysis)
    for field in (
        "candidate_land_fraction",
        "artificial_focal_fraction",
        "artificial_adjacent_fraction",
    ):
        assert result[field].between(0, 1).all()


def test_candidate_output_id_reconciliation() -> None:
    candidates, analysis = _frames([_row(0, 0), _row(1, 0)], [(0, 0), (1, 0)])
    result = calculate_indicators(candidates, analysis)
    audit = validate_candidate_output_reconciliation(candidates, result)
    assert audit["ids_reconcile_exactly"]
    with pytest.raises(LandRestorationFeasibilityError):
        validate_candidate_output_reconciliation(candidates, result.iloc[[0]])


def test_output_is_deterministic_and_has_narrow_schema() -> None:
    rows = [_row(0, 0), _row(1, 0), _row(2, 0)]
    candidates, analysis = _frames(rows, [(0, 0), (1, 0), (2, 0)])
    first = calculate_indicators(candidates.iloc[[2, 0, 1]], analysis)
    second = calculate_indicators(candidates.iloc[[1, 2, 0]], analysis)
    pd.testing.assert_frame_equal(first, second)
    assert first.columns.tolist() == list(OUTPUT_COLUMNS)
    assert first.hex_id.tolist() == ["h_0_0", "h_1_0", "h_2_0"]
