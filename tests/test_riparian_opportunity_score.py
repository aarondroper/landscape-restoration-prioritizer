import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.riparian_opportunity_score import (
    RiparianOpportunityScoreError,
    calculate_scores,
    positive_percentile_scores,
    validate_candidate_reconciliation,
    validate_raw_indicators,
)


def raw_frame(values: list[float], ids: list[str] | None = None) -> pd.DataFrame:
    identifiers = ids or [f"h_{index}" for index in range(len(values))]
    return pd.DataFrame(
        {
            "hex_id": identifiers,
            "riparian_focal_fraction": values,
            "riparian_adjacent_fraction": [0.1] * len(values),
            "riparian_near_fraction": [0.1] * len(values),
            "riparian_local_fraction": [0.1] * len(values),
            "focal_wetland_fraction": values,
            "focal_inland_water_fraction": [0.0] * len(values),
            "boundary_edge_flag": [False] * len(values),
        }
    )


def candidate_frame(ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hex_id": ids,
            "candidate_fraction_of_terrestrial": [0.5] * len(ids),
            "candidate_area_m2": [50_000.0] * len(ids),
            "habitat_context_fraction_of_terrestrial": [0.2] * len(ids),
        }
    )


def test_zero_raw_values_score_zero() -> None:
    assert positive_percentile_scores([0.0, 0.0, 0.2]).tolist() == [0.0, 0.0, 100.0]


def test_positive_values_score_strictly_positive() -> None:
    scores = positive_percentile_scores([0.0, 0.1, 0.2])
    assert scores[0] == 0
    assert (scores[1:] > 0).all()


def test_maximum_positive_value_scores_100() -> None:
    assert positive_percentile_scores([0.0, 0.2, 0.4])[-1] == 100


def test_simple_positive_ordered_vector_has_expected_percentiles() -> None:
    assert positive_percentile_scores([0.1, 0.2, 0.3, 0.4]).tolist() == [25, 50, 75, 100]


def test_zeros_are_excluded_from_positive_ranking() -> None:
    assert positive_percentile_scores([0.0, 0.0, 0.1, 0.2]).tolist() == [0, 0, 50, 100]


def test_positive_ties_use_average_rank() -> None:
    assert positive_percentile_scores([0.1, 0.1, 0.2]).tolist() == [50, 50, 100]


def test_monotonicity_and_bounds() -> None:
    values = np.array([0.0, 0.02, 0.01, 0.8, 0.8])
    scores = positive_percentile_scores(values)
    assert np.all((scores >= 0) & (scores <= 100))
    assert np.all(np.diff(scores[np.argsort(values, kind="stable")]) >= 0)


def test_order_independence() -> None:
    first = calculate_scores(raw_frame([0.0, 0.1, 0.1, 0.4])).set_index("hex_id")
    second = calculate_scores(
        raw_frame([0.4, 0.1, 0.0, 0.1], ids=["h_3", "h_1", "h_0", "h_2"])
    ).set_index("hex_id")
    pd.testing.assert_series_equal(
        first["riparian_opportunity_score"].sort_index(),
        second["riparian_opportunity_score"].sort_index(),
    )


@pytest.mark.parametrize(
    "values",
    [[0.0, np.nan], [0.0, np.inf], [0.0, -0.1], [0.0, 1.1]],
)
def test_malformed_nonfinite_or_out_of_range_input_rejected(values: list[float]) -> None:
    with pytest.raises(RiparianOpportunityScoreError):
        positive_percentile_scores(values)


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(RiparianOpportunityScoreError, match="unique"):
        validate_raw_indicators(raw_frame([0.1, 0.2], ids=["same", "same"]))


def test_candidate_raw_id_mismatch_rejected() -> None:
    raw = raw_frame([0.1, 0.2], ids=["h_0", "h_1"])
    candidates = candidate_frame(["h_0", "h_other"])
    with pytest.raises(RiparianOpportunityScoreError, match="reconcile exactly"):
        validate_candidate_reconciliation(raw, candidates)
