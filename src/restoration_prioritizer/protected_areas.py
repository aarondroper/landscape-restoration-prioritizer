"""Ingest and normalize the selected Naturvårdsverket protected-area network.

This module deliberately stops at a factual source/geometry foundation. It
does not calculate a Protected-Area Reinforcement score, choose a distance
threshold, or rank candidate units.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS
from rasterio.features import geometry_mask
from rasterio.windows import bounds as window_bounds, transform as window_transform
from shapely import make_valid, union_all
from shapely.geometry import GeometryCollection, box
from shapely.prepared import prep

from restoration_prioritizer.config import TARGET_CRS

AUTHORITY = "Naturvårdsverket"
NATIONAL_ENDPOINT = "https://geodata.naturvardsverket.se/naturvardsregistret/wfs"
NATIONAL_LAYER = "Naturvardsregistret_WFS:SkyddadeOmraden"
NATURA_ENDPOINT = "https://geodata.naturvardsverket.se/n2000/wfs"
NATURA_LAYER = "N2000_WFS:N2000"
WFS_VERSION = "2.0.0"
HTTP_TIMEOUT_SECONDS = 60
CONTEXT_BUFFER_M = 5_000.0
PAGE_SIZE = 500
TILE_GRID_SIZE = 2
TILE_OVERLAP_M = 1.0
LICENSE = "CC0"

STUDY_AREA_PATH = Path("data/processed/study_area.gpkg")
STUDY_AREA_LAYER = "study_area"
CANDIDATE_PATH = Path("data/processed/candidate_units.gpkg")
CANDIDATE_LAYER = "candidate_units"
NMD_PATH = Path("data/processed/nmd/nmd2023_v2_1_skane.tif")

RAW_DIRECTORY = Path("data/raw/protected_areas")
RAW_NATIONAL_PATH = RAW_DIRECTORY / "national_protection.gml"
RAW_NATURA_PATH = RAW_DIRECTORY / "natura2000.gml"
RAW_NATIONAL_PAGES = RAW_DIRECTORY / "national_protection_pages"
RAW_NATURA_PAGES = RAW_DIRECTORY / "natura2000_pages"
PROCESSED_PATH = Path("data/processed/protected_areas.gpkg")
PROVENANCE_PATH = Path("data/processed/protected_areas.provenance.json")
NATIONAL_OUTPUT_LAYER = "national_protection"
NATURA_OUTPUT_LAYER = "natura2000"
FOOTPRINT_OUTPUT_LAYER = "protected_footprint"

NATIONAL_REQUIRED_FIELDS = frozenset({"NVRID", "NAMN", "SKYDDSTYP", "BESLUTSSTATUS"})
NATURA_REQUIRED_FIELDS = frozenset({"OBJECTID", "OMRADESKOD", "OMRADESNAMN", "OMRADESTYP"})
NATIONAL_DESIGNATIONS = frozenset({"Nationalpark", "Naturreservat"})
NATURA_DESIGNATIONS = frozenset({"SCI", "SPA", "SPA/SCI"})
CURRENT_STATUS = "Gällande"
POLYGON_TYPES = frozenset({"Polygon", "MultiPolygon"})

NAMESPACE_WFS = "http://www.opengis.net/wfs/2.0"
NAMESPACE_OWS = "http://www.opengis.net/ows/1.1"
NAMESPACE_XSD = "http://www.w3.org/2001/XMLSchema"
NAMESPACE_GML = "http://www.opengis.net/gml/3.2"
XML_NAMESPACES = {
    "wfs": NAMESPACE_WFS,
    "ows": NAMESPACE_OWS,
    "xsd": NAMESPACE_XSD,
}


class ProtectedAreasError(RuntimeError):
    """Raised when the protected-area source or normalized output is unusable."""


@dataclass(frozen=True, slots=True)
class ServiceContract:
    """Live capabilities/schema facts needed by the ingestion run."""

    endpoint: str
    layer: str
    capabilities_version: str
    service_versions: tuple[str, ...]
    available_crs: tuple[str, ...]
    output_formats: tuple[str, ...]
    schema_fields: tuple[str, ...]
    geometry_type: str
    feature_id_examples: tuple[str, ...]
    geojson_supported: bool
    geojson_probe: str
    attribute_filter_supported: bool
    attribute_filter_probe: str


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Bounded WFS retrieval and raw-artifact facts."""

    source: gpd.GeoDataFrame
    raw_path: Path
    page_paths: tuple[Path, ...]
    source_feature_count: int
    request_parameters: dict[str, Any]
    page_count: int
    retrieval_timestamp_utc: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _parse_int(value: str | None, label: str) -> int:
    try:
        return int(value or "")
    except ValueError as exc:
        raise ProtectedAreasError(f"WFS response has no usable {label}: {value!r}") from exc


def _http_get(url: str, accept: str = "application/xml") -> bytes:
    request = Request(url, headers={"Accept": accept})
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ProtectedAreasError(f"WFS request failed for {url}: {exc}") from exc


def _service_url(endpoint: str, parameters: tuple[tuple[str, str], ...]) -> str:
    return f"{endpoint}?{urlencode(parameters)}"


