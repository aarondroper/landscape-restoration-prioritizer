import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon, box

from restoration_prioritizer.config import TARGET_CRS
from restoration_prioritizer.protected_areas import (
    NATIONAL_DESIGNATIONS,
    NATURA_DESIGNATIONS,
    ProtectedAreasError,
    candidate_diagnostics,
    filter_national_records,
    filter_natura_records,
    filter_to_context,
    normalize_national,
    normalize_natura,
    union_protected_geometries,
    validate_and_repair_source,
)


def national_frame(
    designations: list[str],
    statuses: list[str],
    geometries: list[Polygon],
    ids: list[str] | None = None,
) -> gpd.GeoDataFrame:
    identifiers = ids or [f"n-{index}" for index in range(len(geometries))]
    return gpd.GeoDataFrame(
        {
            "NVRID": identifiers,
            "NAMN": [f"National area {index}" for index in range(len(geometries))],
            "SKYDDSTYP": designations,
            "BESLUTSSTATUS": statuses,
        },
        geometry=geometries,
        crs=TARGET_CRS,
    )


def natura_frame(
    types: list[str], geometries: list[Polygon], ids: list[str] | None = None
) -> gpd.GeoDataFrame:
    identifiers = ids or [f"SE000{index}" for index in range(len(geometries))]
    return gpd.GeoDataFrame(
        {
            "OBJECTID": range(len(geometries)),
            "OMRADESKOD": identifiers,
            "OMRADESNAMN": [f"Natura area {index}" for index in range(len(geometries))],
            "OMRADESTYP": types,
        },
        geometry=geometries,
        crs=TARGET_CRS,
    )


def test_approved_national_designation_and_status_filtering() -> None:
    source = national_frame(
        ["Nationalpark", "Naturreservat", "Naturvårdsområde", "Naturreservat"],
        ["Gällande", "Gällande", "Gällande", "Överklagat"],
        [box(0, 0, 1, 1)] * 4,
    )

    selected = filter_national_records(source)

    assert selected["SKYDDSTYP"].tolist() == ["Nationalpark", "Naturreservat"]
    assert set(selected["SKYDDSTYP"]) == NATIONAL_DESIGNATIONS


def test_unapproved_national_designation_is_rejected() -> None:
    source = national_frame(["Naturvårdsområde"], ["Gällande"], [box(0, 0, 1, 1)])

    with pytest.raises(ProtectedAreasError, match="No current"):
        filter_national_records(source)


def test_natura_designation_filter_keeps_sci_spa_and_combined_only() -> None:
    source = natura_frame(
        ["SCI", "SPA", "SPA/SCI", "Ramsar"], [box(index, 0, index + 1, 1) for index in range(4)]
    )

    selected = filter_natura_records(source)

    assert selected["OMRADESTYP"].tolist() == ["SCI", "SPA", "SPA/SCI"]
    assert set(selected["OMRADESTYP"]) == NATURA_DESIGNATIONS


def test_source_geometry_is_repaired_and_nonpolygon_is_rejected() -> None:
    invalid = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    source = national_frame(["Naturreservat"], ["Gällande"], [invalid])

    repaired, count = validate_and_repair_source(
        source, frozenset({"NVRID", "NAMN", "SKYDDSTYP", "BESLUTSSTATUS"}), "NVRID", "national"
    )

    assert count == 1
    assert repaired.geometry.iloc[0].is_valid
    nonpolygon = source.copy()
    nonpolygon.geometry = [Point(0, 0)]
    with pytest.raises(ProtectedAreasError, match="non-polygon"):
        validate_and_repair_source(
            nonpolygon,
            frozenset({"NVRID", "NAMN", "SKYDDSTYP", "BESLUTSSTATUS"}),
            "NVRID",
            "national",
        )


def test_exact_context_filter_removes_outside_feature_and_keeps_cross_boundary() -> None:
    source = national_frame(
        ["Naturreservat", "Naturreservat", "Naturreservat"],
        ["Gällande"] * 3,
        [box(0, 0, 1, 1), box(0.9, 0, 2, 1), box(5, 5, 6, 6)],
    )

    selected = filter_to_context(source, box(0, 0, 1, 1))

    assert selected["NVRID"].tolist() == ["n-0", "n-1"]


def test_overlapping_national_and_natura_polygons_are_physically_deduplicated() -> None:
    national = normalize_national(
        national_frame(["Naturreservat"], ["Gällande"], [box(0, 0, 10, 10)])
    )
    natura = normalize_natura(natura_frame(["SCI"], [box(5, 0, 15, 10)]))

    footprint, audit = union_protected_geometries(national, natura)

    assert footprint.area == pytest.approx(150.0)
    assert audit["national_union_area_m2"] == pytest.approx(100.0)
    assert audit["natura_union_area_m2"] == pytest.approx(100.0)
    assert audit["national_natura_intersection_area_m2"] == pytest.approx(50.0)
    assert audit["final_union_area_m2"] == pytest.approx(150.0)


def test_duplicate_legal_designations_do_not_increase_footprint_area() -> None:
    geometry = box(0, 0, 10, 10)
    national = normalize_national(
        national_frame(["Naturreservat", "Naturreservat"], ["Gällande"] * 2, [geometry, geometry])
    )
    natura = normalize_natura(natura_frame(["SCI"], [geometry]))

    footprint, audit = union_protected_geometries(national, natura)

    assert footprint.area == pytest.approx(100.0)
    assert audit["final_union_area_m2"] == pytest.approx(100.0)
    assert audit["deduplicated_area_m2"] == pytest.approx(200.0)


def test_normalized_layers_have_small_source_grounded_schema() -> None:
    national = normalize_national(national_frame(["Nationalpark"], ["Gällande"], [box(0, 0, 1, 1)]))
    natura = normalize_natura(natura_frame(["SPA/SCI"], [box(0, 0, 1, 1)]))

    assert national.columns.tolist() == [
        "source_id",
        "name",
        "designation",
        "source_status",
        "source_authority",
        "geometry",
    ]
    assert natura.columns.tolist() == [
        "source_id",
        "name",
        "designation",
        "source_authority",
        "geometry",
    ]
    assert str(national.crs) == TARGET_CRS
    assert str(natura.crs) == TARGET_CRS


def test_candidate_diagnostics_report_distance_and_overlap_without_scoring() -> None:
    candidates = gpd.GeoDataFrame(
        {
            "hex_id": ["h0", "h1", "h2"],
            "candidate_fraction_of_terrestrial": [0.5, 0.6, 0.7],
        },
        geometry=[box(0, 0, 1, 1), box(2, 0, 3, 1), box(10, 0, 11, 1)],
        crs=TARGET_CRS,
    )

    diagnostics = candidate_diagnostics(candidates, box(0, 0, 3, 1))

    assert diagnostics["candidate_intersect_count"] == 2
    assert diagnostics["candidate_centroid_inside_count"] == 2
    assert diagnostics["geometry_to_geometry_minimum_distance_m"]["min"] == pytest.approx(0.0)
    assert diagnostics["overlap_candidates"]["count"] == 2
    assert (
        diagnostics["overlap_candidates"]["protected_fraction_of_candidate_geometry"]["max"] == 1.0
    )
