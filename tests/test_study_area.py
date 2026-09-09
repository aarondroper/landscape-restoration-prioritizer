import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from restoration_prioritizer.study_area import (
    COUNTY_CODE,
    NUTS3_CODE,
    STUDY_AREA_NAME,
    StudyAreaError,
    build_study_area,
)


def make_source(
    *,
    county_codes: list[str] | None = None,
    crs: str = "EPSG:3006",
    include_version: bool = True,
    geometries: list[Polygon] | None = None,
) -> gpd.GeoDataFrame:
    counties = county_codes or [COUNTY_CODE, COUNTY_CODE]
    geometry = geometries or [box(0, 0, 10, 10), box(10, 0, 20, 10)]
    values = {
        "lanskod": counties,
        "referensdatum": ["2025-01-01"] * len(counties),
        "geometry": geometry,
    }
    if include_version:
        values["version"] = ["2025"] * len(counties)
    return gpd.GeoDataFrame(values, crs=crs)


def test_multiple_county_polygons_dissolve_to_one_feature_with_metadata() -> None:
    output = build_study_area(make_source())

    assert len(output) == 1
    assert output.geometry.iloc[0].area == 200
    assert output.crs.to_epsg() == 3006
    assert output.loc[0, "study_area_name"] == STUDY_AREA_NAME
    assert output.loc[0, "county_code"] == COUNTY_CODE
    assert output.loc[0, "nuts3_code"] == NUTS3_CODE
    assert output.loc[0, "source_product"] == "DeSO 2025"


def test_wrong_county_code_is_rejected() -> None:
    with pytest.raises(StudyAreaError, match="outside county code"):
        build_study_area(make_source(county_codes=[COUNTY_CODE, "01"]))


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(StudyAreaError, match="missing required fields.*version"):
        build_study_area(make_source(include_version=False))


def test_empty_geometry_is_rejected() -> None:
    with pytest.raises(StudyAreaError, match="missing or empty geometry"):
        build_study_area(make_source(geometries=[box(0, 0, 10, 10), Polygon()]))


def test_crs_mismatch_is_rejected() -> None:
    with pytest.raises(StudyAreaError, match="expected EPSG:3006"):
        build_study_area(make_source(crs="EPSG:4326"))
