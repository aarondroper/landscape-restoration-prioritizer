import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.protected_area_reinforcement_score import (
    ProtectedAreaReinforcementScoreError,
    calculate_scores,
    reverse_empirical_scores,
    validate_candidate_reconciliation,
    validate_raw_indicators,
)


def _raw(steps, ids=None):
    steps = list(steps)
    ids = ids or [f"h_{index}_0" for index in range(len(steps))]
    return pd.DataFrame(
        {
            "hex_id": ids,
            "nearest_protected_hex_steps": steps,
            "nearest_protected_nominal_m": [step * 500 for step in steps],
            "protected_focal_fraction": [0.2 if step == 0 else 0.0 for step in steps],
            "protected_adjacent_fraction": [0.1] * len(steps),
            "protected_local_fraction": [0.2] * len(steps),
            "boundary_edge_flag": [False] * len(steps),
        }
    )


def test_step_zero_scores_100_and_positive_distances_stay_below_100():
    result = calculate_scores(_raw([0, 1, 2, 3]))
    assert (
        result.loc[
            result["nearest_protected_hex_steps"] == 0, "protected_area_reinforcement_score"
        ].iat[0]
        == 100
    )
    assert (
        result.loc[result["nearest_protected_hex_steps"] > 0, "protected_area_reinforcement_score"]
        < 100
    ).all()


def test_synthetic_positive_distances_follow_approved_formula():
    scores = reverse_empirical_scores([1, 2, 3, 4])
    np.testing.assert_allclose(scores, [80, 60, 40, 20])


def test_nearer_positive_distance_scores_higher_and_farthest_remains_positive():
    scores = reverse_empirical_scores([1, 2, 3, 4])
    assert np.all(np.diff(scores) < 0)
    assert scores[-1] > 0


def test_equal_steps_receive_equal_average_rank_scores():
    scores = reverse_empirical_scores([1, 1, 2, 3])
    assert scores[0] == scores[1]
    assert scores[0] > scores[2] > scores[3]


def test_score_is_order_independent_and_output_is_deterministically_sorted():
    first = calculate_scores(_raw([0, 1, 1, 3], ids=["h_3", "h_1", "h_2", "h_0"]))
    second = calculate_scores(_raw([1, 3, 1, 0], ids=["h_1", "h_0", "h_2", "h_3"]))
    first = first.sort_values("hex_id").reset_index(drop=True)
    second = second.sort_values("hex_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(first, second)
    assert first["hex_id"].tolist() == ["h_0", "h_1", "h_2", "h_3"]


@pytest.mark.parametrize(
    "bad_steps",
    [[1.25, 2], [-1, 2], [1, np.nan], [1, np.inf]],
)
def test_malformed_distances_are_rejected(bad_steps):
    with pytest.raises(ProtectedAreaReinforcementScoreError):
        validate_raw_indicators(_raw(bad_steps))


def test_missing_required_and_inconsistent_nominal_distance_are_rejected():
    missing = _raw([0, 1]).drop(columns="nearest_protected_hex_steps")
    with pytest.raises(ProtectedAreaReinforcementScoreError):
        validate_raw_indicators(missing)

    inconsistent = _raw([0, 1])
    inconsistent.loc[1, "nearest_protected_nominal_m"] = 501
    with pytest.raises(ProtectedAreaReinforcementScoreError):
        validate_raw_indicators(inconsistent)


def test_duplicate_ids_are_rejected():
    duplicate = _raw([0, 1], ids=["same", "same"])
    with pytest.raises(ProtectedAreaReinforcementScoreError):
        validate_raw_indicators(duplicate)


def test_candidate_raw_id_mismatch_is_rejected():
    raw = _raw([0, 1])
    candidates = pd.DataFrame({"hex_id": ["h_0_0", "other"]})
    with pytest.raises(ProtectedAreaReinforcementScoreError):
        validate_candidate_reconciliation(raw, candidates)


def test_positive_population_singleton_fails_clearly():
    with pytest.raises(ProtectedAreaReinforcementScoreError, match="two positive-distance"):
        reverse_empirical_scores([0, 1])