def _capabilities_facts(endpoint: str, layer: str) -> tuple[ET.Element, dict[str, Any]]:
    url = _service_url(
        endpoint,
        (("service", "WFS"), ("version", WFS_VERSION), ("request", "GetCapabilities")),
    )
    try:
        root = ET.fromstring(_http_get(url))
    except ET.ParseError as exc:
        raise ProtectedAreasError(f"Capabilities response from {endpoint} is not XML") from exc

    feature_types = root.findall(".//wfs:FeatureType", XML_NAMESPACES)
    target = next(
        (
            feature_type
            for feature_type in feature_types
            if _text(feature_type.find("wfs:Name", XML_NAMESPACES)) == layer
        ),
        None,
    )
    if target is None:
        available = [_text(item.find("wfs:Name", XML_NAMESPACES)) for item in feature_types]
        raise ProtectedAreasError(f"WFS layer {layer} is missing; available layers: {available}")

    crs_values = []
    for element in ("DefaultCRS", "OtherCRS"):
        crs_values.extend(
            _text(value)
            for value in target.findall(f"wfs:{element}", XML_NAMESPACES)
            if _text(value)
        )
    output_formats = [
        _text(value)
        for value in target.findall("wfs:OutputFormats/wfs:Format", XML_NAMESPACES)
        if _text(value)
    ]
    service_versions = [
        _text(value)
        for value in root.findall(".//ows:ServiceTypeVersion", XML_NAMESPACES)
        if _text(value)
    ]
    return root, {
        "version": root.attrib.get("version", ""),
        "service_versions": tuple(dict.fromkeys(service_versions)),
        "available_crs": tuple(dict.fromkeys(crs_values)),
        "output_formats": tuple(dict.fromkeys(output_formats)),
    }


def _schema_facts(endpoint: str, layer: str) -> tuple[tuple[str, ...], str]:
    url = _service_url(
        endpoint,
        (
            ("service", "WFS"),
            ("version", WFS_VERSION),
            ("request", "DescribeFeatureType"),
            ("typeNames", layer),
        ),
    )
    try:
        root = ET.fromstring(_http_get(url))
    except ET.ParseError as exc:
        raise ProtectedAreasError(
            f"DescribeFeatureType response from {endpoint} is not XML"
        ) from exc
    fields = tuple(
        element.attrib["name"]
        for element in root.findall(".//xsd:complexType//xsd:element", XML_NAMESPACES)
        if "name" in element.attrib
    )
    geometry_elements = [
        element
        for element in root.findall(".//xsd:complexType//xsd:element", XML_NAMESPACES)
        if element.attrib.get("name", "").upper() in {"SHAPE", "GEOMETRY"}
    ]
    geometry_types = tuple(element.attrib.get("type", "") for element in geometry_elements)
    if not fields:
        raise ProtectedAreasError(f"DescribeFeatureType returned no fields for {layer}")
    geometry_type = ", ".join(geometry_types) or "not declared"
    return fields, geometry_type


def _probe_request(endpoint: str, layer: str, parameters: tuple[tuple[str, str], ...]) -> bytes:
    return _http_get(_service_url(endpoint, parameters))


def inspect_service(endpoint: str, layer: str, attribute_field: str) -> ServiceContract:
    """Inspect capabilities, schema, GeoJSON support, and attribute filtering."""

    _, capabilities = _capabilities_facts(endpoint, layer)
    schema_fields, geometry_type = _schema_facts(endpoint, layer)
    probe_base = (
        ("service", "WFS"),
        ("version", WFS_VERSION),
        ("request", "GetFeature"),
        ("typeNames", layer),
        ("resultType", "hits"),
        ("bbox", _bbox_parameter((317_000.0, 6_106_000.0, 486_500.0, 6_276_000.0))),
        ("count", "1"),
    )
    geojson_response = _probe_request(
        endpoint,
        layer,
        probe_base + (("outputFormat", "application/json"),),
    )
    geojson_text = geojson_response.decode("utf-8", errors="replace")
    geojson_supported = not ("ExceptionReport" in geojson_text or "not supported" in geojson_text)
    attribute_response = _probe_request(
        endpoint,
        layer,
        probe_base + (("CQL_FILTER", f"{attribute_field}='__NO_SUCH_VALUE__'"),),
    )
    attribute_text = attribute_response.decode("utf-8", errors="replace")
    attribute_match = re.search(r'numberMatched="(\d+)"', attribute_text)
    attribute_count = int(attribute_match.group(1)) if attribute_match else None
    attribute_filter_supported = attribute_count == 0
    return ServiceContract(
        endpoint=endpoint,
        layer=layer,
        capabilities_version=capabilities["version"],
        service_versions=capabilities["service_versions"],
        available_crs=capabilities["available_crs"],
        output_formats=capabilities["output_formats"],
        schema_fields=schema_fields,
        geometry_type=geometry_type,
        feature_id_examples=(),
        geojson_supported=geojson_supported,
        geojson_probe=(
            "application/json accepted"
            if geojson_supported
            else "application/json rejected; XML/GML used"
        ),
        attribute_filter_supported=attribute_filter_supported,
        attribute_filter_probe=(
            f"CQL_FILTER impossible-value probe matched {attribute_count} records"
            if attribute_count is not None
            else "CQL_FILTER probe returned no match count"
        ),
    )


def _bbox_parameter(bounds: tuple[float, float, float, float]) -> str:
    """Return the server-required EPSG:3006 latitude-first WFS 2.0 BBOX."""

    min_x, min_y, max_x, max_y = bounds
    return f"{min_y:.3f},{min_x:.3f},{max_y:.3f},{max_x:.3f},EPSG:3006"


def build_get_feature_parameters(
    endpoint: str,
    layer: str,
    bounds: tuple[float, float, float, float],
    start_index: int = 0,
    count: int = PAGE_SIZE,
) -> tuple[tuple[str, str], ...]:
    """Build a deterministic BBOX-only WFS request."""

    parameters = (
        ("service", "WFS"),
        ("version", WFS_VERSION),
        ("request", "GetFeature"),
        ("typeNames", layer),
        ("resultType", "results"),
        ("srsName", TARGET_CRS),
        ("bbox", _bbox_parameter(bounds)),
        ("count", str(count)),
    )
    if start_index:
        parameters += (("startIndex", str(start_index)),)
    return parameters


