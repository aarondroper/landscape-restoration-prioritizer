import json
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import shape

from restoration_prioritizer.contextual_layers import (
    MODERATE_MINIMUM_PATCH_HA,
    MODERATE_SIMPLIFICATION_TOLERANCE_M,
    PROTECTED_AREAS_OUTPUT_PATH,
    SIMPLIFICATION_METHOD,
    TERRESTRIAL_PROTECTED_CODES,
    WETLAND_INLAND_WATER_CODES,
    WETLAND_INLAND_WATER_OUTPUT_PATH,
    _write_display_variant,
    validate_contextual_geojson,
)
from restoration_prioritizer.nmd_semantics import FACTUAL_GROUP_CODES, INLAND_WATER, SEA


EXPECTED_PROTECTED_DISPLAY_AREA_RATIO = 0.9967032609342738
EXPECTED_WETLAND_DISPLAY_AREA_RATIO = 0.7968110277838426
SKANE_BOUNDS = (11.0, 54.5, 15.5, 57.5)


@pytest.mark.parametrize(
    "path, expected_features, expected_vertices",
    [
        (PROTECTED_AREAS_OUTPUT_PATH, 571, 40_607),
        (WETLAND_INLAND_WATER_OUTPUT_PATH, 8_369, 419_746),
    ],
)
def test_production_contextual_outputs_have_minimal_valid_geojson_contract(
    path: Path, expected_features: int, expected_vertices: int
) -> None:
    assert path.exists()
    metrics = validate_contextual_geojson(path)
    assert metrics["feature_count"] == expected_features
    assert metrics["vertex_count"] == expected_vertices
    assert metrics["geometry_types"] == ["Polygon"]

    payload = json.loads(path.read_text(encoding="utf-8"))
    for feature in payload["features"]:
        geometry = shape(feature["geometry"])
        assert geometry.is_valid and not geometry.is_empty
        assert geometry.geom_type == "Polygon"
        min_lon, min_lat, max_lon, max_lat = geometry.bounds
        assert SKANE_BOUNDS[0] <= min_lon <= max_lon <= SKANE_BOUNDS[2]
        assert SKANE_BOUNDS[1] <= min_lat <= max_lat <= SKANE_BOUNDS[3]
        assert feature["properties"] == {}


def test_protected_source_semantics_are_terrestrial_only() -> None:
    expected = frozenset(
        code
        for group, codes in FACTUAL_GROUP_CODES.items()
        if group not in {"no_data", INLAND_WATER, SEA}
        for code in codes
    )
    assert TERRESTRIAL_PROTECTED_CODES == expected
    assert {0, 61, 62}.isdisjoint(TERRESTRIAL_PROTECTED_CODES)


def test_wetland_source_semantics_are_exact_and_exclude_sea() -> None:
    expected = frozenset(
        code
        for group in ("established_forest_wetland", "transitional_forest_wetland", "open_wetland")
        for code in FACTUAL_GROUP_CODES[group]
    ) | {61}
    assert WETLAND_INLAND_WATER_CODES == expected
    assert 62 not in WETLAND_INLAND_WATER_CODES


def test_approved_moderate_generalization_parameters_are_fixed() -> None:
    assert MODERATE_MINIMUM_PATCH_HA == 1.0
    assert MODERATE_SIMPLIFICATION_TOLERANCE_M == 15.0
    assert SIMPLIFICATION_METHOD == "Shapely topology-preserving Douglas-Peucker"


@pytest.mark.parametrize(
    "path, expected_ratio",
    [
        (PROTECTED_AREAS_OUTPUT_PATH, EXPECTED_PROTECTED_DISPLAY_AREA_RATIO),
        (WETLAND_INLAND_WATER_OUTPUT_PATH, EXPECTED_WETLAND_DISPLAY_AREA_RATIO),
    ],
)
def test_display_area_reconciles_with_approved_moderate_variant(
    path: Path, expected_ratio: float
) -> None:
    frame = gpd.read_file(path)
    projected = frame.to_crs("EPSG:3006")
    area_m2 = float(projected.geometry.area.sum())
    # These authoritative areas are the 10 m source masks. A small tolerance
    # allows only delivery rounding/serialization differences, not retuning.
    authoritative_area_m2 = 466_976_600.0 if "protected" in path.name else 1_012_207_000.0
    assert area_m2 / authoritative_area_m2 == pytest.approx(expected_ratio, abs=0.00001)


def test_display_variant_serialization_is_deterministic(tmp_path: Path) -> None:
    shapes_path = tmp_path / "shapes.jsonl"
    shapes_path.write_text(
        json.dumps(
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[400000, 6200000], [400500, 6200000], [400500, 6200500], [400000, 6200000]]
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    transformer = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True)
    first = tmp_path / "first.geojson"
    second = tmp_path / "second.geojson"
    _write_display_variant(shapes_path, first, 25_000_000.0, transformer)
    _write_display_variant(shapes_path, second, 25_000_000.0, transformer)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    "path",
    [PROTECTED_AREAS_OUTPUT_PATH, WETLAND_INLAND_WATER_OUTPUT_PATH],
)
def test_production_coordinates_use_bounded_web_precision(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))

    def max_decimal_places(value: object) -> int:
        if isinstance(value, list):
            if value and isinstance(value[0], (int, float)):
                return max(
                    (len(str(number).split(".")[1]) if "." in str(number) else 0)
                    for number in value
                )
            return max((max_decimal_places(item) for item in value), default=0)
        return 0

    assert (
        max(
            max_decimal_places(feature["geometry"]["coordinates"])
            for feature in payload["features"]
        )
        <= 6
    )
