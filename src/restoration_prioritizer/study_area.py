"""Download and dissolve the SCB DeSO 2025 study area for Skåne."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geopandas as gpd
from pyproj import CRS
from shapely import make_valid, union_all

from restoration_prioritizer.config import (
    COUNTY_CODE,
    NUTS3_CODE,
    STUDY_AREA_NAME,
    TARGET_CRS,
)

SCB_AUTHORITY = "Statistics Sweden / SCB"
SCB_PRODUCT = "DeSO 2025"
SCB_LAYER = "stat:DeSO_2025"
SCB_WFS_URL = "https://geodata.scb.se/geoserver/stat/wfs"
SCB_LICENSE = "CC0"
WFS_VERSION = "2.0.0"
HTTP_TIMEOUT_SECONDS = 60

RAW_PATH = Path("data/raw/scb/deso_2025_skane.geojson")
PROCESSED_PATH = Path("data/processed/study_area.gpkg")
PROVENANCE_PATH = Path("data/processed/study_area.provenance.json")
OUTPUT_LAYER = "study_area"
REQUIRED_SOURCE_FIELDS = frozenset({"lanskod", "version", "referensdatum"})

# Broad EPSG:3006 guards against an empty or national-scale result without
# pretending that a precise land-area statistic is an invariant of DeSO.
SKANE_BOUNDS = (250_000.0, 6_000_000.0, 650_000.0, 6_300_000.0)
SKANE_AREA_M2 = (5_000_000_000.0, 20_000_000_000.0)


class StudyAreaError(ValueError):
    """Raised when the source or resulting study-area geometry is unusable."""


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Paths and summary values produced by one study-area ingestion run."""

    raw_path: Path
    processed_path: Path
    provenance_path: Path
    source_feature_count: int
    output_feature_count: int
    geometry_type: str
    bounds: tuple[float, float, float, float]
    area_m2: float


def build_wfs_request_url() -> str:
    """Build the deterministic server-side filtered SCB WFS request."""

    parameters = (
        ("service", "WFS"),
        ("version", WFS_VERSION),
        ("request", "GetFeature"),
        ("typeNames", SCB_LAYER),
        ("outputFormat", "application/json"),
        ("srsName", TARGET_CRS),
        ("CQL_FILTER", f"lanskod='{COUNTY_CODE}'"),
    )
    return f"{SCB_WFS_URL}?{urlencode(parameters)}"


