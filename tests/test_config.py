from restoration_prioritizer.config import (
    COUNTY_CODE,
    COMPONENT_IDS,
    NOMINAL_HEX_SIZE_METRES,
    NUTS3_CODE,
    PRESET_IDS,
    PROJECT_CONFIG,
)


def test_configuration_exposes_five_unique_components() -> None:
    assert len(COMPONENT_IDS) == 5
    assert len(set(COMPONENT_IDS)) == len(COMPONENT_IDS)
    assert PROJECT_CONFIG.component_ids == COMPONENT_IDS


def test_configuration_exposes_planned_presets() -> None:
    assert PRESET_IDS == ("balanced", "connectivity_first", "riparian_restoration")
    assert PROJECT_CONFIG.preset_ids == PRESET_IDS


def test_nominal_hex_size_is_positive_and_canonical_crs_is_projected() -> None:
    assert NOMINAL_HEX_SIZE_METRES > 0
    assert PROJECT_CONFIG.target_crs == "EPSG:3006"


def test_study_area_codes_are_explicit() -> None:
    assert COUNTY_CODE == "12"
    assert NUTS3_CODE == "SE224"
    assert PROJECT_CONFIG.county_code == COUNTY_CODE
    assert PROJECT_CONFIG.nuts3_code == NUTS3_CODE