def _members_and_counts(response: bytes) -> tuple[ET.Element, list[ET.Element], int, int]:
    try:
        root = ET.fromstring(response)
    except ET.ParseError as exc:
        raise ProtectedAreasError("WFS GetFeature response is not well-formed XML") from exc
    if _local_name(root.tag) == "ExceptionReport":
        message = " ".join(text.strip() for text in root.itertext() if text.strip())
        raise ProtectedAreasError(f"WFS GetFeature returned an exception: {message}")
    # The Naturvårdsverket service normally places members directly under the
    # FeatureCollection, but occasional responses have an extra collection
    # wrapper. Descendant matching handles both without changing feature XML.
    members = [child for child in root.iter() if _local_name(child.tag) == "member"]
    matched = _parse_int(root.attrib.get("numberMatched"), "numberMatched")
    returned = _parse_int(root.attrib.get("numberReturned"), "numberReturned")
    if returned != len(members):
        raise ProtectedAreasError(
            f"WFS declared {returned} returned features but contained {len(members)} members"
        )
    return root, members, matched, returned


def _member_identifier(member: ET.Element) -> str:
    for child in member.iter():
        if "id" in child.attrib:
            return str(child.attrib["id"])
    return ET.tostring(member, encoding="unicode")


def _combine_gml_pages(pages: list[bytes], expected_count: int, deduplicate: bool = False) -> bytes:
    first_root, _, _, _ = _members_and_counts(pages[0])
    members_by_id: dict[str, ET.Element] = {}
    members: list[ET.Element] = []
    for page in pages:
        _, page_members, _, _ = _members_and_counts(page)
        for member in page_members:
            key = _member_identifier(member)
            if not deduplicate or key not in members_by_id:
                members_by_id[key] = copy.deepcopy(member)
                members.append(members_by_id[key])
    if len(members) != expected_count:
        raise ProtectedAreasError(
            f"Tiled WFS retrieval collected {len(members)} of expected {expected_count} unique features"
        )
    first_root[:] = members
    first_root.attrib["numberReturned"] = str(len(members))
    first_root.attrib["numberMatched"] = str(expected_count)
    first_root.attrib.pop("previous", None)
    first_root.attrib.pop("next", None)
    return ET.tostring(first_root, encoding="utf-8", xml_declaration=True)


