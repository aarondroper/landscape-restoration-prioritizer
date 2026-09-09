"""The NMD2023 v2.1 semantic contract used by the MVP.

NMD classes are land-cover observations. The factual groups below keep their
mutually exclusive meaning separate from the overlapping analytical roles
used by later spatial indicators. No role in this module is a suitability,
quality, condition, ownership, cost, or feasibility assessment.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import transform as window_transform
from shapely import union_all

NMD_RASTER_PATH = Path("data/processed/nmd/nmd2023_v2_1_skane.tif")
NMD_STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
NMD_STUDY_AREA_LAYER = "study_area"
NMD_SEMANTIC_AUDIT_PATH = Path("data/processed/nmd/nmd2023_v2_1_semantic_audit.json")
PIXEL_AREA_KM2 = 10 * 10 / 1_000_000

# These are the codes observed in the processed Skåne raster during Step 4.
# They are deliberately explicit: an unobserved or newly introduced code must
# fail the audit until its semantic interpretation is reviewed.
EXPECTED_OBSERVED_CODES = frozenset(
    {
        0,
        3,
        51,
        52,
        53,
        54,
        61,
        62,
        111,
        112,
        113,
        114,
        115,
        116,
        117,
        118,
        121,
        122,
        123,
        124,
        125,
        126,
        127,
        128,
        200,
        211,
        212,
        213,
        214,
        215,
        216,
        217,
        218,
        221,
        222,
        223,
        224,
        225,
        226,
        227,
        228,
        411,
        4211,
        4212,
        4213,
        4221,
        4222,
        4223,
        4231,
        4232,
        4233,
    }
)

NO_DATA = "no_data"
ARABLE = "arable"
ARTIFICIAL_BUILDING = "artificial_building"
ARTIFICIAL_OTHER = "artificial_other"
ARTIFICIAL_TRANSPORT = "artificial_transport"
PEAT_EXTRACTION = "peat_extraction"
INLAND_WATER = "inland_water"
SEA = "sea"
ESTABLISHED_FOREST_FIRM_GROUND = "established_forest_firm_ground"
TRANSITIONAL_FOREST_FIRM_GROUND = "transitional_forest_firm_ground"
ESTABLISHED_FOREST_WETLAND = "established_forest_wetland"
TRANSITIONAL_FOREST_WETLAND = "transitional_forest_wetland"
OPEN_WETLAND = "open_wetland"
OPEN_NONVEGETATED = "open_nonvegetated"
OPEN_VEGETATED = "open_vegetated"

FACTUAL_GROUPS = (
    NO_DATA,
    ARABLE,
    ARTIFICIAL_BUILDING,
    ARTIFICIAL_OTHER,
    ARTIFICIAL_TRANSPORT,
    PEAT_EXTRACTION,
    INLAND_WATER,
    SEA,
    ESTABLISHED_FOREST_FIRM_GROUND,
    TRANSITIONAL_FOREST_FIRM_GROUND,
    ESTABLISHED_FOREST_WETLAND,
    TRANSITIONAL_FOREST_WETLAND,
    OPEN_WETLAND,
    OPEN_NONVEGETATED,
    OPEN_VEGETATED,
)

# The group definitions are the core factual contract. Each observed class is
# present exactly once, even when several groups share a broad land-cover theme.
FACTUAL_GROUP_CODES: dict[str, frozenset[int]] = {
    NO_DATA: frozenset({0}),
    ARABLE: frozenset({3}),
    ARTIFICIAL_BUILDING: frozenset({51}),
    ARTIFICIAL_OTHER: frozenset({52}),
    ARTIFICIAL_TRANSPORT: frozenset({53}),
    PEAT_EXTRACTION: frozenset({54}),
    INLAND_WATER: frozenset({61}),
    SEA: frozenset({62}),
    ESTABLISHED_FOREST_FIRM_GROUND: frozenset({111, 112, 113, 114, 115, 116, 117}),
    TRANSITIONAL_FOREST_FIRM_GROUND: frozenset({118}),
    ESTABLISHED_FOREST_WETLAND: frozenset({121, 122, 123, 124, 125, 126, 127}),
    TRANSITIONAL_FOREST_WETLAND: frozenset({128}),
    OPEN_WETLAND: frozenset(
        {200, 211, 212, 213, 214, 215, 216, 217, 218, 221, 222, 223, 224, 225, 226, 227, 228}
    ),
    OPEN_NONVEGETATED: frozenset({411}),
    OPEN_VEGETATED: frozenset({4211, 4212, 4213, 4221, 4222, 4223, 4231, 4232, 4233}),
}

# These names are the supplied NMD VAT legend names recorded during Step 4.
# The official code-0 VAT name is blank; Step 4 separately verified it as the
# no-data entry, so it remains None rather than receiving an invented label.
NMD_CLASS_NAMES: dict[int, str | None] = {
    0: None,
    3: "Åkermark",
    51: "Byggnad",
    52: "Anlagd mark, ej byggnad eller väg/järnväg",
    53: "Väg eller järnväg",
    54: "Torvtäkt",
    61: "Inlandsvatten",
    62: "Hav",
    111: "Tallskog på fastmark",
    112: "Granskog på fastmark",
    113: "Barrblandskog på fastmark",
    114: "Lövblandad barrskog på fastmark",
    115: "Triviallövskog på fastmark",
    116: "Ädellövskog på fastmark",
    117: "Triviallövskog med ädellövinslag på fastmark",
    118: "Temporärt ej skog på fastmark",
    121: "Tallskog på våtmark",
    122: "Granskog på våtmark",
    123: "Barrblandskog på våtmark",
    124: "Lövblandad barrskog på våtmark",
    125: "Triviallövskog på våtmark",
    126: "Ädellövskog på våtmark",
    127: "Triviallövskog med ädellövinslag på våtmark",
    128: "Temporärt ej skog på våtmark",
    200: "Öppen våtmark (underindelning saknas)",
    211: "Buskmyr",
    212: "Ristuvemyr",
    213: "Fastmattemyr, mager",
    214: "Fastmattemyr, frodig",
    215: "Sumpkärr",
    216: "Mjukmattemyr",
    217: "Lösbottenmyr",
    218: "Övrig öppen myr",
    221: "Våtmark med buskar",
    222: "Risdominerad våtmark",
    223: "Gräsdominerad våtmark, mager",
    224: "Gräsdominerad våtmark, frodvuxen",
    225: "Gräsdominerad våtmark, högvuxen",
    226: "Mossdominerad våtmark",
    227: "Våtmark utan växttäcke",
    228: "Övrig öppen våtmark",
    411: "Öppen fastmark utan vegetation (ej glaciär eller varaktigt snöfält)",
    4211: "Torr buskdominerad mark",
    4212: "Frisk buskdominerad mark",
    4213: "Frisk-fuktig buskdominerad mark",
    4221: "Torr risdominerad mark",
    4222: "Frisk risdominerad mark",
    4223: "Frisk-fuktig risdominerad mark",
    4231: "Torr gräsdominerad mark",
    4232: "Frisk gräsdominerad mark",
    4233: "Frisk-fuktig gräsdominerad mark",
}

PRIMARY_CANDIDATE = "primary_candidate"
HABITAT_CONTEXT_PROXY = "habitat_context_proxy"
WETLAND_CONTEXT = "wetland_context"
INLAND_WATER_CONTEXT = "inland_water_context"
ARTIFICIAL_CONSTRAINT = "artificial_constraint"
TRANSITIONAL_FOREST = "transitional_forest"
TERRESTRIAL_LAND = "terrestrial_land"

ANALYTICAL_ROLES = (
    PRIMARY_CANDIDATE,
    HABITAT_CONTEXT_PROXY,
    WETLAND_CONTEXT,
    INLAND_WATER_CONTEXT,
    ARTIFICIAL_CONSTRAINT,
    TRANSITIONAL_FOREST,
    TERRESTRIAL_LAND,
)

ROLE_FACTUAL_GROUPS: dict[str, tuple[str, ...]] = {
    PRIMARY_CANDIDATE: (ARABLE,),
    HABITAT_CONTEXT_PROXY: (
        ESTABLISHED_FOREST_FIRM_GROUND,
        ESTABLISHED_FOREST_WETLAND,
        OPEN_WETLAND,
        OPEN_VEGETATED,
    ),
    WETLAND_CONTEXT: (
        ESTABLISHED_FOREST_WETLAND,
        TRANSITIONAL_FOREST_WETLAND,
        OPEN_WETLAND,
    ),
    INLAND_WATER_CONTEXT: (INLAND_WATER,),
    ARTIFICIAL_CONSTRAINT: (
        ARTIFICIAL_BUILDING,
        ARTIFICIAL_OTHER,
        ARTIFICIAL_TRANSPORT,
    ),
    TRANSITIONAL_FOREST: (
        TRANSITIONAL_FOREST_FIRM_GROUND,
        TRANSITIONAL_FOREST_WETLAND,
    ),
    TERRESTRIAL_LAND: tuple(
        group for group in FACTUAL_GROUPS if group not in {NO_DATA, INLAND_WATER, SEA}
    ),
}


class SemanticValidationError(ValueError):
    """Raised when an observed NMD code is outside the explicit contract."""


def validate_contract_definition() -> None:
    """Validate the source-controlled code/group/name contract itself."""

    if tuple(FACTUAL_GROUP_CODES) != FACTUAL_GROUPS:
        raise SemanticValidationError("Factual group order and definitions disagree")
    occurrences: Counter[int] = Counter(
        code for codes in FACTUAL_GROUP_CODES.values() for code in codes
    )
    duplicate_codes = sorted(code for code, count in occurrences.items() if count != 1)
    if duplicate_codes:
        raise SemanticValidationError(
            f"Factual groups must be mutually exclusive; duplicate codes: {duplicate_codes}"
        )
    grouped_codes = frozenset(occurrences)
    if grouped_codes != EXPECTED_OBSERVED_CODES:
        raise SemanticValidationError(
            "Factual groups do not exactly cover expected observed codes: "
            f"missing={sorted(EXPECTED_OBSERVED_CODES - grouped_codes)}, "
            f"extra={sorted(grouped_codes - EXPECTED_OBSERVED_CODES)}"
        )
    if set(NMD_CLASS_NAMES) != EXPECTED_OBSERVED_CODES:
        raise SemanticValidationError("Official class-name mapping does not cover expected codes")
    role_names = set(ROLE_FACTUAL_GROUPS)
    if role_names != set(ANALYTICAL_ROLES):
        raise SemanticValidationError("Analytical role names and definitions disagree")
    unknown_role_groups = {
        group
        for groups in ROLE_FACTUAL_GROUPS.values()
        for group in groups
        if group not in FACTUAL_GROUP_CODES
    }
    if unknown_role_groups:
        raise SemanticValidationError(
            f"Roles refer to unknown factual groups: {unknown_role_groups}"
        )


def validate_observed_codes(observed_codes: Iterable[int]) -> tuple[int, ...]:
    """Fail clearly unless every observed code is covered by the contract."""

    codes = tuple(sorted({int(code) for code in observed_codes}))
    unknown = sorted(set(codes) - EXPECTED_OBSERVED_CODES)
    ambiguous = sorted(
        code
        for code in codes
        if sum(code in code_set for code_set in FACTUAL_GROUP_CODES.values()) != 1
    )
    if unknown or ambiguous:
        raise SemanticValidationError(
            "NMD semantic audit failed: "
            f"unknown_codes={unknown}; codes_without_exactly_one_factual_group={ambiguous}"
        )
    return codes


def factual_group_for_code(code: int) -> str:
    """Return the one factual group for a code, or fail explicitly."""

    validate_observed_codes((code,))
    return next(group for group, codes in FACTUAL_GROUP_CODES.items() if code in codes)


def analytical_roles_for_code(code: int) -> tuple[str, ...]:
    """Return all analytical roles that include a code's factual group."""

    group = factual_group_for_code(code)
    return tuple(role for role in ANALYTICAL_ROLES if group in ROLE_FACTUAL_GROUPS[role])


