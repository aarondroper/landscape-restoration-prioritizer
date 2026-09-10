import json

import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.prioritization_model import (
    BALANCED_WEIGHTS,
    BOUNDARY_FLAG,
    COMPONENTS,
    CONNECTIVITY_FIRST_WEIGHTS,
    FINAL_OUTPUT_COLUMNS,
    PRESET_DEFINITIONS,
    PRESET_SCORE_FIELDS,
    RIPARIAN_RESTORATION_WEIGHTS,
    SCORE_FIELDS,
    PrioritizationModelError,
    calculate_prioritization_scores,
    join_component_scores,
    preset_metadata,
    reconcile_boundary_flags,
    reconcile_component_ids,
    validate_weights,
    write_preset_metadata,
)


def _frame(values: list[list[float]], boundary: list[bool] | None = None) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "hex_id": [f"h_{index}" for index in range(len(values))],
            BOUNDARY_FLAG: boundary or [False] * len(values),
        }
    )
    for index, field in enumerate(SCORE_FIELDS):
        result[field] = [row[index] for row in values]
    return result


def _components(values: list[float], boundary: list[bool] | None = None):
    components = {}
    for label, config in COMPONENTS.items():
        components[label] = (
            _frame([[value] * len(SCORE_FIELDS) for value in values], boundary),
            config["score_field"],
        )
    return components


def test_canonical_final_weights_are_exact() -> None:
    assert list(BALANCED_WEIGHTS.values()) == [0.20] * 5
    assert list(CONNECTIVITY_FIRST_WEIGHTS.values()) == [0.20, 0.30, 0.10, 0.25, 0.15]
    assert list(RIPARIAN_RESTORATION_WEIGHTS.values()) == [0.20, 0.10, 0.35, 0.15, 0.20]


def test_every_canonical_vector_is_positive_and_sums_to_one() -> None:
    for config in PRESET_DEFINITIONS.values():
        validated = validate_weights(config["weights"])
        assert np.isclose(sum(validated.values()), 1.0)
        assert all(value > 0 for value in validated.values())


def test_exact_score_for_each_final_preset() -> None:
    result = calculate_prioritization_scores(_frame([[10, 20, 30, 40, 50]]))
    assert result.loc[0, PRESET_SCORE_FIELDS["balanced"]] == 30.0
    assert result.loc[0, PRESET_SCORE_FIELDS["connectivity_first"]] == 28.5
    assert result.loc[0, PRESET_SCORE_FIELDS["riparian_restoration"]] == 30.5


def test_thematic_synthetic_candidates_behave_as_expected() -> None:
    connectivity = calculate_prioritization_scores(_frame([[50, 100, 50, 100, 50]])).loc[0]
    assert (
        connectivity[PRESET_SCORE_FIELDS["connectivity_first"]]
        > connectivity[PRESET_SCORE_FIELDS["balanced"]]
    )
    riparian = calculate_prioritization_scores(_frame([[50, 50, 100, 50, 50]])).loc[0]
    assert (
        riparian[PRESET_SCORE_FIELDS["riparian_restoration"]]
        > riparian[PRESET_SCORE_FIELDS["balanced"]]
    )


def test_final_scores_remain_in_bounds() -> None:
    result = calculate_prioritization_scores(_frame([[0, 0, 0, 0, 0], [100, 100, 100, 100, 100]]))
    assert (
        result[list(PRESET_SCORE_FIELDS.values())]
        .apply(lambda column: column.between(0, 100))
        .all()
        .all()
    )


@pytest.mark.parametrize(
    "bad_frame",
    [
        _frame([[10, 20, 30, 40, 50]]).drop(columns=SCORE_FIELDS[0]),
        _frame([[10, 20, np.nan, 40, 50]]),
        _frame([[10, 20, 30, 40, 101]]),
    ],
)
def test_malformed_component_input_is_rejected(bad_frame: pd.DataFrame) -> None:
    with pytest.raises(PrioritizationModelError):
        calculate_prioritization_scores(bad_frame)


def test_candidate_reconciliation_is_exact() -> None:
    candidates = pd.DataFrame({"hex_id": ["h_0", "h_1"]})
    components = _components([10, 20])
    joined = join_component_scores(candidates, components)
    assert joined["hex_id"].tolist() == ["h_0", "h_1"]
    with pytest.raises(PrioritizationModelError):
        reconcile_component_ids(components["Habitat Context"][0], candidates.iloc[:1], "Habitat")


def test_boundary_reconciliation_is_required() -> None:
    first = _frame([[10, 10, 10, 10, 10], [20, 20, 20, 20, 20]], [False, False])
    second = first.copy()
    second.loc[1, BOUNDARY_FLAG] = True
    with pytest.raises(PrioritizationModelError):
        reconcile_boundary_flags({"first": first, "second": second})


def test_row_order_independence() -> None:
    frame = _frame([[10, 20, 30, 40, 50], [80, 70, 60, 50, 40], [25, 35, 45, 55, 65]])
    first = calculate_prioritization_scores(frame).set_index("hex_id").sort_index()
    shuffled = calculate_prioritization_scores(frame.sample(frac=1, random_state=19))
    shuffled = shuffled.set_index("hex_id").sort_index()
    pd.testing.assert_frame_equal(first, shuffled)


def test_canonical_output_contains_only_three_final_preset_scores() -> None:
    result = calculate_prioritization_scores(_frame([[10, 20, 30, 40, 50]]))
    output = result[[*FINAL_OUTPUT_COLUMNS[:-1], BOUNDARY_FLAG]]
    assert list(output.columns) == list(FINAL_OUTPUT_COLUMNS)
    assert not any(
        field in output.columns for field in ("connectivity_medium_score", "riparian_strong_score")
    )


def test_presets_metadata_is_generated_from_canonical_definitions(tmp_path) -> None:
    path = tmp_path / "presets.json"
    write_preset_metadata(path)
    assert json.loads(path.read_text()) == preset_metadata()
    assert set(json.loads(path.read_text())) == {
        "balanced",
        "connectivity_first",
        "riparian_restoration",
    }
