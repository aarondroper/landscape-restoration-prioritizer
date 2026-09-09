import hashlib
import json
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from restoration_prioritizer.nmd import (
    NMD_ARCHIVE_URL,
    NMD_COVERAGE_LAYER,
    NMDIngestionError,
    RemoteArchiveInfo,
    create_skane_raster,
    download_or_use_cached_archive,
    evaluate_coverage,
    select_archive_members,
    validate_source_raster,
)


def write_raster(path: Path, *, crs: str = "EPSG:3006", resolution: float = 10) -> None:
    data = np.array([[1, 2], [3, 4]], dtype="uint16")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint16",
        crs=crs,
        transform=from_origin(0, 20, resolution, resolution),
        nodata=0,
    ) as dataset:
        dataset.write(data, 1)


def write_study_area(path: Path) -> None:
    study = gpd.GeoDataFrame({"name": ["synthetic"]}, geometry=[box(0, 0, 20, 20)], crs="EPSG:3006")
    study.to_file(path, layer="study_area", driver="GPKG")


def write_coverage(path: Path, geometry) -> None:
    coverage = gpd.GeoDataFrame({"version": ["v2.1"], "geometry": [geometry]}, crs="EPSG:3006")
    coverage.to_file(path, layer=NMD_COVERAGE_LAYER, driver="GPKG")


def test_source_raster_validation_rejects_wrong_crs_and_resolution(tmp_path: Path) -> None:
    wrong_crs = tmp_path / "wrong-crs.tif"
    write_raster(wrong_crs, crs="EPSG:4326")
    with pytest.raises(NMDIngestionError, match="CRS"):
        validate_source_raster(wrong_crs)

    wrong_resolution = tmp_path / "wrong-resolution.tif"
    write_raster(wrong_resolution, resolution=20)
    with pytest.raises(NMDIngestionError, match="resolution"):
        validate_source_raster(wrong_resolution)


def test_archive_member_selection_prefers_observed_raster_and_gpkg() -> None:
    selected = select_archive_members(
        (
            "NMD2023_basskikt_v2_1/NMD2023bas_v2_1.tif",
            "NMD2023_basskikt_v2_1/NMD2023_metadata_v2_0.gdb/NMD2023_metadata_v2_0.gpkg",
            "NMD2023_basskikt_v2_1/NMD2023bas_v2_1.tif.xml",
        )
    )
    assert selected == (
        "NMD2023_basskikt_v2_1/NMD2023bas_v2_1.tif",
        "NMD2023_basskikt_v2_1/NMD2023_metadata_v2_0.gdb/NMD2023_metadata_v2_0.gpkg",
    )


def test_coverage_evaluation_accepts_complete_and_rejects_valid_gap(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    study = tmp_path / "study.gpkg"
    complete = tmp_path / "complete.gpkg"
    incomplete = tmp_path / "incomplete.gpkg"
    write_raster(source)
    write_study_area(study)
    write_coverage(complete, box(0, 0, 20, 20))
    write_coverage(incomplete, box(0, 0, 10, 20))

    accepted = evaluate_coverage(source, complete, study)
    assert accepted.accepted
    assert accepted.uncovered_valid_pixels == 0
    assert accepted.current_v2x_values == ("v2.1",)

    blocked = evaluate_coverage(source, incomplete, study)
    assert not blocked.accepted
    assert blocked.uncovered_valid_pixels == 2
    assert blocked.uncovered_valid_area_km2 == pytest.approx(0.0002)


def test_output_class_values_are_unchanged_by_clipping(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    study = tmp_path / "study.gpkg"
    output = tmp_path / "output.tif"
    write_raster(source)
    write_study_area(study)

    facts = create_skane_raster(source, output, study)

    with rasterio.open(output) as dataset:
        assert dataset.dtypes == ("uint16",)
        assert tuple(np.unique(dataset.read(1)[dataset.read_masks(1) > 0])) == (1, 2, 3, 4)
        assert facts.class_codes == (1, 2, 3, 4)
        assert facts.valid_pixel_count == 4


def test_cached_archive_is_reused_when_size_members_and_local_hash_match(tmp_path: Path) -> None:
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("NMD2023bas_v2_1.tif", b"raster")
        zf.writestr("NMD2023_metadata_v2_0.gpkg", b"metadata")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"source_archive": {"local_sha256": digest}}))
    remote = RemoteArchiveInfo(
        url=NMD_ARCHIVE_URL,
        content_length=archive.stat().st_size,
        last_modified=None,
        accept_ranges="bytes",
        etag=None,
        content_type="application/zip",
        range_probe_status=206,
        range_probe_content_range="bytes 0-0/1",
        range_supported=True,
    )

    path, observed_hash, used_cache = download_or_use_cached_archive(remote, archive, provenance)

    assert path == archive
    assert observed_hash == digest
    assert used_cache