def factual_group_masks(codes: np.ndarray) -> dict[str, np.ndarray]:
    """Return mutually exclusive factual boolean masks with the input shape."""

    array = np.asarray(codes)
    validate_observed_codes(np.unique(array))
    return {
        group: np.isin(array, tuple(group_codes))
        for group, group_codes in FACTUAL_GROUP_CODES.items()
    }


def analytical_role_masks(codes: np.ndarray) -> dict[str, np.ndarray]:
    """Return overlapping analytical boolean masks with the input shape."""

    factual_masks = factual_group_masks(codes)
    return {
        role: np.logical_or.reduce([factual_masks[group] for group in groups])
        for role, groups in ROLE_FACTUAL_GROUPS.items()
    }


def semantic_masks(codes: np.ndarray) -> dict[str, dict[str, np.ndarray]]:
    """Return factual and analytical masks for an ordinary NumPy array."""

    return {
        "factual_groups": factual_group_masks(codes),
        "analytical_roles": analytical_role_masks(codes),
    }


def audit_nmd_raster(
    raster_path: Path = NMD_RASTER_PATH,
    study_area_path: Path = NMD_STUDY_AREA_PATH,
    audit_path: Path = NMD_SEMANTIC_AUDIT_PATH,
) -> dict[str, object]:
    """Audit the categorical raster block-wise and write one JSON artifact."""

    validate_contract_definition()
    study = gpd.read_file(study_area_path, layer=NMD_STUDY_AREA_LAYER)
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise SemanticValidationError("NMD semantic audit requires a non-empty study-area geometry")
    class_counts: Counter[int] = Counter()
    mask_mismatch_pixels = 0
    valid_pixels_outside_study = 0
    with rasterio.open(raster_path) as dataset:
        if study.crs is None or str(study.crs) != str(dataset.crs):
            raise SemanticValidationError(
                f"Study-area CRS is {study.crs}; raster CRS is {dataset.crs}"
            )
        study_geometry = union_all(study.geometry.array)
        for _, window in dataset.block_windows(1):
            data = dataset.read(1, window=window, masked=False)
            raster_mask = dataset.read_masks(1, window=window) > 0
            study_mask = rasterize(
                [(study_geometry, 1)],
                out_shape=data.shape,
                transform=window_transform(window, dataset.transform),
                fill=0,
                dtype="uint8",
            ).astype(bool)
            values, counts = np.unique(data[study_mask], return_counts=True)
            class_counts.update(
                {int(value): int(count) for value, count in zip(values, counts, strict=True)}
            )
            expected_raster_mask = study_mask & (data != 0)
            mask_mismatch_pixels += int(np.count_nonzero(expected_raster_mask != raster_mask))
            valid_pixels_outside_study += int(np.count_nonzero(raster_mask & ~study_mask))
        raster_facts = {
            "path": str(raster_path),
            "width": dataset.width,
            "height": dataset.height,
            "crs": str(dataset.crs),
            "resolution_m": [float(dataset.res[0]), float(dataset.res[1])],
            "block_shape": [int(value) for value in dataset.block_shapes[0]],
            "pixel_area_km2": PIXEL_AREA_KM2,
            "study_area_path": str(study_area_path),
            "study_area_layer": NMD_STUDY_AREA_LAYER,
        }

    observed_codes = validate_observed_codes(class_counts)
    factual_counts = {
        group: sum(class_counts.get(code, 0) for code in codes)
        for group, codes in FACTUAL_GROUP_CODES.items()
    }
    role_counts = {
        role: sum(factual_counts[group] for group in groups)
        for role, groups in ROLE_FACTUAL_GROUPS.items()
    }
    total_audited_pixels = sum(class_counts.values())
    no_data_pixels = factual_counts[NO_DATA]
    valid_nmd_pixels = total_audited_pixels - no_data_pixels
    terrestrial_pixels = role_counts[TERRESTRIAL_LAND]

    def area(pixel_count: int) -> float:
        return pixel_count * PIXEL_AREA_KM2

    def percent_of_valid(pixel_count: int) -> float | None:
        return pixel_count / valid_nmd_pixels * 100 if valid_nmd_pixels else None

    def percent_of_terrestrial(pixel_count: int) -> float | None:
        return pixel_count / terrestrial_pixels * 100 if terrestrial_pixels else None

    class_records = []
    for code in observed_codes:
        count = class_counts[code]
        class_records.append(
            {
                "nmd_code": code,
                "nmd_class_name": NMD_CLASS_NAMES[code],
                "factual_group": factual_group_for_code(code),
                "pixel_count": count,
                "area_km2": area(count),
                "percent_of_valid_nmd_area": (
                    None if factual_group_for_code(code) == NO_DATA else percent_of_valid(count)
                ),
                "percent_of_observed_raster_area": count / total_audited_pixels * 100,
                "analytical_roles": list(analytical_roles_for_code(code)),
            }
        )

    factual_summary = {
        group: {
            "codes": sorted(FACTUAL_GROUP_CODES[group]),
            "pixel_count": factual_counts[group],
            "area_km2": area(factual_counts[group]),
            "percent_of_valid_nmd_area": (
                None if group == NO_DATA else percent_of_valid(factual_counts[group])
            ),
            "percent_of_terrestrial_land": percent_of_terrestrial(factual_counts[group]),
        }
        for group in FACTUAL_GROUPS
    }
    role_summary = {
        role: {
            "factual_groups": list(ROLE_FACTUAL_GROUPS[role]),
            "pixel_count": role_counts[role],
            "area_km2": area(role_counts[role]),
            "percent_of_valid_nmd_area": percent_of_valid(role_counts[role]),
            "percent_of_terrestrial_land": percent_of_terrestrial(role_counts[role]),
        }
        for role in ANALYTICAL_ROLES
    }

    def overlap_count(role_a: str, role_b: str) -> int:
        groups_a = set(ROLE_FACTUAL_GROUPS[role_a])
        groups_b = set(ROLE_FACTUAL_GROUPS[role_b])
        return sum(factual_counts[group] for group in groups_a & groups_b)

    primary_role_overlaps = {
        role: {
            "pixel_count": overlap_count(PRIMARY_CANDIDATE, role),
            "area_km2": area(overlap_count(PRIMARY_CANDIDATE, role)),
        }
        for role in (
            HABITAT_CONTEXT_PROXY,
            INLAND_WATER_CONTEXT,
            WETLAND_CONTEXT,
            ARTIFICIAL_CONSTRAINT,
        )
    }

    result: dict[str, object] = {
        "contract": {
            "name": "NMD2023 v2.1 Skåne semantic contract",
            "primary_candidate_definition": "class 3 — Åkermark / arable agricultural land",
            "expected_observed_codes": sorted(EXPECTED_OBSERVED_CODES),
            "factual_groups": list(FACTUAL_GROUPS),
            "analytical_roles": list(ANALYTICAL_ROLES),
        },
        "raster": raster_facts,
        "totals": {
            "total_audited_pixel_count": total_audited_pixels,
            "total_audited_area_km2": area(total_audited_pixels),
            "total_classified_pixel_count": valid_nmd_pixels,
            "valid_nmd_pixel_count": valid_nmd_pixels,
            "valid_nmd_area_km2": area(valid_nmd_pixels),
            "terrestrial_land_pixel_count": terrestrial_pixels,
            "terrestrial_land_area_km2": area(terrestrial_pixels),
        },
        "class_records": class_records,
        "factual_group_summary": factual_summary,
        "analytical_role_summary": role_summary,
        "peat_extraction": {
            "pixel_count": factual_counts[PEAT_EXTRACTION],
            "area_km2": area(factual_counts[PEAT_EXTRACTION]),
            "percent_of_terrestrial_land": percent_of_terrestrial(factual_counts[PEAT_EXTRACTION]),
        },
        "validation": {
            "observed_codes": list(observed_codes),
            "unclassified_valid_codes": [],
            "unclassified_valid_code_count": 0,
            "all_observed_codes_have_exactly_one_factual_group": True,
            "raster_mask_matches_study_extent_except_code_0": mask_mismatch_pixels == 0,
            "raster_mask_mismatch_pixel_count": mask_mismatch_pixels,
            "valid_pixels_outside_study_count": valid_pixels_outside_study,
            "primary_candidate_overlaps": primary_role_overlaps,
            "invariants": {
                "primary_candidate_disjoint_from_habitat_context_proxy": overlap_count(
                    PRIMARY_CANDIDATE, HABITAT_CONTEXT_PROXY
                )
                == 0,
                "primary_candidate_disjoint_from_inland_water_context": overlap_count(
                    PRIMARY_CANDIDATE, INLAND_WATER_CONTEXT
                )
                == 0,
                "primary_candidate_disjoint_from_wetland_context": overlap_count(
                    PRIMARY_CANDIDATE, WETLAND_CONTEXT
                )
                == 0,
                "primary_candidate_disjoint_from_artificial_constraint": overlap_count(
                    PRIMARY_CANDIDATE, ARTIFICIAL_CONSTRAINT
                )
                == 0,
            },
        },
    }
    _write_json(audit_path, result)
    result["artifact"] = {
        "path": str(audit_path),
        "size_bytes": audit_path.stat().st_size,
    }
    return result


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.part")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_path, path)


