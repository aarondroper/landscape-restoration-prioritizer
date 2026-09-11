"""Build display-only contextual map layers.

These artifacts are deliberately separate from the analytical model. The
source masks use the same NMD roles and protected-area semantics as the scoring
pipeline, then apply moderate display rules for static EPSG:4326 GeoJSON assets.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union

from .delivery_serialization import (
    GEOMETRY_DECIMALS,
    finite_coordinates,
    round_coordinates,
    serialize_feature_collection,
)
from .nmd_semantics import (
    FACTUAL_GROUP_CODES,
    INLAND_WATER,
    ROLE_FACTUAL_GROUPS,
    TERRESTRIAL_LAND,
    WETLAND_CONTEXT,
)

NMD_PATH = Path("data/processed/nmd/nmd2023_v2_1_skane.tif")
PROTECTED_PATH = Path("data/processed/protected_areas.gpkg")
STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"
PROTECTED_FOOTPRINT_LAYER = "protected_footprint"

PROTECTED_AREAS_OUTPUT_PATH = Path("data/processed/delivery/protected_areas.geojson")
WETLAND_INLAND_WATER_OUTPUT_PATH = Path("data/processed/delivery/wetland_inland_water.geojson")

RASTER_RESOLUTION_M = 10
PIXEL_AREA_M2 = RASTER_RESOLUTION_M * RASTER_RESOLUTION_M
MODERATE_MINIMUM_PATCH_HA = 1.0
MODERATE_SIMPLIFICATION_TOLERANCE_M = 15.0
SIMPLIFICATION_METHOD = "Shapely topology-preserving Douglas-Peucker"
WGS84_EPSG = 4326

WETLAND_INLAND_WATER_CODES = frozenset(
    code
    for group in (*ROLE_FACTUAL_GROUPS[WETLAND_CONTEXT], INLAND_WATER)
    for code in FACTUAL_GROUP_CODES[group]
)
TERRESTRIAL_PROTECTED_CODES = frozenset(
    code for group in ROLE_FACTUAL_GROUPS[TERRESTRIAL_LAND] for code in FACTUAL_GROUP_CODES[group]
)


class ContextualLayerError(ValueError):
    """Raised when a contextual display artifact cannot be built."""


def _clean_geometry(geometry: Any) -> Any:
    if geometry.is_empty:
        return geometry
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type == "GeometryCollection":
        geometry = unary_union(
            part for part in geometry.geoms if part.geom_type in {"Polygon", "MultiPolygon"}
        )
    return geometry


def _coordinate_count(value: Any) -> int:
    if not value:
        return 0
    if isinstance(value[0], (int, float)):
        return 1
    return sum(_coordinate_count(item) for item in value)


def _round_geometry(geometry: Any) -> dict[str, Any]:
    return round_coordinates(mapping(geometry), GEOMETRY_DECIMALS)


def _payload_sizes(payload: bytes) -> dict[str, Any]:
    sizes: dict[str, Any] = {
        "raw_bytes": len(payload),
        "gzip_bytes": len(gzip.compress(payload, compresslevel=9, mtime=0)),
        "brotli_bytes": None,
    }
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:
        sizes["brotli_status"] = "not available in generation environment"
    else:
        sizes["brotli_bytes"] = len(brotli.compress(payload, quality=11))
        sizes["brotli_status"] = "measured with Brotli quality 11"
    return sizes


def _make_mask_raster(
    path: Path,
    codes: frozenset[int] | None = None,
    protected_geometry: Any | None = None,
) -> int:
    selected_pixels = 0
    with rasterio.open(NMD_PATH) as source:
        profile = source.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="uint8",
            count=1,
            nodata=0,
            compress="DEFLATE",
            predictor=1,
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        with rasterio.open(path, "w", **profile) as target:
            for _, window in source.block_windows(1):
                data = source.read(1, window=window, masked=False)
                valid = source.read_masks(1, window=window) > 0
                if codes is not None:
                    selected = np.isin(data, tuple(codes)) & valid
                else:
                    selected = (
                        rasterize(
                            [(protected_geometry, 1)],
                            out_shape=data.shape,
                            transform=rasterio.windows.transform(window, source.transform),
                            fill=0,
                            dtype="uint8",
                            all_touched=False,
                        ).astype(bool)
                        & np.isin(data, tuple(TERRESTRIAL_PROTECTED_CODES))
                        & valid
                    )
                selected_pixels += int(selected.sum())
                target.write(selected.astype("uint8"), 1, window=window)
    return selected_pixels


def _polygonize_jsonl(mask_path: Path, output_path: Path) -> None:
    rio = shutil.which("rio") or str(Path(".venv/bin/rio"))
    try:
        subprocess.run(
            [
                rio,
                "shapes",
                "--as-mask",
                "--bidx",
                "1",
                "--projected",
                "--sequence",
                "--compact",
                str(mask_path),
                "-o",
                str(output_path),
            ],
            check=True,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContextualLayerError(f"Could not polygonize contextual mask {mask_path}") from exc


def _write_display_variant(
    shapes_path: Path,
    output_path: Path,
    authoritative_area_m2: float,
    transformer: Transformer,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    feature_count = 0
    vertex_count = 0
    retained_mask_area_m2 = 0.0
    display_area_m2 = 0.0
    features: list[dict[str, Any]] = []
    with shapes_path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            geometry = _clean_geometry(shape(json.loads(line)["geometry"]))
            if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                continue
            area_m2 = float(geometry.area)
            if area_m2 < MODERATE_MINIMUM_PATCH_HA * 10_000:
                continue
            simplified = _clean_geometry(
                geometry.simplify(
                    MODERATE_SIMPLIFICATION_TOLERANCE_M,
                    preserve_topology=True,
                )
            )
            if simplified.is_empty or simplified.geom_type not in {"Polygon", "MultiPolygon"}:
                continue
            delivered = transform_geometry(transformer.transform, simplified)
            features.append(
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": _round_geometry(delivered),
                }
            )
            feature_count += 1
            vertex_count += _coordinate_count(mapping(delivered)["coordinates"])
            retained_mask_area_m2 += area_m2
            display_area_m2 += float(simplified.area)
    if not all(finite_coordinates(feature["geometry"]) for feature in features):
        raise ContextualLayerError(
            f"Contextual geometry contains non-finite coordinates: {output_path}"
        )
    payload = serialize_feature_collection(features)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return {
        "feature_count": feature_count,
        "vertex_count": vertex_count,
        "authoritative_area_m2": authoritative_area_m2,
        "retained_mask_area_m2_before_simplification": retained_mask_area_m2,
        "retained_mask_area_percent": 100.0 * retained_mask_area_m2 / authoritative_area_m2,
        "display_geometry_area_m2_after_simplification": display_area_m2,
        "display_geometry_area_percent": 100.0 * display_area_m2 / authoritative_area_m2,
        "payload": _payload_sizes(payload),
        "display_rules": {
            "minimum_patch_ha": MODERATE_MINIMUM_PATCH_HA,
            "simplification_tolerance_m": MODERATE_SIMPLIFICATION_TOLERANCE_M,
            "simplification": SIMPLIFICATION_METHOD,
            "topology_preservation": True,
        },
        "crs": {"authority": "EPSG", "code": WGS84_EPSG},
        "properties": "{} on every feature",
    }


def _protected_geometry() -> Any:
    footprint = gpd.read_file(PROTECTED_PATH, layer=PROTECTED_FOOTPRINT_LAYER)
    study = gpd.read_file(STUDY_AREA_PATH, layer=STUDY_AREA_LAYER)
    if footprint.crs is None or study.crs is None:
        raise ContextualLayerError("Protected-area or study-area source has no CRS")
    study = study.to_crs(footprint.crs)
    return _clean_geometry(
        unary_union(footprint.geometry.array).intersection(unary_union(study.geometry.array))
    )


def _build_layer(
    name: str,
    output_path: Path,
    codes: frozenset[int] | None = None,
    protected_geometry: Any | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as temporary_directory:
        temporary = Path(temporary_directory)
        mask_path = temporary / "mask.tif"
        shapes_path = temporary / "mask.jsonl"
        with rasterio.open(NMD_PATH) as source:
            source_crs = source.crs
        if source_crs is None:
            raise ContextualLayerError("NMD source has no CRS")
        pixels = _make_mask_raster(mask_path, codes=codes, protected_geometry=protected_geometry)
        _polygonize_jsonl(mask_path, shapes_path)
        result = _write_display_variant(
            shapes_path,
            output_path,
            float(pixels * PIXEL_AREA_M2),
            Transformer.from_crs(source_crs, f"EPSG:{WGS84_EPSG}", always_xy=True),
        )
    result.update(
        {
            "name": name,
            "source_resolution_m": RASTER_RESOLUTION_M,
            "authoritative_pixel_count": pixels,
            "source_semantics": "protected terrestrial NMD mask"
            if codes is None
            else "NMD wetland and inland-water role; sea excluded",
            "source_codes": sorted(codes)
            if codes is not None
            else sorted(TERRESTRIAL_PROTECTED_CODES),
            "output_path": str(output_path),
            "display_only": True,
        }
    )
    return result


def build_contextual_layers(
    protected_output_path: Path = PROTECTED_AREAS_OUTPUT_PATH,
    wetland_output_path: Path = WETLAND_INLAND_WATER_OUTPUT_PATH,
) -> dict[str, Any]:
    """Generate both contextual display artifacts deterministically."""

    protected = _build_layer(
        "protected-areas",
        protected_output_path,
        protected_geometry=_protected_geometry(),
    )
    wetland = _build_layer(
        "wetland-inland-water",
        wetland_output_path,
        codes=WETLAND_INLAND_WATER_CODES,
    )
    return {"protected_areas": protected, "wetland_inland_water": wetland}


def coordinate_count_from_geojson(path: Path) -> int:
    """Count GeoJSON coordinates for delivery reporting and focused tests."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return sum(
        _coordinate_count(feature["geometry"]["coordinates"]) for feature in payload["features"]
    )