def fetch_tiled_source(
    endpoint: str,
    layer: str,
    bounds: tuple[float, float, float, float],
    raw_path: Path,
    pages_directory: Path,
) -> FetchResult:
    """Fetch exact-context BBOX tiles and preserve raw page/combined artifacts."""

    pages: list[bytes] = []
    page_paths: list[Path] = []
    tile_counts: list[int] = []
    min_x, min_y, max_x, max_y = bounds
    tile_width = (max_x - min_x) / TILE_GRID_SIZE
    tile_height = (max_y - min_y) / TILE_GRID_SIZE
    for tile_row in range(TILE_GRID_SIZE):
        for tile_col in range(TILE_GRID_SIZE):
            tile_bounds = (
                max(min_x, min_x + tile_col * tile_width - TILE_OVERLAP_M),
                max(min_y, min_y + tile_row * tile_height - TILE_OVERLAP_M),
                min(max_x, min_x + (tile_col + 1) * tile_width + TILE_OVERLAP_M),
                min(max_y, min_y + (tile_row + 1) * tile_height + TILE_OVERLAP_M),
            )
            parameters = build_get_feature_parameters(
                endpoint, layer, tile_bounds, start_index=0, count=PAGE_SIZE
            )
            response = _http_get(_service_url(endpoint, parameters), accept="application/gml+xml")
            _, members, matched, returned = _members_and_counts(response)
            if matched != returned or returned != len(members):
                raise ProtectedAreasError(
                    f"WFS tile {tile_col},{tile_row} returned {returned} of {matched} records"
                )
            pages.append(response)
            tile_counts.append(matched)

    if not pages or sum(tile_counts) == 0:
        raise ProtectedAreasError(f"WFS layer {layer} returned no features in the context BBOX")
    unique_ids = {
        _member_identifier(member) for page in pages for member in _members_and_counts(page)[1]
    }
    pages_directory.mkdir(parents=True, exist_ok=True)
    for old_page in pages_directory.glob("*.gml"):
        old_page.unlink()
    for index, response in enumerate(pages):
        page_path = pages_directory / f"tile_{index:04d}.gml"
        page_path.write_bytes(response)
        page_paths.append(page_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(_combine_gml_pages(pages, len(unique_ids), deduplicate=True))
    try:
        source = gpd.read_file(raw_path)
    except (OSError, ValueError, IndexError) as exc:
        raise ProtectedAreasError(
            f"Could not parse preserved WFS GML at {raw_path}: {exc}"
        ) from exc
    if len(source) != len(unique_ids):
        raise ProtectedAreasError(
            f"Parsed {len(source)} unique features from WFS response; expected {len(unique_ids)}"
        )
    feature_id_values = (
        source["gml_id"].head(3).tolist()
        if "gml_id" in source
        else source.index.astype(str).tolist()[:3]
    )
    feature_id_examples = tuple(str(value) for value in feature_id_values)
    source.attrs["feature_id_examples"] = feature_id_examples
    return FetchResult(
        source=source,
        raw_path=raw_path,
        page_paths=tuple(page_paths),
        source_feature_count=len(unique_ids),
        request_parameters={
            "service": "WFS",
            "version": WFS_VERSION,
            "request": "GetFeature",
            "typeNames": layer,
            "resultType": "results",
            "srsName": TARGET_CRS,
            "bbox": _bbox_parameter(bounds),
            "bbox_axis_order": "EPSG:3006 northing,easting,northing,easting",
            "count": PAGE_SIZE,
            "tiling": {
                "grid": f"{TILE_GRID_SIZE}x{TILE_GRID_SIZE}",
                "tile_overlap_m": TILE_OVERLAP_M,
                "tile_match_counts": tile_counts,
                "unique_record_count_after_tile_deduplication": len(unique_ids),
            },
            "attribute_filter": None,
        },
        page_count=len(pages),
        retrieval_timestamp_utc=_utc_now(),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _polygonal_parts(geometry: Any) -> Any:
    if geometry.geom_type in POLYGON_TYPES:
        return geometry
    if isinstance(geometry, GeometryCollection):
        parts = [part for part in geometry.geoms if part.geom_type in POLYGON_TYPES]
        if not parts:
            return GeometryCollection()
        return union_all(parts)
    return GeometryCollection()


def validate_and_repair_source(
    source: gpd.GeoDataFrame,
    required_fields: frozenset[str],
    source_id_field: str,
    label: str,
    require_unique_id: bool = False,
) -> tuple[gpd.GeoDataFrame, int]:
    """Validate polygon source geometry/IDs and safely repair invalid features."""

    if not isinstance(source, gpd.GeoDataFrame):
        raise ProtectedAreasError(f"{label} source must be a GeoDataFrame")
    missing = sorted(required_fields.difference(source.columns))
    if missing:
        raise ProtectedAreasError(f"{label} source is missing fields: {', '.join(missing)}")
    if source.crs is None or CRS.from_user_input(source.crs) != CRS.from_user_input(TARGET_CRS):
        raise ProtectedAreasError(f"{label} source CRS is {source.crs}; expected {TARGET_CRS}")
    if source[source_id_field].isna().any() or source[source_id_field].astype(str).eq("").any():
        raise ProtectedAreasError(f"{label} source has null/empty {source_id_field}")
    if require_unique_id and source[source_id_field].duplicated().any():
        raise ProtectedAreasError(f"{label} source {source_id_field} values are not unique")
    if source.geometry.isna().any() or source.geometry.is_empty.any():
        raise ProtectedAreasError(f"{label} source contains null or empty geometry")
    if not source.geometry.geom_type.isin(POLYGON_TYPES).all():
        unexpected = sorted(set(source.geometry.geom_type) - POLYGON_TYPES)
        raise ProtectedAreasError(f"{label} source contains non-polygon geometry: {unexpected}")

    repaired = 0
    for index in source.index[source.geometry.is_valid.eq(False)]:
        repaired_geometry = _polygonal_parts(make_valid(source.at[index, source.geometry.name]))
        if repaired_geometry.is_empty:
            raise ProtectedAreasError(f"{label} invalid geometry {index} has no polygonal part")
        source.at[index, source.geometry.name] = repaired_geometry
        repaired += 1
    if source.geometry.is_empty.any() or (~source.geometry.is_valid).any():
        raise ProtectedAreasError(f"{label} contains geometry that could not be made valid")
    if not source.geometry.geom_type.isin(POLYGON_TYPES).all():
        raise ProtectedAreasError(f"{label} geometry repair produced non-polygonal output")
    return source, repaired


def filter_to_context(source: gpd.GeoDataFrame, context_geometry: Any) -> gpd.GeoDataFrame:
    """Apply the final local exact intersection filter after the WFS BBOX."""

    if context_geometry is None or context_geometry.is_empty:
        raise ProtectedAreasError("Protected-area context geometry must be non-empty")
    selected = source.loc[source.geometry.intersects(context_geometry)].copy()
    if selected.empty:
        raise ProtectedAreasError(
            "No source features intersect the exact retained context geometry"
        )
    return selected.reset_index(drop=True)


def filter_national_records(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Select only current national parks and nature reserves."""

    mask = source["SKYDDSTYP"].isin(NATIONAL_DESIGNATIONS) & source["BESLUTSSTATUS"].eq(
        CURRENT_STATUS
    )
    selected = source.loc[mask].copy()
    if selected.empty:
        raise ProtectedAreasError("No current national parks or nature reserves were selected")
    return selected.reset_index(drop=True)


def filter_natura_records(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Select all eligible SCI, SPA, and combined SPA/SCI site records."""

    selected = source.loc[source["OMRADESTYP"].isin(NATURA_DESIGNATIONS)].copy()
    if selected.empty:
        raise ProtectedAreasError("No SCI, SPA, or SPA/SCI Natura 2000 records were selected")
    return selected.reset_index(drop=True)


def normalize_national(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Create the small normalized national-protection layer."""

    result = gpd.GeoDataFrame(
        {
            "source_id": source["NVRID"].astype(str),
            "name": source["NAMN"].astype(str),
            "designation": source["SKYDDSTYP"].astype(str),
            "source_status": source["BESLUTSSTATUS"].astype(str),
            "source_authority": AUTHORITY,
            "geometry": source.geometry.array,
        },
        geometry="geometry",
        crs=TARGET_CRS,
    )
    required = {"source_id", "name", "designation", "source_status", "source_authority", "geometry"}
    _validate_normalized(result, required, NATIONAL_DESIGNATIONS, {CURRENT_STATUS})
    return result


def normalize_natura(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Create the small normalized Natura 2000 layer."""

    result = gpd.GeoDataFrame(
        {
            "source_id": source["OMRADESKOD"].astype(str),
            "name": source["OMRADESNAMN"].astype(str),
            "designation": source["OMRADESTYP"].astype(str),
            "source_authority": AUTHORITY,
            "geometry": source.geometry.array,
        },
        geometry="geometry",
        crs=TARGET_CRS,
    )
    required = {"source_id", "name", "designation", "source_authority", "geometry"}
    _validate_normalized(result, required, NATURA_DESIGNATIONS, set())
    return result


def _validate_normalized(
    result: gpd.GeoDataFrame,
    required: set[str],
    designations: frozenset[str],
    statuses: set[str],
) -> None:
    if not required.issubset(result.columns):
        raise ProtectedAreasError("Normalized layer is missing required fields")
    if result.crs is None or CRS.from_user_input(result.crs) != CRS.from_user_input(TARGET_CRS):
        raise ProtectedAreasError("Normalized layer CRS is not EPSG:3006")
    if result.empty or result.geometry.isna().any() or result.geometry.is_empty.any():
        raise ProtectedAreasError("Normalized layer has no usable geometry")
    if not result.geometry.geom_type.isin(POLYGON_TYPES).all():
        raise ProtectedAreasError("Normalized layer contains non-polygon geometry")
    if result["source_id"].duplicated().any():
        raise ProtectedAreasError("Normalized source IDs are not unique")
    if not set(result["designation"]).issubset(designations):
        raise ProtectedAreasError("Normalized layer contains an unapproved designation")
    if statuses and not set(result["source_status"]).issubset(statuses):
        raise ProtectedAreasError("Normalized layer contains an unapproved status")


def union_protected_geometries(
    national: gpd.GeoDataFrame, natura: gpd.GeoDataFrame
) -> tuple[Any, dict[str, float]]:
    """Union selected physical areas and return overlap/deduplication areas."""

    national_union = union_all(national.geometry.array)
    natura_union = union_all(natura.geometry.array)
    final_union = union_all([national_union, natura_union])
    if not final_union.is_valid:
        final_union = make_valid(final_union)
    final_union = _polygonal_parts(final_union)
    if final_union.is_empty or not final_union.is_valid:
        raise ProtectedAreasError("Protected footprint union is empty or invalid")
    if final_union.geom_type not in POLYGON_TYPES:
        raise ProtectedAreasError(
            f"Protected footprint union is {final_union.geom_type}, not polygonal"
        )
    summed_national = float(national.geometry.area.sum())
    summed_natura = float(natura.geometry.area.sum())
    national_area = float(national_union.area)
    natura_area = float(natura_union.area)
    intersection_area = float(national_union.intersection(natura_union).area)
    final_area = float(final_union.area)
    if final_area <= 0 or national_area <= 0 or natura_area <= 0:
        raise ProtectedAreasError("Protected union areas must be positive")
    if national_area > summed_national + 1e-6 or natura_area > summed_natura + 1e-6:
        raise ProtectedAreasError("A source union area exceeds its summed feature area")
    expected = national_area + natura_area - intersection_area
    if not math.isclose(final_area, expected, rel_tol=0, abs_tol=max(1e-6, expected * 1e-9)):
        raise ProtectedAreasError("Protected union arithmetic does not reconcile")
    return final_union, {
        "national_summed_feature_area_m2": summed_national,
        "national_union_area_m2": national_area,
        "natura_summed_feature_area_m2": summed_natura,
        "natura_union_area_m2": natura_area,
        "national_natura_intersection_area_m2": intersection_area,
        "final_union_area_m2": final_area,
        "deduplicated_area_m2": summed_national + summed_natura - final_area,
        "union_arithmetic_difference_m2": final_area - expected,
    }


def _distribution(values: Any) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return {
            key: None for key in ("n", "min", "p10", "p25", "median", "p75", "p90", "p95", "max")
        }
    quantiles = np.quantile(array, [0.10, 0.25, 0.50, 0.75, 0.90, 0.95])
    return {
        "n": int(array.size),
        "min": float(array.min()),
        "p10": float(quantiles[0]),
        "p25": float(quantiles[1]),
        "median": float(quantiles[2]),
        "p75": float(quantiles[3]),
        "p90": float(quantiles[4]),
        "p95": float(quantiles[5]),
        "max": float(array.max()),
    }


def candidate_diagnostics(candidates: gpd.GeoDataFrame, footprint: Any) -> dict[str, Any]:
    """Calculate factual candidate overlap and distance diagnostics only."""

    if candidates.empty:
        raise ProtectedAreasError("Candidate layer is empty")
    if candidates.crs is None or CRS.from_user_input(candidates.crs) != CRS.from_user_input(
        TARGET_CRS
    ):
        raise ProtectedAreasError("Candidate layer CRS must be EPSG:3006")
    if candidates.geometry.isna().any() or candidates.geometry.is_empty.any():
        raise ProtectedAreasError("Candidate geometry is null or empty")
    if (~candidates.geometry.is_valid).any():
        raise ProtectedAreasError("Candidate geometry is invalid")
    geometry_distances = candidates.geometry.distance(footprint).to_numpy(dtype=float)
    centroids = candidates.geometry.centroid
    centroid_distances = centroids.distance(footprint).to_numpy(dtype=float)
    intersects = candidates.geometry.intersects(footprint)
    centroid_inside = centroids.within(footprint)
    overlap = candidates.loc[intersects].copy()
    overlap_areas = overlap.geometry.intersection(footprint).area.to_numpy(dtype=float)
    overlap_fractions = overlap_areas / overlap.geometry.area.to_numpy(dtype=float)
    result: dict[str, Any] = {
        "candidate_count": int(len(candidates)),
        "candidate_intersect_count": int(intersects.sum()),
        "candidate_centroid_inside_count": int(centroid_inside.sum()),
        "geometry_to_geometry_minimum_distance_m": _distribution(geometry_distances),
        "centroid_to_footprint_distance_m": _distribution(centroid_distances),
        "overlap_candidates": {
            "count": int(len(overlap)),
            "intersection_area_m2_sum": float(overlap_areas.sum()),
            "protected_fraction_of_candidate_geometry": _distribution(overlap_fractions),
        },
    }
    if "candidate_fraction_of_terrestrial" in overlap:
        result["overlap_candidates"]["candidate_arable_fraction_of_terrestrial"] = _distribution(
            overlap["candidate_fraction_of_terrestrial"].to_numpy(dtype=float)
        )
    else:
        result["overlap_candidates"]["candidate_arable_fraction_of_terrestrial"] = None
    return result


def _read_study_geometry(path: Path = STUDY_AREA_PATH) -> Any:
    study = gpd.read_file(path, layer=STUDY_AREA_LAYER)
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise ProtectedAreasError("Study area is empty or has missing geometry")
    if study.crs is None or CRS.from_user_input(study.crs) != CRS.from_user_input(TARGET_CRS):
        raise ProtectedAreasError("Study area CRS must be EPSG:3006")
    geometry = union_all(study.geometry.array)
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.is_empty or geometry.geom_type not in POLYGON_TYPES:
        raise ProtectedAreasError("Study area is not a valid polygonal geometry")
    return geometry


def _read_candidates(path: Path = CANDIDATE_PATH) -> gpd.GeoDataFrame:
    candidates = gpd.read_file(path, layer=CANDIDATE_LAYER)
    if "hex_id" not in candidates.columns or candidates["hex_id"].duplicated().any():
        raise ProtectedAreasError("Candidate layer must have unique hex_id values")
    return candidates


def _nmd_water_audit(footprint: Any) -> dict[str, Any]:
    """Estimate protected-footprint overlap with existing NMD water/sea pixels."""

    if not NMD_PATH.exists():
        return {"available": False, "reason": f"Missing {NMD_PATH}"}
    with rasterio.open(NMD_PATH) as dataset:
        raster_extent = box(*dataset.bounds)
        in_extent = footprint.intersection(raster_extent)
        if in_extent.is_empty:
            return {"available": True, "footprint_in_nmd_extent_area_m2": 0.0}
        prepared = prep(in_extent)
        counts = Counter()
        for _, window in dataset.block_windows(1):
            block = box(*window_bounds(window, dataset.transform))
            if not prepared.intersects(block):
                continue
            data = dataset.read(1, window=window)
            mask = geometry_mask(
                [in_extent],
                out_shape=data.shape,
                transform=window_transform(window, dataset.transform),
                invert=True,
                all_touched=False,
            )
            values = data[mask]
            counts.update(int(value) for value in values)
        pixel_area = abs(float(dataset.transform.a * dataset.transform.e))
    sea_pixels = counts.get(62, 0)
    inland_water_pixels = counts.get(61, 0)
    footprint_pixels = sum(counts.values())
    area = float(in_extent.area)
    return {
        "available": True,
        "method": "NMD pixel-center mask within EPSG:3006 raster extent; diagnostic only",
        "nmd_raster_path": str(NMD_PATH),
        "footprint_in_nmd_extent_area_m2": area,
        "nmd_pixels_intersecting_footprint": footprint_pixels,
        "nmd_sea_area_m2": float(sea_pixels * pixel_area),
        "nmd_inland_water_area_m2": float(inland_water_pixels * pixel_area),
        "nmd_sea_and_inland_water_area_m2": float((sea_pixels + inland_water_pixels) * pixel_area),
        "nmd_sea_fraction_of_footprint_in_nmd_extent": float(sea_pixels / footprint_pixels)
        if footprint_pixels
        else None,
        "nmd_inland_water_fraction_of_footprint_in_nmd_extent": float(
            inland_water_pixels / footprint_pixels
        )
        if footprint_pixels
        else None,
    }


def _context_extent_audit(
    national: gpd.GeoDataFrame,
    natura: gpd.GeoDataFrame,
    study_geometry: Any,
    context_geometry: Any,
    footprint: Any,
) -> dict[str, Any]:
    all_selected = gpd.GeoDataFrame(
        geometry=list(national.geometry) + list(natura.geometry), crs=TARGET_CRS
    )
    in_study = all_selected.geometry.intersects(study_geometry)
    in_context = all_selected.geometry.intersects(context_geometry)
    footprint_in_study = footprint.intersection(study_geometry)
    footprint_in_context = footprint.intersection(context_geometry)
    footprint_outside = footprint_in_context.difference(study_geometry)
    return {
        "selected_features_intersecting_skane_proper": int(in_study.sum()),
        "selected_features_only_in_outside_5km_context": int((in_context & ~in_study).sum()),
        "selected_features_intersecting_retained_context": int(in_context.sum()),
        "final_footprint_area_inside_skane_m2": float(footprint_in_study.area),
        "final_footprint_area_outside_skane_within_5km_context_m2": float(footprint_outside.area),
        "final_footprint_area_outside_retained_context_m2": float(
            footprint.difference(context_geometry).area
        ),
    }


def _service_contract_dict(contract: ServiceContract) -> dict[str, Any]:
    return {
        "endpoint": contract.endpoint,
        "layer": contract.layer,
        "capabilities_version": contract.capabilities_version,
        "supported_wfs_versions": list(contract.service_versions),
        "available_crs": list(contract.available_crs),
        "advertised_output_formats": list(contract.output_formats),
        "describe_feature_type_fields": list(contract.schema_fields),
        "declared_geometry_type": contract.geometry_type,
        "feature_identifier_observation": list(contract.feature_id_examples),
        "geojson_supported": contract.geojson_supported,
        "geojson_probe": contract.geojson_probe,
        "attribute_filter_supported": contract.attribute_filter_supported,
        "attribute_filter_probe": contract.attribute_filter_probe,
    }


def _update_contract_ids(
    contract: ServiceContract, source: gpd.GeoDataFrame, source_id_field: str
) -> ServiceContract:
    values = (
        source[source_id_field].head(3).tolist()
        if source_id_field in source
        else source.index.astype(str).tolist()[:3]
    )
    return ServiceContract(
        endpoint=contract.endpoint,
        layer=contract.layer,
        capabilities_version=contract.capabilities_version,
        service_versions=contract.service_versions,
        available_crs=contract.available_crs,
        output_formats=contract.output_formats,
        schema_fields=contract.schema_fields,
        geometry_type=contract.geometry_type,
        feature_id_examples=tuple(str(value) for value in values),
        geojson_supported=contract.geojson_supported,
        geojson_probe=contract.geojson_probe,
        attribute_filter_supported=contract.attribute_filter_supported,
        attribute_filter_probe=contract.attribute_filter_probe,
    )


def run_ingestion(
    study_area_path: Path = STUDY_AREA_PATH,
    candidate_path: Path = CANDIDATE_PATH,
    raw_national_path: Path = RAW_NATIONAL_PATH,
    raw_natura_path: Path = RAW_NATURA_PATH,
    processed_path: Path = PROCESSED_PATH,
    provenance_path: Path = PROVENANCE_PATH,
) -> dict[str, Any]:
    """Fetch, normalize, union, audit, write, and record protected areas."""

    study_geometry = _read_study_geometry(study_area_path)
    context_geometry = study_geometry.buffer(CONTEXT_BUFFER_M)
    context_bounds = tuple(float(value) for value in context_geometry.bounds)

    national_contract = inspect_service(NATIONAL_ENDPOINT, NATIONAL_LAYER, "SKYDDSTYP")
    natura_contract = inspect_service(NATURA_ENDPOINT, NATURA_LAYER, "OMRADESTYP")
    national_fetch = fetch_tiled_source(
        NATIONAL_ENDPOINT,
        NATIONAL_LAYER,
        context_bounds,
        raw_national_path,
        raw_national_path.parent / f"{raw_national_path.stem}_pages",
    )
    natura_fetch = fetch_tiled_source(
        NATURA_ENDPOINT,
        NATURA_LAYER,
        context_bounds,
        raw_natura_path,
        raw_natura_path.parent / f"{raw_natura_path.stem}_pages",
    )
    national_contract = _update_contract_ids(national_contract, national_fetch.source, "NVRID")
    natura_contract = _update_contract_ids(natura_contract, natura_fetch.source, "OMRADESKOD")

    national_source, national_repaired = validate_and_repair_source(
        national_fetch.source, NATIONAL_REQUIRED_FIELDS, "NVRID", "National protection"
    )
    natura_source, natura_repaired = validate_and_repair_source(
        natura_fetch.source, NATURA_REQUIRED_FIELDS, "OMRADESKOD", "Natura 2000"
    )
    national_context = filter_to_context(national_source, context_geometry)
    natura_context = filter_to_context(natura_source, context_geometry)
    national_selected = filter_national_records(national_context)
    natura_selected = filter_natura_records(natura_context)
    national = normalize_national(national_selected)
    natura = normalize_natura(natura_selected)
    footprint, union_audit = union_protected_geometries(national, natura)

    candidates = _read_candidates(candidate_path)
    candidate_audit = candidate_diagnostics(candidates, footprint)
    context_audit = _context_extent_audit(
        national, natura, study_geometry, context_geometry, footprint
    )
    nmd_audit = _nmd_water_audit(footprint)
    footprint_inside_nmd = (
        nmd_audit.get("footprint_in_nmd_extent_area_m2") if nmd_audit.get("available") else None
    )
    marine_fraction = nmd_audit.get("nmd_sea_fraction_of_footprint_in_nmd_extent")
    marine_dominance = {
        "assessment": "diagnostic only; no marine clipping applied",
        "sea_fraction_of_nmd_extent_overlap": marine_fraction,
        "clearly_dominant_by_simple_half_area_check": bool(
            marine_fraction is not None and marine_fraction > 0.5
        ),
    }

    footprint_output = gpd.GeoDataFrame(
        [
            {
                "source_authority": AUTHORITY,
                "designation_scope": "Nationalpark/Naturreservat + Natura 2000 SCI/SPA/SPA/SCI",
                "geometry": footprint,
            }
        ],
        geometry="geometry",
        crs=TARGET_CRS,
    )
    if (
        footprint_output.geometry.iloc[0].area <= 0
        or not footprint_output.geometry.iloc[0].is_valid
    ):
        raise ProtectedAreasError("Protected-footprint output is invalid")
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    if processed_path.exists():
        processed_path.unlink()
    national.to_file(processed_path, layer=NATIONAL_OUTPUT_LAYER, driver="GPKG")
    natura.to_file(processed_path, layer=NATURA_OUTPUT_LAYER, driver="GPKG")
    footprint_output.to_file(processed_path, layer=FOOTPRINT_OUTPUT_LAYER, driver="GPKG")

    provenance = {
        "source_authority": AUTHORITY,
        "sources": {
            "national_protection": _service_contract_dict(national_contract),
            "natura2000": _service_contract_dict(natura_contract),
        },
        "live_wfs_versions": {
            "national_protection": list(national_contract.service_versions),
            "natura2000": list(natura_contract.service_versions),
        },
        "layers": {
            "national_source": NATIONAL_LAYER,
            "natura_source": NATURA_LAYER,
            "national_output": NATIONAL_OUTPUT_LAYER,
            "natura_output": NATURA_OUTPUT_LAYER,
            "footprint_output": FOOTPRINT_OUTPUT_LAYER,
        },
        "selection_rules": {
            "national_designation_field": "SKYDDSTYP",
            "national_designation_values": sorted(NATIONAL_DESIGNATIONS),
            "national_status_field": "BESLUTSSTATUS",
            "national_status_values": [CURRENT_STATUS],
            "natura_designation_field": "OMRADESTYP",
            "natura_designation_values": sorted(NATURA_DESIGNATIONS),
            "natura_status": "No separate legal/status field exposed; formal type values selected without an invented status filter",
        },
        "retrieval": {
            "retrieval_timestamp_utc": _utc_now(),
            "context_source": str(study_area_path),
            "context_crs": TARGET_CRS,
            "study_geometry_bounds_epsg_3006": [float(value) for value in study_geometry.bounds],
            "acquisition_buffer_m": CONTEXT_BUFFER_M,
            "context_bounds_epsg_3006": list(context_bounds),
            "bbox_final_local_filter": "Server BBOX uses EPSG:3006 northing,easting axis order; local exact intersects(buffered study geometry) is authoritative",
            "attribute_filter_strategy": "No server-side attribute filter; live CQL/FES impossible-value probes were ignored by both services; designation/status filters are local",
            "national": {
                "request_parameters": national_fetch.request_parameters,
                "page_count": national_fetch.page_count,
                "raw_response_path": str(national_fetch.raw_path),
                "raw_page_paths": [str(path) for path in national_fetch.page_paths],
                "raw_combined_bytes": national_fetch.raw_path.stat().st_size,
                "raw_page_bytes_total": sum(
                    path.stat().st_size for path in national_fetch.page_paths
                ),
            },
            "natura2000": {
                "request_parameters": natura_fetch.request_parameters,
                "page_count": natura_fetch.page_count,
                "raw_response_path": str(natura_fetch.raw_path),
                "raw_page_paths": [str(path) for path in natura_fetch.page_paths],
                "raw_combined_bytes": natura_fetch.raw_path.stat().st_size,
                "raw_page_bytes_total": sum(
                    path.stat().st_size for path in natura_fetch.page_paths
                ),
            },
        },
        "source_feature_counts": {
            "national_bbox_records": national_fetch.source_feature_count,
            "national_bbox_duplicate_nvrid_records": int(
                national_fetch.source["NVRID"].duplicated().sum()
            ),
            "national_exact_context_records": len(national_context),
            "national_selected_records": len(national_selected),
            "natura_bbox_records": natura_fetch.source_feature_count,
            "natura_bbox_duplicate_omradeskod_records": int(
                natura_fetch.source["OMRADESKOD"].duplicated().sum()
            ),
            "natura_exact_context_records": len(natura_context),
            "natura_selected_records": len(natura_selected),
        },
        "normalized_counts": {
            "national_protection": len(national),
            "natura2000": len(natura),
            "protected_footprint": len(footprint_output),
            "national_parks": int((national["designation"] == "Nationalpark").sum()),
            "nature_reserves": int((national["designation"] == "Naturreservat").sum()),
            "natura_sci": int((natura["designation"] == "SCI").sum()),
            "natura_spa": int((natura["designation"] == "SPA").sum()),
            "natura_combined_spa_sci": int((natura["designation"] == "SPA/SCI").sum()),
        },
        "geometry_validation": {
            "national_source_repaired": national_repaired,
            "natura_source_repaired": natura_repaired,
            "national_normalized_repaired": 0,
            "natura_normalized_repaired": 0,
            "output_crs": TARGET_CRS,
            "source_geometry_contract": "GML MultiSurfacePropertyType normalized to polygonal Polygon/MultiPolygon",
        },
        "areas_m2": union_audit,
        "context_extent_audit": context_audit,
        "candidate_relationship_diagnostics": candidate_audit,
        "marine_protection_audit": nmd_audit,
        "marine_dominance_diagnostic": marine_dominance,
        "footprint_geometry": {
            "geometry_type": footprint.geom_type,
            "feature_count": len(footprint_output),
            "area_m2": float(footprint.area),
            "natural_multipolygon_preserved": footprint.geom_type in POLYGON_TYPES,
            "buffered_or_simplified": False,
            "physical_union": True,
            "area_leq_input_sum": footprint.area
            <= union_audit["national_summed_feature_area_m2"]
            + union_audit["natura_summed_feature_area_m2"]
            + 1e-6,
        },
        "license": LICENSE,
        "output": {
            "processed_path": str(processed_path),
            "layers": [NATIONAL_OUTPUT_LAYER, NATURA_OUTPUT_LAYER, FOOTPRINT_OUTPUT_LAYER],
            "provenance_path": str(provenance_path),
            "nmd_overlap_area_m2_available": footprint_inside_nmd,
        },
        "caveats": [
            "The 5,000 m buffer is source-context acquisition only, not a future score distance threshold.",
            "The service exposes GML/XML rather than application/json; exact paged GML responses are preserved and assembled into valid combined GML artifacts.",
            "Natura 2000 exposes designation type and dates but no independent current-status field; no status semantics were invented.",
            "Marine and inland-water protected geometry is preserved; NMD overlap is an approximate diagnostic and no water clipping was applied.",
            "Candidate distances and overlaps are factual diagnostics only; no threshold, score, ranking, or candidate exclusion is implemented.",
        ],
    }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return provenance


def main() -> None:
    """Run the protected-area ingestion command and print a concise summary."""

    provenance = run_ingestion()
    counts = provenance["normalized_counts"]
    areas = provenance["areas_m2"]
    candidate = provenance["candidate_relationship_diagnostics"]
    print(
        "Protected areas: "
        f"{counts['national_parks']} national parks, "
        f"{counts['nature_reserves']} nature reserves, "
        f"{counts['natura_sci']} SCI, {counts['natura_spa']} SPA, "
        f"{counts['natura_combined_spa_sci']} SPA/SCI"
    )
    print(
        f"Footprint: {areas['final_union_area_m2'] / 1_000_000:.2f} km² "
        f"({provenance['footprint_geometry']['geometry_type']})"
    )
    print(
        f"Candidates: {candidate['candidate_intersect_count']} intersect, "
        f"{candidate['candidate_centroid_inside_count']} centroids inside; "
        f"geometry distance min/max {candidate['geometry_to_geometry_minimum_distance_m']['min']:.1f}/"
        f"{candidate['geometry_to_geometry_minimum_distance_m']['max']:.1f} m"
    )
    print(f"Wrote {PROCESSED_PATH} layers: {', '.join(provenance['output']['layers'])}")
    print(f"Raw: {RAW_NATIONAL_PATH}, {RAW_NATURA_PATH}")
    print(f"Provenance: {PROVENANCE_PATH}")


if __name__ == "__main__":
    main()
