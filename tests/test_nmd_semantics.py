import numpy as np
import pytest

from restoration_prioritizer.nmd_semantics import (
    ANALYTICAL_ROLES,
    ARABLE,
    ARTIFICIAL_CONSTRAINT,
    EXPECTED_OBSERVED_CODES,
    FACTUAL_GROUP_CODES,
    HABITAT_CONTEXT_PROXY,
    INLAND_WATER_CONTEXT,
    NO_DATA,
    PEAT_EXTRACTION,
    PRIMARY_CANDIDATE,
    SEA,
    SemanticValidationError,
    TERRESTRIAL_LAND,
    TRANSITIONAL_FOREST,
    WETLAND_CONTEXT,
    analytical_role_masks,
    analytical_roles_for_code,
    factual_group_for_code,
    factual_group_masks,
    validate_observed_codes,
)


def test_expected_codes_are_covered_by_exactly_one_factual_group() -> None:
    assert set(validate_observed_codes(EXPECTED_OBSERVED_CODES)) == EXPECTED_OBSERVED_CODES
    assert set().union(*FACTUAL_GROUP_CODES.values()) == EXPECTED_OBSERVED_CODES
    assert all(
        sum(code in group_codes for group_codes in FACTUAL_GROUP_CODES.values()) == 1
        for code in EXPECTED_OBSERVED_CODES
    )


def test_unknown_code_fails_explicitly() -> None:
    with pytest.raises(SemanticValidationError, match=r"unknown_codes=\[9999\]"):
        validate_observed_codes([3, 9999])


def test_candidate_and_context_role_membership() -> None:
    assert factual_group_for_code(3) == ARABLE
    assert PRIMARY_CANDIDATE in analytical_roles_for_code(3)
    assert HABITAT_CONTEXT_PROXY not in analytical_roles_for_code(3)
    assert WETLAND_CONTEXT not in analytical_roles_for_code(3)
    assert INLAND_WATER_CONTEXT not in analytical_roles_for_code(3)
    assert ARTIFICIAL_CONSTRAINT not in analytical_roles_for_code(3)

    assert HABITAT_CONTEXT_PROXY in analytical_roles_for_code(111)
    assert HABITAT_CONTEXT_PROXY in analytical_roles_for_code(121)
    assert WETLAND_CONTEXT in analytical_roles_for_code(121)
    assert WETLAND_CONTEXT in analytical_roles_for_code(128)
    assert TRANSITIONAL_FOREST in analytical_roles_for_code(128)
    assert HABITAT_CONTEXT_PROXY not in analytical_roles_for_code(128)


def test_water_artificial_peat_and_terrestrial_role_membership() -> None:
    assert INLAND_WATER_CONTEXT in analytical_roles_for_code(61)
    assert TERRESTRIAL_LAND not in analytical_roles_for_code(61)
    assert factual_group_for_code(62) == SEA
    assert TERRESTRIAL_LAND not in analytical_roles_for_code(62)
    assert ARTIFICIAL_CONSTRAINT in analytical_roles_for_code(51)
    assert ARTIFICIAL_CONSTRAINT in analytical_roles_for_code(52)
    assert ARTIFICIAL_CONSTRAINT in analytical_roles_for_code(53)
    assert factual_group_for_code(54) == PEAT_EXTRACTION
    assert PEAT_EXTRACTION not in analytical_roles_for_code(54)
    assert PRIMARY_CANDIDATE not in analytical_roles_for_code(54)


def test_mask_functions_preserve_shape_and_roles_can_overlap() -> None:
    codes = np.array([[0, 3, 111, 121], [128, 61, 62, 51]], dtype=np.uint16)
    factual = factual_group_masks(codes)
    roles = analytical_role_masks(codes)

    assert set(factual) == set(FACTUAL_GROUP_CODES)
    assert set(roles) == set(ANALYTICAL_ROLES)
    assert all(mask.shape == codes.shape for mask in factual.values())
    assert all(mask.shape == codes.shape for mask in roles.values())
    assert factual[NO_DATA][0, 0]
    assert roles[PRIMARY_CANDIDATE][0, 1]
    assert roles[HABITAT_CONTEXT_PROXY][0, 2]
    assert roles[HABITAT_CONTEXT_PROXY][0, 3]
    assert roles[WETLAND_CONTEXT][0, 3]
    assert roles[WETLAND_CONTEXT][1, 0]
    assert roles[TRANSITIONAL_FOREST][1, 0]
    assert roles[INLAND_WATER_CONTEXT][1, 1]
    assert not roles[TERRESTRIAL_LAND][1, 1]
    assert not roles[TERRESTRIAL_LAND][1, 2]
