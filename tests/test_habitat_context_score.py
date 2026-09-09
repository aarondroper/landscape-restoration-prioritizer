from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.habitat_context_score import (
    HabitatContextScoreError,
    calculate_scores,
    percentile_scores,
    validate_candidate_reconciliation,
    validate_raw_indicators,
)


def raw_frame(values: list[float], ids: list[str] | None = None) -> pd.DataFrame:
    ids = ids or [f"h_{index}_0" for index in range(len(values))]
    return pd.DataFrame({"hex_id": ids, "habitat_context_local_fraction": values})


def candidate_frame(ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hex_id": ids,
            "candidate_fraction_of_terrestrial": [0.5] * len(ids),
            "candidate_area_m2": [50_000.0] * len(ids),
            "habitat_context_fraction_of_terrestrial": [0.2] * len(ids),
        }
    )


def test_percentile_formula_on_ordered_vector() -> None:
    assert percentile_scores([0.1, 0.2, 0.3, 0.4]).tolist() == pytest.approx(
        [0, 100 / 3, 200 / 3, 100]
    )


def test_lowest_is_zero_highest_is_100_and_scores_are_bounded() -> None:
    scores = percentile_scores([0.4, 0.1, 0.3, 0.2])
    assert scores.min() == 0
    assert scores.max() == 100
    assert np.all((scores >= 0) & (scores <= 100))


def test_average_rank_ties_and_equal_raw_values_receive_equal_scores() -> None:
    scores = percentile_scores([0.1, 0.2, 0.2, 0.4])
    assert scores.tolist() == pytest.approx([0, 50, 50, 100])
    scored = calculate_scores(raw_frame([0.2, 0.1, 0.2, 0.4]))
    assert (
        scored.loc[scored.habitat_context_local_fraction == 0.2, "habitat_context_score"].nunique()
        == 1
    )


def test_score_order_is_monotonic() -> None:
    scored = calculate_scores(raw_frame([0.8, 0.1, 0.5, 0.5]))
    ordered = scored.sort_values("habitat_context_local_fraction")
    assert ordered.habitat_context_score.tolist() == sorted(ordered.habitat_context_score.tolist())


@pytest.mark.parametrize(
    "bad_values",
    [[0.1, None], [0.1, np.nan], [0.1, np.inf], [-0.1, 0.2], [0.2, 1.1]],
)
def test_malformed_missing_nonfinite_and_out_of_range_input_is_rejected(
    bad_values: list[float],
) -> None:
    with pytest.raises(HabitatContextScoreError):
        validate_raw_indicators(raw_frame(bad_values))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(HabitatContextScoreError, match="unique"):
        validate_raw_indicators(raw_frame([0.1, 0.2], ids=["h_0_0", "h_0_0"]))


def test_missing_required_scoring_field_is_rejected() -> None:
    with pytest.raises(HabitatContextScoreError, match="missing required fields"):
        validate_raw_indicators(pd.DataFrame({"hex_id": ["h_0_0", "h_1_0"]}))


def test_candidate_and_raw_id_mismatch_is_rejected() -> None:
    raw = raw_frame([0.1, 0.2])
    candidates = candidate_frame(["h_0_0", "h_99_0"])
    with pytest.raises(HabitatContextScoreError, match="do not reconcile"):
        validate_candidate_reconciliation(raw, candidates)


def test_candidate_reconciliation_accepts_exact_ids() -> None:
    raw = raw_frame([0.1, 0.2])
    candidates = candidate_frame(raw.hex_id.tolist())
    result = validate_candidate_reconciliation(raw, candidates)
    assert result["ids_reconcile_exactly"] is True
    assert result["missing_raw_ids_count"] == 0
    assert result["extra_raw_ids_count"] == 0
