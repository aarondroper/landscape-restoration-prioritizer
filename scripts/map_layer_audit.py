"""Measure display-only environmental map-layer candidates.

This script is deliberately separate from the analytical model. It reads the
finalized NMD raster and protected-area footprint, writes small diagnostic
binary masks window by window, and polygonizes those masks only for payload
and fidelity measurement. No production delivery artifact is modified.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import rasterio
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from restoration_prioritizer.contextual_layers import (
    MODERATE_MINIMUM_PATCH_HA,
    MODERATE_SIMPLIFICATION_TOLERANCE_M,
    WETLAND_INLAND_WATER_CODES,
    _clean_geometry,
    _coordinate_count,
    _make_mask_raster as _make_production_mask_raster,
    _polygonize_jsonl as _polygonize_production_mask,
)

from restoration_prioritizer.nmd_semantics import (
    HABITAT_CONTEXT_PROXY,
    ROLE_FACTUAL_GROUPS,
    FACTUAL_GROUP_CODES,
)

ROOT = Path(__file__).resolve().parents[1]
NMD_PATH = ROOT / "data/processed/nmd/nmd2023_v2_1_skane.tif"
PROTECTED_PATH = ROOT / "data/processed/protected_areas.gpkg"
STUDY_PATH = ROOT / "data/processed/study_area.gpkg"
CANDIDATE_PATH = ROOT / "data/processed/candidate_units.gpkg"
OUTPUT_DIR = ROOT / "data/derived/diagnostics/map_layers"

NMD_ROLE_CODES = {
    "riparian_context": WETLAND_INLAND_WATER_CODES,
    "semi_natural_habitat": frozenset(
        code
        for group in ROLE_FACTUAL_GROUPS[HABITAT_CONTEXT_PROXY]
        for code in FACTUAL_GROUP_CODES[group]
    ),
    "candidate_agricultural": frozenset({3}),
}
VARIANTS = (
    ("light", 0.25, 5.0),
    ("moderate", MODERATE_MINIMUM_PATCH_HA, MODERATE_SIMPLIFICATION_TOLERANCE_M),
    ("strong", 5.0, 30.0),
)


def _geometry_metrics(geometries: Iterable[Any]) -> dict[str, Any]:
    values = list(geometries)
    return {
        "feature_count": len(values),
        "geometry_types": dict(
            sorted(
                {
                    geometry.geom_type: sum(item.geom_type == geometry.geom_type for item in values)
                    for geometry in values
                }.items()
            )
        ),
        "vertex_count": sum(
            _coordinate_count(mapping(geometry)["coordinates"]) for geometry in values
        ),
        "area_m2": float(sum(geometry.area for geometry in values)),
    }


def _write_geojson(
    path: Path, geometries: Iterable[Any], properties: dict[str, Any] | None = None
) -> bytes:
    features = [
        {
            "type": "Feature",
            "properties": properties or {},
            "geometry": mapping(geometry),
        }
        for geometry in geometries
        if not geometry.is_empty
    ]
    payload = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(payload)
    return payload


def _payload_metrics(payload: bytes) -> dict[str, Any]:
    return {
        "raw_bytes": len(payload),
        "gzip_bytes": len(gzip.compress(payload, compresslevel=9, mtime=0)),
        "brotli_bytes": None,
        "brotli_status": "not available in audit environment",
    }


def _make_mask_raster(
    name: str,
    codes: frozenset[int] | None = None,
    protected_geometry: Any | None = None,
) -> tuple[Path, int]:
    path = OUTPUT_DIR / f"{name}.tif"
    return path, _make_production_mask_raster(
        path, codes=codes, protected_geometry=protected_geometry
    )


def _polygonize_jsonl(mask_path: Path) -> Path:
    shapes_path = mask_path.with_suffix(".jsonl")
    _polygonize_production_mask(mask_path, shapes_path)
    return shapes_path


def _write_feature(stream: Any, geometry: Any, properties: dict[str, Any]) -> None:
    feature = {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(geometry),
    }
    if stream.tell() > len('{"type":"FeatureCollection","features":['):
        stream.write(",")
    stream.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":")))


def _stream_polygonized_variants(
    shapes_path: Path,
    authoritative_area_m2: float,
    prefix: str,
    exact: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    handles: dict[str, Any] = {}
    paths: dict[str, Path] = {}
    for variant, _, _ in VARIANTS:
        path = OUTPUT_DIR / f"{prefix}_{variant}.geojson"
        handle = path.open("w", encoding="utf-8")
        handle.write('{"type":"FeatureCollection","features":[')
        handles[variant] = handle
        paths[variant] = path
    exact_handle = None
    exact_path = OUTPUT_DIR / f"{prefix}_exact.geojson"
    if exact:
        exact_handle = exact_path.open("w", encoding="utf-8")
        exact_handle.write('{"type":"FeatureCollection","features":[')

    base_count = 0
    base_vertices = 0
    base_area_m2 = 0.0
    variant_stats = {
        variant: {
            "feature_count": 0,
            "vertex_count": 0,
            "retained_mask_area_m2": 0.0,
            "display_area_m2": 0.0,
        }
        for variant, _, _ in VARIANTS
    }
    with shapes_path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            geometry = _clean_geometry(shape(json.loads(line)["geometry"]))
            if geometry.is_empty:
                continue
            area_m2 = float(geometry.area)
            vertices = _coordinate_count(mapping(geometry)["coordinates"])
            base_count += 1
            base_vertices += vertices
            base_area_m2 += area_m2
            if exact_handle is not None:
                _write_feature(exact_handle, geometry, {"display_only": True})
            for variant, minimum_hectares, tolerance_m in VARIANTS:
                if area_m2 < minimum_hectares * 10_000:
                    continue
                stats = variant_stats[variant]
                stats["retained_mask_area_m2"] += area_m2
                simplified = geometry.simplify(tolerance_m, preserve_topology=True)
                if simplified.is_empty:
                    continue
                _write_feature(handles[variant], simplified, {"display_only": True})
                stats["feature_count"] += 1
                stats["vertex_count"] += _coordinate_count(mapping(simplified)["coordinates"])
                stats["display_area_m2"] += float(simplified.area)
    for handle in handles.values():
        handle.write("]}")
        handle.close()
    if exact_handle is not None:
        exact_handle.write("]}")
        exact_handle.close()

    variants = {}
    for variant, minimum_hectares, tolerance_m in VARIANTS:
        stats = variant_stats[variant]
        payload = _payload_metrics(paths[variant].read_bytes())
        variants[variant] = {
            "display_rules": {
                "minimum_patch_ha": minimum_hectares,
                "simplification_tolerance_m": tolerance_m,
                "simplification": "Shapely topology-preserving Douglas-Peucker",
            },
            "feature_count": stats["feature_count"],
            "vertex_count": stats["vertex_count"],
            "authoritative_mask_area_m2": authoritative_area_m2,
            "retained_mask_area_m2_before_simplification": stats["retained_mask_area_m2"],
            "retained_mask_area_percent": 100.0
            * stats["retained_mask_area_m2"]
            / authoritative_area_m2,
            "display_geometry_area_m2_after_simplification": stats["display_area_m2"],
            "display_geometry_area_percent": 100.0
            * stats["display_area_m2"]
            / authoritative_area_m2,
            "payload": payload,
            "path": str(paths[variant]),
        }
    exact_result = None
    if exact:
        exact_payload = exact_path.read_bytes()
        exact_result = {
            "feature_count": base_count,
            "vertex_count": base_vertices,
            "mask_area_m2": authoritative_area_m2,
            "polygon_area_m2": base_area_m2,
            "payload": _payload_metrics(exact_payload),
            "path": str(exact_path),
        }
    return {
        "base_polygonized_feature_count": base_count,
        "base_polygonized_vertex_count": base_vertices,
        "base_polygonized_area_m2": base_area_m2,
        "variants": variants,
    }, exact_result


def _protected_metrics() -> dict[str, Any]:
    national = gpd.read_file(PROTECTED_PATH, layer="national_protection")
    natura = gpd.read_file(PROTECTED_PATH, layer="natura2000")
    footprint = gpd.read_file(PROTECTED_PATH, layer="protected_footprint")
    study = gpd.read_file(STUDY_PATH, layer="study_area")
    footprint_geometry = _clean_geometry(unary_union(footprint.geometry.array))
    study_geometry = _clean_geometry(unary_union(study.geometry.array))
    clipped = _clean_geometry(footprint_geometry.intersection(study_geometry))
    clipped_payload = _write_geojson(
        OUTPUT_DIR / "protected_clipped_union.geojson",
        [clipped],
        {"display_only": True, "representation": "deduplicated footprint clipped to Skåne"},
    )
    terrestrial_geometry = _clean_geometry(footprint_geometry.intersection(study_geometry))
    mask_path = OUTPUT_DIR / "protected_terrestrial_mask.tif"
    if mask_path.exists():
        with rasterio.open(mask_path) as dataset:
            pixels = int(dataset.read(1).sum())
    else:
        mask_path, pixels = _make_mask_raster(
            "protected_terrestrial_mask",
            protected_geometry=terrestrial_geometry,
        )
    shapes_path = _polygonize_jsonl(mask_path)
    stream_metrics, exact = _stream_polygonized_variants(
        shapes_path,
        float(pixels * 100.0),
        "protected_terrestrial",
        exact=True,
    )
    return {
        "source_geometry_count": len(national) + len(natura),
        "source_geometry_counts": {
            "national_protection": len(national),
            "natura2000": len(natura),
        },
        "source_geometry_type": {
            "national_protection": national.geometry.geom_type.mode()[0],
            "natura2000": natura.geometry.geom_type.mode()[0],
        },
        "deduplicated_footprint_geometry_count": len(footprint),
        "deduplicated_footprint_geometry_type": footprint.geometry.geom_type.mode()[0],
        "variant_a_vector_clipped_to_skane": {
            "feature_count": 1,
            "vertex_count": _geometry_metrics([clipped])["vertex_count"],
            "geometry_type": clipped.geom_type,
            "area_m2": clipped.area,
            "payload": _payload_metrics(clipped_payload),
            "path": str(OUTPUT_DIR / "protected_clipped_union.geojson"),
        },
        "variant_b_terrestrial_nmd_semantics": {
            "method": "10 m NMD pixel-center protected-footprint mask; code 0, inland water, and sea excluded",
            "mask_source": "deduplicated protected footprint intersected with Skåne, rasterized all_touched=False",
            "exact": exact,
            "display_variants": stream_metrics["variants"],
            "base_polygonized_feature_count": stream_metrics["base_polygonized_feature_count"],
            "base_polygonized_vertex_count": stream_metrics["base_polygonized_vertex_count"],
        },
        "mask_path": str(mask_path),
    }


def _raster_role_metrics(name: str, codes: frozenset[int]) -> dict[str, Any]:
    mask_path = OUTPUT_DIR / f"{name}.tif"
    if mask_path.exists():
        with rasterio.open(mask_path) as dataset:
            pixels = int(dataset.read(1).sum())
    else:
        mask_path, pixels = _make_mask_raster(name, codes=codes)
    authoritative_area_m2 = float(pixels * 100.0)
    shapes_path = _polygonize_jsonl(mask_path)
    stream_metrics, _ = _stream_polygonized_variants(
        shapes_path,
        authoritative_area_m2,
        name,
    )
    return {
        "source_codes": sorted(codes),
        "authoritative_mask_area_m2": authoritative_area_m2,
        "authoritative_mask_area_ha": authoritative_area_m2 / 10_000,
        "base_polygonized_feature_count": stream_metrics["base_polygonized_feature_count"],
        "base_polygonized_vertex_count": stream_metrics["base_polygonized_vertex_count"],
        "base_polygonized_area_m2": stream_metrics["base_polygonized_area_m2"],
        "variants": stream_metrics["variants"],
        "mask_path": str(mask_path),
    }


def _candidate_metrics() -> dict[str, Any]:
    candidates = gpd.read_file(CANDIDATE_PATH, layer="candidate_units")
    candidate_payload = ROOT / "data/processed/delivery/candidates.geojson"
    return {
        "candidate_unit_count": len(candidates),
        "candidate_land_area_ha_sum": float(candidates["candidate_area_m2"].sum() / 10_000),
        "candidate_land_field": "candidate_area_m2 / candidate_land_area_ha in selected-area inspector",
        "existing_candidate_delivery_raw_bytes": candidate_payload.stat().st_size
        if candidate_payload.exists()
        else None,
        "vector_generated": False,
        "reason": "Candidate hexagons and candidate-land hectares already expose this screening input; a land-cover polygon would be visually redundant and much more fragmented.",
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "audit": {
            "purpose": "display-only map-layer feasibility; analytical artifacts untouched",
            "nmd_path": str(NMD_PATH),
            "protected_path": str(PROTECTED_PATH),
            "raster_resolution_m": 10,
            "pixel_area_m2": 100,
            "brotli": "unavailable",
            "generalization_variants": [
                {"name": name, "minimum_patch_ha": minimum, "simplification_tolerance_m": tolerance}
                for name, minimum, tolerance in VARIANTS
            ],
        },
        "protected_areas": _protected_metrics(),
        "riparian_context": _raster_role_metrics(
            "riparian_context", NMD_ROLE_CODES["riparian_context"]
        ),
        "semi_natural_habitat": _raster_role_metrics(
            "semi_natural_habitat", NMD_ROLE_CODES["semi_natural_habitat"]
        ),
        "candidate_agricultural_land": _candidate_metrics(),
    }
    output_path = OUTPUT_DIR / "map-layer-audit.json"
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
