"""Acquire, validate, and subset the Naturvårdsverket NMD2023 v2.1 raster."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS
from rasterio.features import rasterize, shapes
from rasterio.windows import Window, from_bounds, transform as window_transform
from shapely import make_valid, union_all
from shapely.geometry import shape

from restoration_prioritizer.config import TARGET_CRS
from restoration_prioritizer.study_area import (
    OUTPUT_LAYER as STUDY_AREA_LAYER,
    PROCESSED_PATH as STUDY_AREA_PATH,
    validate_output as validate_study_area,
)

NMD_AUTHORITY = "Naturvårdsverket"
NMD_PRODUCT = "Nationella marktäckedata 2023 – NMD2023 Basskikt"
NMD_PRODUCT_VERSION = "2.1"
NMD_REFERENCE_YEAR = 2023
NMD_LICENSE = "CC0"
NMD_ARCHIVE_URL = (
    "https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/"
    "Basskikt_v2_x/NMD2023_basskikt_v2_1.zip"
)
NMD_DELIVERY_DIRECTORY = (
    "https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/"
)
NMD_COVERAGE_LAYER = "NV_NMD2023_version_baskartering"
NMD_EXPECTED_CRS = TARGET_CRS
NMD_EXPECTED_RESOLUTION = (10.0, 10.0)
NMD_EXPECTED_DTYPE = "uint16"
NMD_EXPECTED_BANDS = 1
# These domain codes are verified from the archive's supplied .vat.dbf legend.
NMD_NO_DATA_CODES = frozenset({0})
NMD_MARINE_CODES = frozenset({62})
NMD_CLASS_LEGEND_SUFFIX = ".tif.vat.dbf"
NMD_RAW_DIR = Path("data/raw/nmd")
NMD_INTERIM_DIR = Path("data/interim/nmd")
NMD_PROCESSED_DIR = Path("data/processed/nmd")
NMD_ARCHIVE_PATH = NMD_RAW_DIR / "NMD2023_basskikt_v2_1.zip"
NMD_OUTPUT_PATH = NMD_PROCESSED_DIR / "nmd2023_v2_1_skane.tif"
NMD_PROVENANCE_PATH = NMD_PROCESSED_DIR / "nmd2023_v2_1_skane.provenance.json"
HTTP_TIMEOUT_SECONDS = 300
DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024
PROGRESS_BYTES = 100 * 1024 * 1024


class NMDIngestionError(RuntimeError):
    """Raised when NMD acquisition or validation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class RemoteArchiveInfo:
    """Relevant HTTP facts observed for the official archive."""

    url: str
    content_length: int | None
    last_modified: str | None
    accept_ranges: str | None
    etag: str | None
    content_type: str | None
    range_probe_status: int | None
    range_probe_content_range: str | None
    range_supported: bool


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """Stable, JSON-friendly ZIP central-directory facts for one member."""

    name: str
    compression: str
    compression_method: int
    compressed_size: int
    uncompressed_size: int
    crc32: str


@dataclass(frozen=True, slots=True)
class RasterFacts:
    """Validated metadata for a source or processed raster."""

    crs: str
    resolution: tuple[float, float]
    dtype: str
    count: int
    width: int
    height: int
    bounds: tuple[float, float, float, float]
    nodata: float | int | None
    mask_flags: tuple[str, ...]
    compression: str | None
    tiled: bool
    block_shapes: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """Raster-footprint coverage result for the adopted study extent."""

    accepted: bool
    metadata_layer: str
    metadata_crs: str
    geometry_type: str
    metadata_fields: tuple[str, ...]
    version_field: str
    observed_version_values: tuple[str, ...]
    current_v2x_values: tuple[str, ...]
    study_pixels: int
    valid_data_pixels: int
    current_v2x_pixels: int
    uncovered_valid_pixels: int
    non_data_pixels: int
    marine_pixels: int
    no_data_pixels: int
    valid_data_area_km2: float
    uncovered_valid_area_km2: float
    non_data_area_km2: float
    valid_data_without_metadata_pixels: int
    uncovered_bounds: tuple[float, float, float, float] | None
    uncovered_component_count: int
    result: str
    domain_definition: str


@dataclass(frozen=True, slots=True)
class OutputFacts:
    """Factual statistics for the Skåne NMD subset."""

    path: Path
    raster: RasterFacts
    valid_pixel_count: int
    valid_data_area_km2: float
    class_codes: tuple[int, ...]
    class_counts: dict[str, int]
    file_size_bytes: int


