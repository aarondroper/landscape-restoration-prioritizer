import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.land_restoration_feasibility_score import (
    OUTPUT_COLUMNS,
    RestorationLandAvailabilityError,
    calculate_scores,
    percentile_scores,
    validate_candidate_area_reconciliation,
    validate_candidate_reconciliation,
    validate_raw_indicators,
)


def _raw(areas: list[float] | None = None) -> pd.DataFrame:
    areas = areas or [5.0, 10.0, 20.0, 20.0]
    return pd.DataFrame(
        {
            "hex_id": [f"h_{i}_0" for i in range(len(areas))],
            "candidate_land_area_ha": areas,
            "candidate_land_fraction": [0.25, 0.5, 0.75, 0.8][: len(areas)],
            "artificial_focal_fraction": [0.0] * len(areas),
            "artificial_adjacent_fraction": [0.0] * len(areas),
            "artificial_local_fraction": [0.0] * len(areas),
            "boundary_edge_flag": [False] * len(areas),
        }
    )


def _candidates(areas_m2: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hex_id": [f"h_{i}_0" for i in range(len(areas_m2))],
            "candidate_area_m2": areas_m2,
        }
    )


def test_ordered_area_percentile_scoring() -> None:
    np.testing.assert_allclose(percentile_scores([5.0, 10.0, 20.0]), [0.0, 50.0, 100.0])


def test_minimum_maps_to_zero_and_maximum_maps_to_100() -> None:
    scores = percentile_scores([5.0, 10.0, 20.0])
    assert scores[0] == 0
    assert scores[-1] == 100


def test_average_rank_ties_and_equal_areas_have_equal_scores() -> None:
    np.testing.assert_allclose(percentile_scores([5.0, 10.0, 10.0, 20.0]), [0, 50, 50, 100])


def test_monotonicity_and_bounds() -> None:
    scores = percentile_scores([7.0, 6.0, 12.0, 9.0])
    assert np.all(np.diff(scores[np.argsort([7.0, 6.0, 12.0, 9.0])]) >= 0)
    assert np.all((scores >= 0) & (scores <= 100))


def test_row_order_does_not_change_id_score_mapping() -> None:
    raw = _raw([5.0, 10.0, 20.0, 20.0])
    first = calculate_scores(raw).set_index("hex_id")
    shuffled = calculate_scores(raw.sample(frac=1, random_state=3))
    second = shuffled.set_index("hex_id")
    pd.testing.assert_series_equal(
        first["restoration_land_availability_score"].sort_index(),
        second["restoration_land_availability_score"].sort_index(),
        check_names=True,
    )


@pytest.mark.parametrize("bad", [None, np.nan, np.inf, -1.0, 0.0])
def test_malformed_missing_nonfinite_and_nonpositive_area_rejected(bad: float | None) -> None:
    raw = _raw()
    raw.loc[0, "candidate_land_area_ha"] = bad
    with pytest.raises(RestorationLandAvailabilityError):
        validate_raw_indicators(raw)


def test_duplicate_ids_rejected() -> None:
    raw = _raw()
    raw.loc[1, "hex_id"] = raw.loc[0, "hex_id"]
    with pytest.raises(RestorationLandAvailabilityError):
        validate_raw_indicators(raw)


def test_candidate_raw_id_mismatch_rejected() -> None:
    raw = _raw([5.0, 10.0])
    candidates = _candidates([50_000.0, 100_000.0])
    candidates.loc[1, "hex_id"] = "h_extra_0"
    with pytest.raises(RestorationLandAvailabilityError):
        validate_candidate_reconciliation(raw, candidates)


def test_candidate_area_reconciles_from_square_metres() -> None:
    raw = _raw([5.0, 10.0])
    candidates = _candidates([50_000.0, 100_000.0])
    result = validate_candidate_area_reconciliation(raw, candidates)
    assert result["consistent_with_candidate_source"]


def test_candidate_area_reconciliation_rejects_mismatch() -> None:
    raw = _raw([5.01, 10.0])
    candidates = _candidates([50_000.0, 100_000.0])
    with pytest.raises(RestorationLandAvailabilityError):
        validate_candidate_area_reconciliation(raw, candidates)


def test_final_output_has_only_component_schema() -> None:
    result = calculate_scores(_raw())
    assert result.columns.tolist() == list(OUTPUT_COLUMNS)
    assert result["hex_id"].tolist() == ["h_0_0", "h_1_0", "h_2_0", "h_3_0"]