def main() -> None:
    """Run the real-data semantic audit and print its essential results."""

    result = audit_nmd_raster()
    totals = result["totals"]
    roles = result["analytical_role_summary"]
    peat = result["peat_extraction"]
    validation = result["validation"]
    print(
        f"Semantic audit artifact: {result['artifact']['path']} ({result['artifact']['size_bytes']:,} bytes)"
    )
    print(
        f"Observed codes: {len(validation['observed_codes'])}; unclassified valid codes: {validation['unclassified_valid_code_count']}"
    )
    print(f"Total classified pixels: {totals['total_classified_pixel_count']:,}")
    for role in (
        PRIMARY_CANDIDATE,
        HABITAT_CONTEXT_PROXY,
        WETLAND_CONTEXT,
        INLAND_WATER_CONTEXT,
        TERRESTRIAL_LAND,
    ):
        summary = roles[role]
        print(
            f"{role}: {summary['area_km2']:.4f} km² ({summary['percent_of_valid_nmd_area']:.4f}% of valid NMD area)"
        )
    print(
        f"peat_extraction: {peat['pixel_count']:,} pixels, {peat['area_km2']:.4f} km², "
        f"{peat['percent_of_terrestrial_land']:.4f}% of terrestrial land"
    )
    print(f"Invariant checks: {validation['invariants']}")


if __name__ == "__main__":
    main()