def inspect_remote_archive(url: str = NMD_ARCHIVE_URL) -> RemoteArchiveInfo:
    """Inspect archive headers and make a bounded one-byte range request."""

    request = Request(url, method="HEAD")
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            headers = response.headers
            content_length = _header_int(headers.get("Content-Length"))
            last_modified = headers.get("Last-Modified")
            accept_ranges = headers.get("Accept-Ranges")
            etag = headers.get("ETag")
            content_type = headers.get("Content-Type")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise NMDIngestionError(f"Could not inspect official NMD archive: {exc}") from exc

    range_status: int | None = None
    range_content_range: str | None = None
    range_supported = False
    try:
        range_request = Request(url, headers={"Range": "bytes=0-0"})
        with urlopen(range_request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response.read(1)
            range_status = getattr(response, "status", None)
            range_content_range = response.headers.get("Content-Range")
            range_supported = range_status == 206 and bool(range_content_range)
    except (HTTPError, URLError, TimeoutError, OSError):
        range_supported = False

    return RemoteArchiveInfo(
        url=url,
        content_length=content_length,
        last_modified=last_modified,
        accept_ranges=accept_ranges,
        etag=etag,
        content_type=content_type,
        range_probe_status=range_status,
        range_probe_content_range=range_content_range,
        range_supported=range_supported,
    )


def select_archive_members(names: Iterable[str]) -> tuple[str, str]:
    """Select the observed v2.1 raster and preferred GeoPackage members."""

    member_names = tuple(names)
    raster_candidates = tuple(name for name in member_names if name.endswith("NMD2023bas_v2_1.tif"))
    gpkg_candidates = tuple(
        name for name in member_names if name.endswith("NMD2023_metadata_v2_0.gpkg")
    )
    if len(raster_candidates) != 1:
        raise NMDIngestionError(
            f"Expected one NMD2023 v2.1 raster member, found {raster_candidates}"
        )
    if len(gpkg_candidates) != 1:
        raise NMDIngestionError(
            f"Expected one preferred NMD metadata GeoPackage member, found {gpkg_candidates}"
        )
    return raster_candidates[0], gpkg_candidates[0]


def read_class_legend(archive_path: Path) -> dict[int, str | None]:
    """Read the official code/name table supplied beside the NMD raster."""

    try:
        with zipfile.ZipFile(archive_path) as archive:
            candidates = tuple(
                name for name in archive.namelist() if name.endswith(NMD_CLASS_LEGEND_SUFFIX)
            )
            if len(candidates) != 1:
                raise NMDIngestionError(
                    f"Expected one supplied NMD class legend, found {candidates}"
                )
            return _parse_dbf_legend(archive.read(candidates[0]))
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise NMDIngestionError(f"Could not read the supplied NMD class legend: {exc}") from exc


def _parse_dbf_legend(payload: bytes) -> dict[int, str | None]:
    """Parse the small dBase VAT table without adding a DBF dependency."""

    if len(payload) < 32:
        raise NMDIngestionError("Supplied NMD class legend is too short to be a DBF")
    header_length = int.from_bytes(payload[8:10], "little")
    record_length = int.from_bytes(payload[10:12], "little")
    record_count = int.from_bytes(payload[4:8], "little")
    fields: list[tuple[str, int]] = []
    offset = 32
    while offset + 32 <= len(payload) and payload[offset] != 0x0D:
        name = payload[offset : offset + 11].split(b"\0", 1)[0].decode("ascii")
        fields.append((name, payload[offset + 16]))
        offset += 32
    field_offsets: dict[str, tuple[int, int]] = {}
    field_offset = 1
    for name, length in fields:
        field_offsets[name] = (field_offset, length)
        field_offset += length
    if "Value" not in field_offsets or "Klass" not in field_offsets:
        raise NMDIngestionError(f"Supplied NMD class legend lacks Value/Klass fields: {fields}")

    result: dict[int, str | None] = {}
    for index in range(record_count):
        start = header_length + index * record_length
        record = payload[start : start + record_length]
        if len(record) != record_length or record[:1] == b"*":
            continue
        value_start, value_length = field_offsets["Value"]
        name_start, name_length = field_offsets["Klass"]
        value_text = record[value_start : value_start + value_length].decode("ascii").strip()
        if not value_text:
            continue
        class_name = record[name_start : name_start + name_length].decode("utf-8").strip(" \0")
        result[int(value_text)] = class_name or None
    return result


def archive_members(archive_path: Path) -> tuple[ArchiveMember, ...]:
    """Read ZIP central-directory facts without extracting members."""

    try:
        with zipfile.ZipFile(archive_path) as archive:
            return tuple(
                ArchiveMember(
                    name=info.filename,
                    compression=_compression_name(info.compress_type),
                    compression_method=info.compress_type,
                    compressed_size=info.compress_size,
                    uncompressed_size=info.file_size,
                    crc32=f"{info.CRC:08x}",
                )
                for info in archive.infolist()
            )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise NMDIngestionError(
            f"NMD archive is not a readable ZIP: {archive_path}: {exc}"
        ) from exc


def download_or_use_cached_archive(
    remote: RemoteArchiveInfo,
    archive_path: Path = NMD_ARCHIVE_PATH,
    provenance_path: Path = NMD_PROVENANCE_PATH,
) -> tuple[Path, str, bool]:
    """Download the archive atomically, resuming a simple partial file when possible."""

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists() and _cached_archive_is_valid(archive_path, remote, provenance_path):
        print(f"Using cached NMD archive: {archive_path} ({archive_path.stat().st_size:,} bytes)")
        return archive_path, _sha256_file(archive_path), True

    if archive_path.exists():
        raise NMDIngestionError(
            f"Cached archive exists but failed validation: {archive_path}. "
            "Move it aside and rerun rather than overwriting it automatically."
        )
    if remote.content_length is None:
        raise NMDIngestionError(
            "Official archive did not expose Content-Length; refusing unbounded download"
        )

    part_path = archive_path.with_name(f"{archive_path.name}.part")
    current_size = part_path.stat().st_size if part_path.exists() else 0
    use_range = remote.range_supported and 0 < current_size < remote.content_length
    headers = {"Range": f"bytes={current_size}-"} if use_range else {}
    mode = "ab" if use_range else "wb"
    if current_size >= remote.content_length:
        part_path.unlink()
        current_size = 0
        use_range = False
        headers = {}
        mode = "wb"

    print(
        f"Downloading NMD archive ({remote.content_length / (1024**3):.2f} GiB)"
        + (f", resuming at {current_size / (1024**3):.2f} GiB" if use_range else "")
    )
    try:
        request = Request(remote.url, headers=headers)
        with (
            urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response,
            part_path.open(mode) as output,
        ):
            status = getattr(response, "status", None)
            if use_range and not _response_starts_at(response, current_size):
                output.close()
                part_path.unlink(missing_ok=True)
                return download_or_use_cached_archive(remote, archive_path, provenance_path)
            downloaded = current_size
            next_report = ((downloaded // PROGRESS_BYTES) + 1) * PROGRESS_BYTES
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_report:
                    print(f"  downloaded {downloaded / (1024**3):.2f} GiB")
                    next_report += PROGRESS_BYTES
            response_length = _header_int(response.headers.get("Content-Length"))
            if response_length is not None and response_length != downloaded - current_size:
                raise NMDIngestionError(
                    f"NMD download ended early: received {downloaded - current_size:,} bytes, "
                    f"expected {response_length:,}"
                )
            if status not in (200, 206):
                raise NMDIngestionError(f"NMD download returned unexpected HTTP status {status}")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise NMDIngestionError(
            f"NMD archive download failed; partial file retained at {part_path}: {exc}"
        ) from exc

    if part_path.stat().st_size != remote.content_length:
        raise NMDIngestionError(
            f"NMD archive size mismatch: {part_path.stat().st_size:,} bytes, "
            f"expected {remote.content_length:,}"
        )
    os.replace(part_path, archive_path)
    print("Checking downloaded ZIP member CRCs...")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        archive_path.unlink(missing_ok=True)
        raise NMDIngestionError(
            f"Downloaded NMD archive failed ZIP integrity validation: {exc}"
        ) from exc
    if bad_member is not None:
        archive_path.unlink(missing_ok=True)
        raise NMDIngestionError(f"Downloaded NMD archive has a failed CRC in member {bad_member}")
    return archive_path, _sha256_file(archive_path), False


def validate_source_raster(source_path: Path) -> RasterFacts:
    """Validate the official raster's minimum structural contract."""

    try:
        with rasterio.open(source_path) as source:
            if source.crs is None or CRS.from_user_input(source.crs) != CRS.from_user_input(
                NMD_EXPECTED_CRS
            ):
                raise NMDIngestionError(
                    f"NMD raster CRS is {source.crs}; expected {NMD_EXPECTED_CRS}"
                )
            if not np.allclose(source.res, NMD_EXPECTED_RESOLUTION, atol=1e-7):
                raise NMDIngestionError(
                    f"NMD raster resolution is {source.res}; expected 10 m x 10 m"
                )
            if source.count != NMD_EXPECTED_BANDS:
                raise NMDIngestionError(
                    f"NMD raster has {source.count} bands; expected {NMD_EXPECTED_BANDS}"
                )
            if source.dtypes[0] != NMD_EXPECTED_DTYPE:
                raise NMDIngestionError(
                    f"NMD raster dtype is {source.dtypes[0]}; expected {NMD_EXPECTED_DTYPE}"
                )
            if source.width <= 0 or source.height <= 0:
                raise NMDIngestionError("NMD raster has non-positive dimensions")
            min_x, min_y, max_x, max_y = source.bounds
            if not (100_000 < min_x < 1_000_000 and 5_000_000 < min_y < 7_500_000):
                raise NMDIngestionError(f"NMD raster bounds are implausible: {source.bounds}")
            return RasterFacts(
                crs=CRS.from_user_input(source.crs).to_string(),
                resolution=(float(source.res[0]), float(source.res[1])),
                dtype=source.dtypes[0],
                count=source.count,
                width=source.width,
                height=source.height,
                bounds=tuple(float(value) for value in source.bounds),
                nodata=_json_number(source.nodata),
                mask_flags=tuple(flag.name for flag in source.mask_flag_enums[0]),
                compression=source.compression.name if source.compression else None,
                tiled=source.is_tiled,
                block_shapes=tuple(
                    tuple(int(value) for value in shape) for shape in source.block_shapes
                ),
            )
    except rasterio.errors.RasterioIOError as exc:
        raise NMDIngestionError(
            f"NMD source raster could not be opened: {source_path}: {exc}"
        ) from exc


def evaluate_coverage(
    source_path: Path,
    metadata_path: Path,
    study_area_path: Path = STUDY_AREA_PATH,
    no_data_codes: frozenset[int] = NMD_NO_DATA_CODES,
    marine_codes: frozenset[int] = NMD_MARINE_CODES,
) -> CoverageResult:
    """Compare current v2.x metadata coverage with valid NMD pixels in Skåne.

    The source's all-valid GDAL mask is combined with the supplied code legend:
    code 0 is no-data and code 62 is the marine class. Pixels inside the
    administrative study extent that are no-data or marine are reported as
    non-terrestrial domain pixels, not as terrestrial coverage gaps. Any valid
    terrestrial source pixel not covered by current v2.x metadata fails the gate.
    """

    study = gpd.read_file(study_area_path, layer=STUDY_AREA_LAYER)
    validate_study_area(study)
    study_geometry = study.geometry.iloc[0]
    layers = gpd.list_layers(metadata_path)
    layer_matches = tuple(
        str(name)
        for name in layers["name"]
        if str(name).casefold() == NMD_COVERAGE_LAYER.casefold()
    )
    if len(layer_matches) != 1:
        raise NMDIngestionError(
            f"Metadata GeoPackage does not contain exactly one {NMD_COVERAGE_LAYER} layer: "
            f"{tuple(str(name) for name in layers['name'])}"
        )
    metadata_layer = layer_matches[0]
    coverage = gpd.read_file(metadata_path, layer=metadata_layer)
    if coverage.empty:
        raise NMDIngestionError(f"Metadata layer {metadata_layer} is empty")
    if coverage.crs is None or CRS.from_user_input(coverage.crs) != CRS.from_user_input(
        NMD_EXPECTED_CRS
    ):
        raise NMDIngestionError(
            f"Coverage metadata CRS is {coverage.crs}; expected {NMD_EXPECTED_CRS}"
        )
    if coverage.geometry.isna().any() or coverage.geometry.is_empty.any():
        raise NMDIngestionError("Coverage metadata contains missing or empty geometries")
    invalid = ~coverage.geometry.is_valid
    if invalid.any():
        coverage.loc[invalid, "geometry"] = coverage.loc[invalid, "geometry"].map(make_valid)
    if (~coverage.geometry.is_valid).any():
        raise NMDIngestionError(
            "Coverage metadata contains invalid geometry that could not be repaired"
        )

    version_field = _find_version_field(coverage)
    value_strings = coverage[version_field].map(_value_string)
    observed_values = tuple(sorted(set(value_strings.dropna())))
    current_values = tuple(value for value in observed_values if _is_current_v2x(value))
    if not current_values:
        raise NMDIngestionError(
            f"Coverage metadata field {version_field!r} has no recognizable current v2.x values: "
            f"{observed_values}"
        )
    current = coverage.loc[value_strings.isin(current_values)]
    all_geometry = union_all(coverage.geometry.array)
    current_geometry = union_all(current.geometry.array)

    with rasterio.open(source_path) as source:
        window = _study_window(source, study_geometry)
        data = source.read(1, window=window, masked=False)
        source_valid = source.read_masks(1, window=window) > 0
        transform = window_transform(window, source.transform)
        study_mask = rasterize(
            [(study_geometry, 1)], out_shape=data.shape, transform=transform, fill=0, dtype="uint8"
        ).astype(bool)
        code_domain = ~np.isin(data, tuple(no_data_codes | marine_codes))
        valid_data = study_mask & source_valid & code_domain
        all_metadata_mask = rasterize(
            [(all_geometry, 1)], out_shape=data.shape, transform=transform, fill=0, dtype="uint8"
        ).astype(bool)
        current_mask = rasterize(
            [(current_geometry, 1)],
            out_shape=data.shape,
            transform=transform,
            fill=0,
            dtype="uint8",
        ).astype(bool)
        uncovered = valid_data & ~current_mask
        non_data = study_mask & ~code_domain
        marine = study_mask & source_valid & np.isin(data, tuple(marine_codes))
        no_data = study_mask & source_valid & np.isin(data, tuple(no_data_codes))
        pixel_area_km2 = abs(float(source.res[0] * source.res[1])) / 1_000_000
        uncovered_bounds, component_count = _mask_bounds_and_components(uncovered, transform)
        valid_count = int(valid_data.sum())
        current_count = int((valid_data & current_mask).sum())
        uncovered_count = int(uncovered.sum())
        non_data_count = int(non_data.sum())
        marine_count = int(marine.sum())
        no_data_count = int(no_data.sum())
        metadata_outside_valid = int((valid_data & ~all_metadata_mask).sum())

    accepted = uncovered_count == 0 and valid_count > 0 and metadata_outside_valid == 0
    result = "ACCEPTED: every valid NMD pixel inside Skåne is covered by current v2.x metadata"
    if not accepted:
        result = (
            "BLOCKED: valid NMD pixels inside Skåne fall outside current v2.x metadata coverage"
        )
    return CoverageResult(
        accepted=accepted,
        metadata_layer=metadata_layer,
        metadata_crs=CRS.from_user_input(coverage.crs).to_string(),
        geometry_type=", ".join(sorted(set(str(value) for value in coverage.geometry.geom_type))),
        metadata_fields=tuple(str(column) for column in coverage.columns),
        version_field=version_field,
        observed_version_values=observed_values,
        current_v2x_values=current_values,
        study_pixels=int(study_mask.sum()),
        valid_data_pixels=valid_count,
        current_v2x_pixels=current_count,
        uncovered_valid_pixels=uncovered_count,
        non_data_pixels=non_data_count,
        valid_data_area_km2=valid_count * pixel_area_km2,
        uncovered_valid_area_km2=uncovered_count * pixel_area_km2,
        non_data_area_km2=non_data_count * pixel_area_km2,
        marine_pixels=marine_count,
        no_data_pixels=no_data_count,
        valid_data_without_metadata_pixels=metadata_outside_valid,
        uncovered_bounds=uncovered_bounds,
        uncovered_component_count=component_count,
        result=result,
        domain_definition=(
            "Source mask pixels with the legend's code 0 (no-data) and code 62 (Hav/sea) "
            "are excluded from the terrestrial NMD data footprint. They remain factual "
            "domain statistics and are not analytical class groupings."
        ),
    )


def create_skane_raster(
    source_path: Path,
    output_path: Path = NMD_OUTPUT_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
    no_data_codes: frozenset[int] = NMD_NO_DATA_CODES,
) -> OutputFacts:
    """Write a native-grid, masked, tiled and losslessly compressed subset."""

    study = gpd.read_file(study_area_path, layer=STUDY_AREA_LAYER)
    validate_study_area(study)
    study_geometry = study.geometry.iloc[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.part")
    temporary_path.unlink(missing_ok=True)
    try:
        with rasterio.open(source_path) as source:
            window = _study_window(source, study_geometry)
            data = source.read(1, window=window, masked=False)
            source_valid = source.read_masks(1, window=window) > 0
            transform = window_transform(window, source.transform)
            study_mask = rasterize(
                [(study_geometry, 1)],
                out_shape=data.shape,
                transform=transform,
                fill=0,
                dtype="uint8",
            ).astype(bool)
            output_mask = study_mask & source_valid & ~np.isin(data, tuple(no_data_codes))
            source_codes = tuple(int(value) for value in np.unique(data[output_mask]))
            profile = source.profile.copy()
            profile.update(
                driver="GTiff",
                height=data.shape[0],
                width=data.shape[1],
                count=1,
                dtype=source.dtypes[0],
                crs=NMD_EXPECTED_CRS,
                transform=transform,
                tiled=True,
                blockxsize=256,
                blockysize=256,
                compress="deflate",
                zlevel=6,
                predictor=1,
                BIGTIFF="IF_SAFER",
                nodata=_json_number(source.nodata) if source.nodata is not None else 0,
            )
            with rasterio.open(temporary_path, "w", **profile) as destination:
                destination.write(data, 1)
                destination.write_mask(np.where(output_mask, 255, 0).astype("uint8"))
        os.replace(temporary_path, output_path)
    except (OSError, rasterio.errors.RasterioIOError) as exc:
        temporary_path.unlink(missing_ok=True)
        raise NMDIngestionError(f"Could not create Skåne NMD raster: {exc}") from exc

    return inspect_output_raster(output_path, expected_codes=source_codes)


def inspect_output_raster(output_path: Path, expected_codes: tuple[int, ...] | None) -> OutputFacts:
    """Reopen and inspect the durable output, optionally checking code preservation."""

    with rasterio.open(output_path) as output:
        if output.crs is None or CRS.from_user_input(output.crs) != CRS.from_user_input(
            NMD_EXPECTED_CRS
        ):
            raise NMDIngestionError(
                f"Output raster CRS is {output.crs}; expected {NMD_EXPECTED_CRS}"
            )
        if not np.allclose(output.res, NMD_EXPECTED_RESOLUTION, atol=1e-7):
            raise NMDIngestionError(
                f"Output raster resolution is {output.res}; expected 10 m x 10 m"
            )
        if output.count != 1 or output.dtypes[0] != NMD_EXPECTED_DTYPE:
            raise NMDIngestionError(
                "Output raster does not preserve the single uint16 categorical band"
            )
        values = output.read(1, masked=False)
        valid = output.read_masks(1) > 0
        codes, counts = np.unique(values[valid], return_counts=True)
        class_codes = tuple(int(value) for value in codes)
        if expected_codes is not None and class_codes != expected_codes:
            raise NMDIngestionError(
                f"Output class codes differ from source subset: {class_codes} != {expected_codes}"
            )
        raster = RasterFacts(
            crs=CRS.from_user_input(output.crs).to_string(),
            resolution=(float(output.res[0]), float(output.res[1])),
            dtype=output.dtypes[0],
            count=output.count,
            width=output.width,
            height=output.height,
            bounds=tuple(float(value) for value in output.bounds),
            nodata=_json_number(output.nodata),
            mask_flags=tuple(flag.name for flag in output.mask_flag_enums[0]),
            compression=output.compression.name if output.compression else None,
            tiled=bool(output.profile.get("tiled", False)),
            block_shapes=tuple(
                tuple(int(value) for value in shape) for shape in output.block_shapes
            ),
        )
    return OutputFacts(
        path=output_path,
        raster=raster,
        valid_pixel_count=int(valid.sum()),
        valid_data_area_km2=int(valid.sum())
        * abs(raster.resolution[0] * raster.resolution[1])
        / 1_000_000,
        class_codes=class_codes,
        class_counts={
            str(int(code)): int(count) for code, count in zip(codes, counts, strict=True)
        },
        file_size_bytes=output_path.stat().st_size,
    )


def run_ingestion(
    archive_path: Path = NMD_ARCHIVE_PATH,
    study_area_path: Path = STUDY_AREA_PATH,
    output_path: Path = NMD_OUTPUT_PATH,
    provenance_path: Path = NMD_PROVENANCE_PATH,
    keep_interim: bool = False,
) -> dict[str, Any]:
    """Run the complete NMD v2.1 acquisition and Skåne artifact workflow."""

    remote = inspect_remote_archive()
    archive_path, archive_sha256, used_cache = download_or_use_cached_archive(
        remote, archive_path=archive_path, provenance_path=provenance_path
    )
    members = archive_members(archive_path)
    raster_member_name, gpkg_member_name = select_archive_members(member.name for member in members)
    legend_member_candidates = tuple(
        member.name for member in members if member.name.endswith(NMD_CLASS_LEGEND_SUFFIX)
    )
    if len(legend_member_candidates) != 1:
        raise NMDIngestionError(
            f"Expected one supplied NMD class legend member, found {legend_member_candidates}"
        )
    legend_member_name = legend_member_candidates[0]
    class_legend = read_class_legend(archive_path)
    member_by_name = {member.name: member for member in members}
    interim_raster_path = NMD_INTERIM_DIR / Path(raster_member_name).name
    interim_metadata_path = NMD_INTERIM_DIR / Path(gpkg_member_name).name
    _extract_member(archive_path, raster_member_name, interim_raster_path)
    _extract_member(archive_path, gpkg_member_name, interim_metadata_path)
    source_raster = validate_source_raster(interim_raster_path)
    coverage = evaluate_coverage(
        interim_raster_path,
        interim_metadata_path,
        study_area_path,
        no_data_codes=NMD_NO_DATA_CODES,
        marine_codes=NMD_MARINE_CODES,
    )

    output: OutputFacts | None = None
    cleanup_performed = False
    if coverage.accepted:
        output = create_skane_raster(interim_raster_path, output_path, study_area_path)
        unknown_codes = sorted(set(output.class_codes) - set(class_legend))
        if unknown_codes:
            raise NMDIngestionError(
                f"Output contains class codes absent from the supplied NMD legend: {unknown_codes}"
            )
        output = inspect_output_raster(output_path, expected_codes=output.class_codes)
        if not keep_interim:
            interim_raster_path.unlink(missing_ok=True)
            interim_metadata_path.unlink(missing_ok=True)
            cleanup_performed = True

    provenance = _build_provenance(
        remote=remote,
        archive_path=archive_path,
        archive_sha256=archive_sha256,
        used_cache=used_cache,
        members=member_by_name,
        raster_member_name=raster_member_name,
        gpkg_member_name=gpkg_member_name,
        legend_member_name=legend_member_name,
        source_raster=source_raster,
        coverage=coverage,
        output=output,
        class_legend=class_legend,
        study_area_path=study_area_path,
        interim_raster_path=interim_raster_path,
        interim_metadata_path=interim_metadata_path,
        cleanup_performed=cleanup_performed,
    )
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_provenance = provenance_path.with_name(f"{provenance_path.name}.part")
    temporary_provenance.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_provenance, provenance_path)
    print(_summary(remote, archive_path, source_raster, coverage, output, provenance_path))
    if not coverage.accepted:
        raise NMDIngestionError(coverage.result)
    return provenance


def _build_provenance(
    *,
    remote: RemoteArchiveInfo,
    archive_path: Path,
    archive_sha256: str,
    used_cache: bool,
    members: dict[str, ArchiveMember],
    raster_member_name: str,
    gpkg_member_name: str,
    legend_member_name: str,
    source_raster: RasterFacts,
    coverage: CoverageResult,
    output: OutputFacts | None,
    class_legend: dict[int, str | None],
    study_area_path: Path,
    interim_raster_path: Path,
    interim_metadata_path: Path,
    cleanup_performed: bool,
) -> dict[str, Any]:
    output_raster = asdict(output.raster) if output is not None else None
    if output_raster is not None:
        output_raster["bounds"] = list(output.raster.bounds)
        output_raster["resolution"] = list(output.raster.resolution)
        output_raster["block_shapes"] = [list(shape) for shape in output.raster.block_shapes]
    return {
        "authority": NMD_AUTHORITY,
        "product": NMD_PRODUCT,
        "official_product_version": NMD_PRODUCT_VERSION,
        "reference_year": NMD_REFERENCE_YEAR,
        "archive_url": remote.url,
        "delivery_directory": NMD_DELIVERY_DIRECTORY,
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "remote_http": asdict(remote),
        "source_archive": {
            "path": str(archive_path),
            "local_size_bytes": archive_path.stat().st_size,
            "local_sha256": archive_sha256,
            "cache_reused": used_cache,
            "publisher_checksum_available": False,
            "publisher_checksum_note": (
                "No checksum file or checksum field was observed in the official v2.x delivery "
                "directory or adjacent official search results. SHA-256 is computed locally."
            ),
            "zip_integrity": "CRC checked after a new download; cached ZIP central directory and size checked on reuse",
        },
        "archive_members": {
            "raster": asdict(members[raster_member_name]),
            "preferred_metadata_geopackage": asdict(members[gpkg_member_name]),
            "class_legend": asdict(members[legend_member_name]),
            "member_count": len(members),
            "observed_raster_member": raster_member_name,
            "observed_metadata_member": gpkg_member_name,
            "duplicate_metadata_formats_not_extracted": True,
        },
        "source_raster": _json_raster_facts(source_raster),
        "coverage_metadata": {
            "layer": coverage.metadata_layer,
            "geometry_type": coverage.geometry_type,
            "crs": coverage.metadata_crs,
            "version_field": coverage.version_field,
            "observed_version_values": list(coverage.observed_version_values),
            "current_v2x_values": list(coverage.current_v2x_values),
            "verification": asdict(coverage),
        },
        "coverage_acceptance": coverage.accepted,
        "coverage_result": coverage.result,
        "study_area": {"path": str(study_area_path), "layer": STUDY_AREA_LAYER},
        "output": (
            {
                "path": str(output.path),
                "format": "tiled GeoTIFF with internal mask and DEFLATE compression",
                "raster": output_raster,
                "valid_pixel_count": output.valid_pixel_count,
                "valid_data_area_km2": output.valid_data_area_km2,
                "file_size_bytes": output.file_size_bytes,
                "class_codes": list(output.class_codes),
                "class_counts": output.class_counts,
                "class_code_names": {
                    str(code): class_legend.get(code) for code in output.class_codes
                },
            }
            if output is not None
            else None
        ),
        "extraction": {
            "interim_raster_path": str(interim_raster_path),
            "interim_raster_size_bytes": interim_raster_path.stat().st_size
            if interim_raster_path.exists()
            else None,
            "interim_metadata_path": str(interim_metadata_path),
            "interim_metadata_size_bytes": interim_metadata_path.stat().st_size
            if interim_metadata_path.exists()
            else None,
            "cleanup_performed": cleanup_performed,
        },
        "license": NMD_LICENSE,
        "code_semantics": {
            "legend_member_suffix": NMD_CLASS_LEGEND_SUFFIX,
            "verified_legend_entry_count": len(class_legend),
            "no_data_codes": sorted(NMD_NO_DATA_CODES),
            "marine_codes": sorted(NMD_MARINE_CODES),
            "note": "Code 62 (Hav/sea) is retained in the output raster but excluded from terrestrial coverage statistics; code 0 is masked as no-data.",
        },
        "documentation_discrepancies": [
            "Live v2.1 archive contains a raster named NMD2023bas_v2_1.tif, while the bundled metadata GeoPackage is named NMD2023_metadata_v2_0.gpkg and is nested under the .gdb directory. The observed live ZIP member names are authoritative for this ingestion.",
            "The live v2.x directory also lists a PDF named Granskning_NMD2023_2_x.pdf and a v2.0 quality report; neither changes the selected v2.1 product contract.",
        ],
        "analytical_boundary": "No NMD codes are reclassified or grouped into habitat, candidate-land, suitability, or score semantics in this step.",
    }


def _extract_member(archive_path: Path, member_name: str, output_path: Path) -> None:
    """Extract one member through a temporary file and atomic rename."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.part")
    temporary_path.unlink(missing_ok=True)
    try:
        with (
            zipfile.ZipFile(archive_path) as archive,
            archive.open(member_name) as source,
            temporary_path.open("wb") as destination,
        ):
            shutil.copyfileobj(source, destination, length=DOWNLOAD_CHUNK_BYTES)
        os.replace(temporary_path, output_path)
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        temporary_path.unlink(missing_ok=True)
        raise NMDIngestionError(f"Could not extract NMD member {member_name}: {exc}") from exc
    print(f"Extracted {member_name} -> {output_path} ({output_path.stat().st_size:,} bytes)")


def _cached_archive_is_valid(path: Path, remote: RemoteArchiveInfo, provenance_path: Path) -> bool:
    if remote.content_length is not None and path.stat().st_size != remote.content_length:
        return False
    try:
        members = archive_members(path)
        select_archive_members(member.name for member in members)
    except NMDIngestionError:
        return False
    if provenance_path.exists():
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            expected_hash = provenance.get("source_archive", {}).get("local_sha256")
            if expected_hash:
                return _sha256_file(path) == expected_hash
        except (OSError, json.JSONDecodeError):
            return False
    return True


def _find_version_field(frame: gpd.GeoDataFrame) -> str:
    candidates = [
        str(column)
        for column in frame.columns
        if column != frame.geometry.name and "version" in str(column).casefold()
    ]
    if candidates:
        for candidate in candidates:
            values = {_value_string(value) for value in frame[candidate].dropna()}
            if any(_is_current_v2x(value) for value in values):
                return candidate
    for column in frame.columns:
        if column == frame.geometry.name:
            continue
        values = {_value_string(value) for value in frame[column].dropna()}
        if any(_is_current_v2x(value) for value in values):
            return str(column)
    raise NMDIngestionError(
        "Could not identify the version field in the NMD coverage metadata layer"
    )


def _is_current_v2x(value: str) -> bool:
    return bool(
        re.search(r"(?<!\d)(?:v(?:ersion)?\s*)?2(?:[._\-\s]?[0-9x]+)?(?!\d)", value.casefold())
    )


def _value_string(value: Any) -> str:
    return str(value).strip()


def _study_window(source: rasterio.DatasetReader, geometry: Any) -> Window:
    window = from_bounds(*geometry.bounds, transform=source.transform)
    window = window.round_offsets().round_lengths()
    full = Window(0, 0, source.width, source.height)
    intersection = window.intersection(full)
    if intersection.width <= 0 or intersection.height <= 0:
        raise NMDIngestionError("Skåne study area does not overlap the NMD source raster")
    return intersection


def _mask_bounds_and_components(
    mask: np.ndarray, transform: Any
) -> tuple[tuple[float, float, float, float] | None, int]:
    if not mask.any():
        return None, 0
    polygons = [
        shape(polygon)
        for polygon, value in shapes(mask.astype("uint8"), mask=mask, transform=transform)
        if value
    ]
    if not polygons:
        return None, 0
    geometry = union_all(polygons)
    return tuple(float(value) for value in geometry.bounds), len(polygons)


def _response_starts_at(response: Any, start: int) -> bool:
    content_range = response.headers.get("Content-Range", "")
    return (
        bool(re.match(rf"bytes\s+{start}-\d+/\d+", content_range, re.IGNORECASE))
        and getattr(response, "status", None) == 206
    )


def _header_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _compression_name(method: int) -> str:
    return {
        zipfile.ZIP_STORED: "stored",
        zipfile.ZIP_DEFLATED: "deflate",
        zipfile.ZIP_BZIP2: "bzip2",
        zipfile.ZIP_LZMA: "lzma",
    }.get(method, f"method-{method}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_number(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else float(value)


def _json_raster_facts(facts: RasterFacts) -> dict[str, Any]:
    result = asdict(facts)
    result["resolution"] = list(facts.resolution)
    result["bounds"] = list(facts.bounds)
    result["block_shapes"] = [list(shape) for shape in facts.block_shapes]
    return result


def _summary(
    remote: RemoteArchiveInfo,
    archive_path: Path,
    source_raster: RasterFacts,
    coverage: CoverageResult,
    output: OutputFacts | None,
    provenance_path: Path,
) -> str:
    lines = [
        f"NMD archive: {archive_path} ({archive_path.stat().st_size / (1024**3):.2f} GiB)",
        f"Source raster: {source_raster.width} x {source_raster.height}, {source_raster.crs}, {source_raster.dtype}, {source_raster.resolution[0]:g} m",
        f"Remote: Content-Length={remote.content_length}, range_supported={remote.range_supported}",
        f"Coverage: {coverage.result}; valid={coverage.valid_data_area_km2:.2f} km², non-data={coverage.non_data_area_km2:.2f} km²",
    ]
    if output is not None:
        lines.append(
            f"Output: {output.path} ({output.file_size_bytes / (1024**2):.1f} MiB), "
            f"{output.valid_data_area_km2:.2f} km² valid, codes={output.class_codes}"
        )
    lines.append(f"Provenance: {provenance_path}")
    return "\n".join(lines)


def main() -> None:
    """Run the NMD source-specific ingestion command."""

    run_ingestion()


if __name__ == "__main__":
    main()
