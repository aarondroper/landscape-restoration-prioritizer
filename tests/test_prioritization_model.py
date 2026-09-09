import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.prioritization_model import (
    BALANCED_SCORE,
    BOUNDARY_FLAG,
    COMPONENTS,
    SCORE_FIELDS,
    PrioritizationModelError,
    calculate_baseline,
    join_component_scores,
    reconcile_boundary_flags,
    reconcile_component_ids,
    validate_component,
    weighted_mean,
)


def _scores(values: list[float], *, boundary: list[bool] | None = None) -> pd.DataFrame:
    boundary = boundary or [False] * len(values)
    result = pd.DataFrame({"hex_id": [f"h_{index}" for index in range(len(values))]})
    for field in SCORE_FIELDS:
        result[field] = values
    result[BOUNDARY_FLAG] = boundary
    return result


def _components(
    values: list[float], *, boundary: list[bool] | None = None
) -> dict[str, tuple[pd.DataFrame, str]]:
    return {
        label: (_scores(values, boundary=boundary), config["score_field"])
        for label, config in COMPONENTS.items()
    }


def test_five_way_equal_arithmetic_mean() -> None:
    frame = _scores([10, 20, 30])
    frame.loc[:, SCORE_FIELDS[0]] = [0, 50, 100]
    frame.loc[:, SCORE_FIELDS[1]] = [10, 20, 30]
    frame.loc[:, SCORE_FIELDS[2]] = [20, 30, 40]
    frame.loc[:, SCORE_FIELDS[3]] = [30, 40, 50]
    frame.loc[:, SCORE_FIELDS[4]] = [40, 50, 60]
    result = calculate_baseline(frame)
    np.testing.assert_allclose(result[BALANCED_SCORE], [20, 38, 56])


def test_weights_sum_to_one() -> None:
    assert sum({field: 0.20 for field in SCORE_FIELDS}.values()) == 1.0
    assert weighted_mean(_scores([10, 20])).tolist() == [10.0, 20.0]


def test_all_zero_and_all_100_are_preserved() -> None:
    assert calculate_baseline(_scores([0, 0]))[BALANCED_SCORE].tolist() == [0.0, 0.0]
    assert calculate_baseline(_scores([100, 100]))[BALANCED_SCORE].tolist() == [100.0, 100.0]


def test_contribution_sum_equals_balanced_score() -> None:
    result = calculate_baseline(_scores([10, 90]))
    contribution_columns = [column for column in result if column.endswith("_contribution")]
    np.testing.assert_allclose(result[contribution_columns].sum(axis=1), result[BALANCED_SCORE])


def test_score_remains_in_bounds() -> None:
    result = calculate_baseline(_scores([0, 50, 100]))
    assert result[BALANCED_SCORE].between(0, 100).all()


def test_missing_component_score_rejected() -> None:
    frame = _scores([10, 20]).drop(columns=SCORE_FIELDS[2])
    with pytest.raises(PrioritizationModelError):
        calculate_baseline(frame)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -0.1, 100.1])
def test_nonfinite_or_out_of_range_component_score_rejected(bad: float) -> None:
    frame = pd.DataFrame({"hex_id": ["h_0"], "score": [bad]})
    with pytest.raises(PrioritizationModelError):
        validate_component(frame, "score", "Synthetic", expected_count=None)


def test_duplicate_ids_rejected() -> None:
    frame = _scores([10, 20])
    frame.loc[1, "hex_id"] = frame.loc[0, "hex_id"]
    with pytest.raises(PrioritizationModelError):
        validate_component(frame, SCORE_FIELDS[0], "Synthetic", expected_count=None)


def test_component_candidate_id_mismatch_rejected() -> None:
    component = _scores([10, 20])
    candidates = pd.DataFrame({"hex_id": ["h_0", "h_extra"]})
    with pytest.raises(PrioritizationModelError):
        reconcile_component_ids(component, candidates, "Synthetic")


def test_boundary_flag_mismatch_rejected() -> None:
    first = _scores([10, 20], boundary=[False, False])
    second = _scores([10, 20], boundary=[False, True])
    with pytest.raises(PrioritizationModelError):
        reconcile_boundary_flags({"first": first, "second": second})


def test_row_order_independence() -> None:
    frame = _scores([10, 20, 30])
    first = calculate_baseline(frame).set_index("hex_id")[BALANCED_SCORE]
    shuffled = calculate_baseline(frame.sample(frac=1, random_state=11)).set_index("hex_id")[
        BALANCED_SCORE
    ]
    pd.testing.assert_series_equal(first.sort_index(), shuffled.sort_index())


def test_join_result_has_one_row_per_candidate() -> None:
    candidates = pd.DataFrame({"hex_id": ["h_0", "h_1", "h_2"]})
    components = _components([10, 20, 30])
    joined = join_component_scores(candidates, components)
    assert len(joined) == len(candidates)
    assert joined["hex_id"].is_unique
    assert set(SCORE_FIELDS).issubset(joined.columns)
