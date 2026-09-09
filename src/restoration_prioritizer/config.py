"""Stable project and analytical-domain constants.

This module intentionally does not contain indicator formulas, source-specific
settings, or scoring weights. Those decisions depend on validated data sources
and belong to later implementation steps.
"""

from dataclasses import dataclass

STUDY_AREA_NAME = "Skåne län, Sweden"
COUNTY_CODE = "12"
NUTS3_CODE = "SE224"

# SWEREF 99 TM is the canonical metre-based processing CRS for Swedish data.
TARGET_CRS = "EPSG:3006"

NOMINAL_HEX_SIZE_METRES = 500

COMPONENT_IDS = (
    "habitat_context",
    "ecological_network_context",
    "riparian_opportunity",
    "protected_area_reinforcement",
    "land_restoration_feasibility",
)

PRESET_IDS = (
    "balanced",
    "connectivity_first",
    "riparian_restoration",
)


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Stable project constants shared by analytical and delivery code."""

    study_area_name: str = STUDY_AREA_NAME
    county_code: str = COUNTY_CODE
    nuts3_code: str = NUTS3_CODE
    target_crs: str = TARGET_CRS
    nominal_hex_size_metres: int = NOMINAL_HEX_SIZE_METRES
    component_ids: tuple[str, ...] = COMPONENT_IDS
    preset_ids: tuple[str, ...] = PRESET_IDS


PROJECT_CONFIG = ProjectConfig()
