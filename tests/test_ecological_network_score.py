from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from restoration_prioritizer.ecological_network_score import (
    EcologicalNetworkScoreError,
    calculate_scores,
    direct_scale,
    validate_candidate_reconciliation,
    validate_raw_indicators,
)


def raw_frame(
    values: list[float],
    ids: list[str] | None = None,
    **overrides: list[float] | list[bool],
) -> pd.DataFrame:
    ids = ids or [f"h_{index}_0" for index in range(len(values))]
    diagnostic_values = (
        pd.to_numeric(pd.Series(values), errors="coerce")
        .clip(lower=0, upper=0.25)
        .fillna(0.25)
        .tolist()
    )
    frame = pd.DataFrame(
        {
            "hex_id": ids,
            "opposing_balance_ratio": values,
            "dominant_opposing_pair_share": diagnostic_values,
            "bridge_strength_max": [0.2] * len(values),
            "bridge_strength_mean": [0.1] * len(values),
            "neighbor_habitat_mean": [0.4] * len(values),
            "boundary_edge_flag": [False] * len(values),
        }
    )
    for field, replacement in overrides.items():
        frame[field] = replacement
    return frame


def candidate_frame(ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hex_id": ids,
            "candidate_fraction_of_terrestrial": [0.5] * len(ids),
            "candidate_area_m2": [50_000.0] * len(ids),
            "habitat_context_fraction_of_terrestrial": [0.2] * len(ids),
        }
    )


def test_direct_scaling_uses_the_fixed_ratio_scale() -> None:
    assert direct_scale([0, 0.25, 0.8, 1]).tolist() == pytest.approx([0, 25, 80, 100])


def test_direct_scaling_is_monotonic_and_bounded() -> None:
    scores = direct_scale([0.8, 0.0, 0.25, 1.0])
    assert np.all(np.diff(scores[np.argsort([0.8, 0.0, 0.25, 1.0])]) >= 0)
    assert np.all((scores >= 0) & (scores <= 100))


def test_calculated_scores_have_exact_linear_relationship() -> None:
    scored = calculate_scores(raw_frame([0.0, 0.25, 0.8, 1.0]))
    assert np.array_equal(
        scored["ecological_network_score"].to_numpy(),
        scored["opposing_balance_ratio"].to_numpy() * 100.0,
    )


def test_same_raw_value_is_not_affected_by_other_observations() -> None:
    assert direct_scale([0.5])[0] == direct_scale([0.1, 0.5, 0.9])[1] == 50


@pytest.mark.parametrize(
    "bad_values",
    [[0.1, None], [0.1, np.nan], [0.1, np.inf], [-0.1, 0.2], [0.2, 1.1]],
)
def test_malformed_missing_nonfinite_and_out_of_range_selected_input_is_rejected(
    bad_values: list[float],
) -> None:
    with pytest.raises(EcologicalNetworkScoreError):
        validate_raw_indicators(raw_frame(bad_values))


def test_missing_supporting_diagnostic_is_rejected() -> None:
    frame = raw_frame([0.1, 0.2]).drop(columns="dominant_opposing_pair_share")
    with pytest.raises(EcologicalNetworkScoreError, match="missing required fields"):
        validate_raw_indicators(frame)


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(EcologicalNetworkScoreError, match="unique"):
        validate_raw_indicators(raw_frame([0.1, 0.2], ids=["h_0_0", "h_0_0"]))


def test_candidate_and_raw_id_mismatch_is_rejected() -> None:
    raw = raw_frame([0.1, 0.2])
    candidates = candidate_frame(["h_0_0", "h_99_0"])
    with pytest.raises(EcologicalNetworkScoreError, match="do not reconcile"):
        validate_candidate_reconciliation(raw, candidates)


def test_out_of_range_supporting_diagnostic_and_invalid_boundary_are_rejected() -> None:
    with pytest.raises(EcologicalNetworkScoreError, match="bridge_strength_max"):
        validate_raw_indicators(raw_frame([0.1, 0.2], bridge_strength_max=[1.1, 0.2]))
    with pytest.raises(EcologicalNetworkScoreError, match="boundary_edge_flag"):
        validate_raw_indicators(raw_frame([0.1, 0.2], boundary_edge_flag=["true", "unknown"]))


def test_candidate_reconciliation_accepts_exact_ids() -> None:
    raw = raw_frame([0.1, 0.2])
    result = validate_candidate_reconciliation(raw, candidate_frame(raw.hex_id.tolist()))
    assert result["ids_reconcile_exactly"] is True
    assert result["missing_candidate_ids"] == 0
    assert result["extra_raw_ids"] == 0
