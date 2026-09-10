import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.preset_sensitivity import (
    BALANCED_WEIGHTS,
    SCHEMES,
    calculate_sensitivity_scores,
)
from restoration_prioritizer.prioritization_model import (
    SCORE_FIELDS,
    PrioritizationModelError,
    validate_component,
    validate_weights,
)


def _frame(rows: int = 3) -> pd.DataFrame:
    result = pd.DataFrame({"hex_id": [f"h_{index}" for index in range(rows)]})
    for field in SCORE_FIELDS:
        result[field] = np.linspace(10, 90, rows)
    return result


def test_all_fixed_vectors_sum_to_one_and_are_positive() -> None:
    vectors = {"balanced_reference": BALANCED_WEIGHTS}
    vectors.update({name: config["weights"] for name, config in SCHEMES.items()})
    for weights in vectors.values():
        validated = validate_weights(weights)
        assert sum(validated.values()) == 1.0
        assert all(value > 0 for value in validated.values())


def test_exact_weighted_mean_calculation() -> None:
    frame = _frame(1)
    frame.loc[0, SCORE_FIELDS] = [10, 20, 30, 40, 50]
    result = calculate_sensitivity_scores(frame)
    expected = sum(value * 0.20 for value in [10, 20, 30, 40, 50])
    assert result.loc[0, "balanced_reference_score"] == expected


def test_equal_component_scores_are_unchanged_by_every_scheme() -> None:
    frame = _frame(2)
    frame.loc[:, SCORE_FIELDS] = 37.5
    result = calculate_sensitivity_scores(frame)
    for field in result.columns:
        if field.endswith("_score"):
            np.testing.assert_allclose(result[field], 37.5)


def test_connectivity_emphasis_rewards_network_and_protection() -> None:
    frame = _frame(1)
    frame.loc[0, SCORE_FIELDS] = [50, 100, 50, 100, 50]
    result = calculate_sensitivity_scores(frame)
    assert result.loc[0, "connectivity_strong_score"] > result.loc[0, "balanced_reference_score"]


def test_riparian_emphasis_rewards_riparian_score() -> None:
    frame = _frame(1)
    frame.loc[0, SCORE_FIELDS] = [50, 50, 100, 50, 50]
    result = calculate_sensitivity_scores(frame)
    assert result.loc[0, "riparian_strong_score"] > result.loc[0, "balanced_reference_score"]


@pytest.mark.parametrize(
    "weights",
    [
        {**BALANCED_WEIGHTS, SCORE_FIELDS[0]: -0.1},
        {field: value for field, value in list(BALANCED_WEIGHTS.items())[:-1]},
        {**BALANCED_WEIGHTS, SCORE_FIELDS[0]: 0.3},
        {**BALANCED_WEIGHTS, SCORE_FIELDS[0]: np.nan},
    ],
)
def test_invalid_weight_vectors_are_rejected(weights: dict[str, float]) -> None:
    with pytest.raises(PrioritizationModelError):
        validate_weights(weights)


def test_component_input_validation_remains_enforced() -> None:
    frame = pd.DataFrame({"hex_id": ["h_0"], "score": [101.0]})
    with pytest.raises(PrioritizationModelError):
        validate_component(frame, "score", "Synthetic", expected_count=None)


def test_sensitivity_calculation_is_row_order_independent() -> None:
    frame = _frame(5)
    first = calculate_sensitivity_scores(frame).set_index("hex_id").sort_index()
    shuffled = (
        calculate_sensitivity_scores(frame.sample(frac=1, random_state=7))
        .set_index("hex_id")
        .sort_index()
    )
    pd.testing.assert_frame_equal(first, shuffled)


def test_one_sensitivity_row_per_candidate() -> None:
    frame = _frame(11)
    result = calculate_sensitivity_scores(frame)
    assert len(result) == len(frame)
    assert result.hex_id.is_unique
    assert set(result.hex_id) == set(frame.hex_id)