def fetch_source_response(raw_path: Path = RAW_PATH) -> tuple[bytes, dict[str, Any]]:
    """Fetch the filtered GeoJSON and persist the exact response bytes."""

    request_url = build_wfs_request_url()
    request = Request(request_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_bytes = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise StudyAreaError(f"SCB WFS request failed: {exc}") from exc

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(response_bytes)

    try:
        payload = json.loads(response_bytes)
    except json.JSONDecodeError as exc:
        raise StudyAreaError("SCB WFS returned a malformed JSON response") from exc

    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise StudyAreaError("SCB WFS response is not a GeoJSON FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list):
        raise StudyAreaError("SCB WFS GeoJSON response has no valid features array")
    if not features:
        raise StudyAreaError("SCB WFS returned zero features for Skåne county code 12")

    declared_crs = _declared_geojson_crs(payload)
    if declared_crs is not None and declared_crs != CRS.from_user_input(TARGET_CRS):
        raise StudyAreaError(
            f"SCB WFS returned unexpected CRS {declared_crs.to_string()}; expected {TARGET_CRS}"
        )
    return response_bytes, payload


def source_geodataframe(payload: dict[str, Any]) -> gpd.GeoDataFrame:
    """Convert the validated GeoJSON payload to a GeoDataFrame in EPSG:3006."""

    try:
        source = gpd.GeoDataFrame.from_features(payload["features"], crs=TARGET_CRS)
    except (KeyError, TypeError, ValueError) as exc:
        raise StudyAreaError(f"Could not parse SCB GeoJSON features: {exc}") from exc
    validate_source(source)
    return source


def validate_source(source: gpd.GeoDataFrame) -> None:
    """Validate the selected SCB records before dissolving them."""

    missing_fields = REQUIRED_SOURCE_FIELDS.difference(source.columns)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise StudyAreaError(f"SCB source is missing required fields: {missing}")
    if source.empty:
        raise StudyAreaError("SCB source returned zero features for Skåne county code 12")
    if source.crs is None:
        raise StudyAreaError(f"SCB source CRS is missing; expected {TARGET_CRS}")
    if CRS.from_user_input(source.crs) != CRS.from_user_input(TARGET_CRS):
        raise StudyAreaError(f"SCB source CRS is {source.crs}; expected {TARGET_CRS}")

    county_values = source["lanskod"].map(str)
    if county_values.isna().any() or not county_values.eq(COUNTY_CODE).all():
        unexpected = sorted(set(county_values) - {COUNTY_CODE})
        raise StudyAreaError(
            f"SCB source contains records outside county code {COUNTY_CODE}: {unexpected}"
        )
    if source.geometry.isna().any() or source.geometry.is_empty.any():
        raise StudyAreaError("SCB source contains missing or empty geometry")

    invalid = ~source.geometry.is_valid
    if invalid.any():
        source.loc[invalid, "geometry"] = source.loc[invalid, "geometry"].map(make_valid)
    if (~source.geometry.is_valid).any():
        raise StudyAreaError("SCB source contains geometry that could not be made valid")
    if not source.geometry.geom_type.isin({"Polygon", "MultiPolygon"}).all():
        raise StudyAreaError("SCB source contains non-polygon geometry")


def build_study_area(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Dissolve validated DeSO features into one metadata-bearing feature."""

    validate_source(source)
    dissolved = union_all(source.geometry.array)
    if dissolved.is_empty:
        raise StudyAreaError("Dissolved SCB study-area geometry is empty")
    if not dissolved.is_valid:
        dissolved = make_valid(dissolved)
    if dissolved.is_empty or not dissolved.is_valid:
        raise StudyAreaError("Dissolved SCB study-area geometry is invalid or empty")
    if dissolved.geom_type not in {"Polygon", "MultiPolygon"}:
        raise StudyAreaError(f"Dissolved SCB geometry is {dissolved.geom_type}, not polygonal")
    if dissolved.area <= 0:
        raise StudyAreaError("Dissolved SCB study-area geometry has non-positive area")

    output = gpd.GeoDataFrame(
        [
            {
                "study_area_name": STUDY_AREA_NAME,
                "county_code": COUNTY_CODE,
                "nuts3_code": NUTS3_CODE,
                "source_authority": SCB_AUTHORITY,
                "source_product": SCB_PRODUCT,
            }
        ],
        geometry=[dissolved],
        crs=TARGET_CRS,
    )
    validate_output(output)
    return output


def validate_output(output: gpd.GeoDataFrame) -> None:
    """Validate the one-feature processed study-area dataset."""

    if len(output) != 1:
        raise StudyAreaError(f"Processed study area has {len(output)} features; expected exactly 1")
    if output.crs is None or CRS.from_user_input(output.crs) != CRS.from_user_input(TARGET_CRS):
        raise StudyAreaError(f"Processed study-area CRS must be {TARGET_CRS}")
    geometry = output.geometry.iloc[0]
    if geometry is None or geometry.is_empty or not geometry.is_valid:
        raise StudyAreaError("Processed study-area geometry is missing, empty, or invalid")
    if geometry.area <= 0:
        raise StudyAreaError("Processed study-area geometry has non-positive area")


def validate_skane_plausibility(output: gpd.GeoDataFrame) -> None:
    """Apply deliberately broad bounds and area guards to the Skåne result."""

    validate_output(output)
    min_x, min_y, max_x, max_y = output.total_bounds
    bound_min_x, bound_min_y, bound_max_x, bound_max_y = SKANE_BOUNDS
    if not (
        bound_min_x <= min_x <= bound_max_x
        and bound_min_x <= max_x <= bound_max_x
        and bound_min_y <= min_y <= bound_max_y
        and bound_min_y <= max_y <= bound_max_y
    ):
        raise StudyAreaError(f"Processed bounds {output.total_bounds.tolist()} are not Skåne-like")
    area_m2 = float(output.geometry.iloc[0].area)
    if not SKANE_AREA_M2[0] <= area_m2 <= SKANE_AREA_M2[1]:
        raise StudyAreaError(f"Processed area {area_m2:.0f} m² is not Skåne-like")


def run_ingestion(
    raw_path: Path = RAW_PATH,
    processed_path: Path = PROCESSED_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> IngestionResult:
    """Fetch, validate, dissolve, write, and record the SCB study area."""

    response_bytes, payload = fetch_source_response(raw_path)
    source = source_geodataframe(payload)
    output = build_study_area(source)
    validate_skane_plausibility(output)

    processed_path.parent.mkdir(parents=True, exist_ok=True)
    if processed_path.exists():
        processed_path.unlink()
    output.to_file(processed_path, layer=OUTPUT_LAYER, driver="GPKG")

    bounds = tuple(float(value) for value in output.total_bounds)
    geometry = output.geometry.iloc[0]
    provenance = {
        "source_authority": SCB_AUTHORITY,
        "source_product": SCB_PRODUCT,
        "source_layer": SCB_LAYER,
        "source_endpoint": SCB_WFS_URL,
        "wfs_version": WFS_VERSION,
        "request_url": build_wfs_request_url(),
        "request_parameters": {
            "service": "WFS",
            "version": WFS_VERSION,
            "request": "GetFeature",
            "typeNames": SCB_LAYER,
            "outputFormat": "application/json",
            "srsName": TARGET_CRS,
            "CQL_FILTER": f"lanskod='{COUNTY_CODE}'",
        },
        "county_filter": {"field": "lanskod", "value": COUNTY_CODE},
        "source_crs": TARGET_CRS,
        "output_crs": TARGET_CRS,
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_feature_count": len(source),
        "output_feature_count": len(output),
        "output_format": "GeoPackage",
        "output_path": str(processed_path),
        "output_layer": OUTPUT_LAYER,
        "raw_response_path": str(raw_path),
        "raw_response_bytes": len(response_bytes),
        "observed_geometry_type": geometry.geom_type,
        "observed_bounds_epsg_3006": list(bounds),
        "observed_area_m2": float(geometry.area),
        "observed_area_km2": float(geometry.area / 1_000_000),
        "source_version_values": sorted({str(value) for value in source["version"]}),
        "source_reference_date_values": sorted({str(value) for value in source["referensdatum"]}),
        "license": SCB_LICENSE,
        "semantic_note": (
            "DeSO 2025 is nationally complete to the territorial-water boundary. "
            "This dissolved geometry is the administrative/statistical study extent, "
            "may include marine territory, and is not the terrestrial candidate-analysis mask."
        ),
    }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return IngestionResult(
        raw_path=raw_path,
        processed_path=processed_path,
        provenance_path=provenance_path,
        source_feature_count=len(source),
        output_feature_count=len(output),
        geometry_type=geometry.geom_type,
        bounds=bounds,
        area_m2=float(geometry.area),
    )


def _declared_geojson_crs(payload: dict[str, Any]) -> CRS | None:
    """Read the optional legacy GeoJSON CRS member when the server supplies it."""

    crs_member = payload.get("crs")
    if not isinstance(crs_member, dict):
        return None
    properties = crs_member.get("properties")
    if not isinstance(properties, dict):
        raise StudyAreaError("SCB GeoJSON CRS declaration is malformed")
    name = properties.get("name") or properties.get("href")
    if not isinstance(name, str):
        raise StudyAreaError("SCB GeoJSON CRS declaration has no usable name")
    try:
        return CRS.from_user_input(name)
    except (TypeError, ValueError) as exc:
        raise StudyAreaError(f"SCB GeoJSON declares an unreadable CRS: {name}") from exc


def main() -> None:
    """Run the source-specific ingestion command and print a compact summary."""

    result = run_ingestion()
    print(f"Fetched {result.source_feature_count} SCB DeSO features for Skåne")
    print(
        f"Wrote {result.output_feature_count} {result.geometry_type} feature to "
        f"{result.processed_path} ({result.area_m2 / 1_000_000:.1f} km²)"
    )
    print(f"Provenance: {result.provenance_path}")


if __name__ == "__main__":
    main()