def validate_contextual_geojson(path: Path) -> dict[str, Any]:
    """Validate the static delivery contract without changing the source data."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextualLayerError(f"Invalid contextual GeoJSON: {path}") from exc
    if payload.get("type") != "FeatureCollection":
        raise ContextualLayerError(f"Contextual GeoJSON is not a FeatureCollection: {path}")
    if not payload.get("features"):
        raise ContextualLayerError(f"Contextual GeoJSON has no features: {path}")
    geometry_types: set[str] = set()
    for feature in payload["features"]:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ContextualLayerError(f"Contextual GeoJSON contains non-polygon geometry: {path}")
        if feature.get("properties") != {}:
            raise ContextualLayerError(
                f"Contextual GeoJSON contains unnecessary properties: {path}"
            )
        geometry_types.add(geometry["type"])
        coordinates = json.dumps(geometry.get("coordinates", []))
        if any(token in coordinates.lower() for token in ("nan", "infinity")):
            raise ContextualLayerError(
                f"Contextual GeoJSON contains non-finite coordinates: {path}"
            )
    return {
        "feature_count": len(payload["features"]),
        "vertex_count": coordinate_count_from_geojson(path),
        "geometry_types": sorted(geometry_types),
        "payload": _payload_sizes(path.read_bytes()),
    }
