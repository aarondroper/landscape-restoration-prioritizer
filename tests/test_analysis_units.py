from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from shapely import STRtree

from restoration_prioritizer.analysis_units import (
    HEX_FLAT_TO_FLAT_M,
    HEX_SIDE_M,
    THEORETICAL_HEX_AREA_M2,
    build_analysis_units,
    generate_grid,
    hex_center,
    hex_polygon,
    pixel_centers_to_grid_indices,
)


def write_synthetic_raster(path: Path) -> None:
    data = np.zeros((20, 60), dtype="uint16")
    data[0, :6] = [3, 111, 61, 62, 51, 54]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=data.dtype,
        crs="EPSG:3006",
        transform=Affine(10, 0, -50, 0, -10, 100),
        nodata=0,
    ) as dataset:
        dataset.write(data, 1)


def test_hex_dimensions_match_nominal_flat_to_flat_definition() -> None:
    polygon = hex_polygon(0, 0)

    assert polygon.bounds[2] - polygon.bounds[0] == pytest.approx(HEX_FLAT_TO_FLAT_M)
    assert HEX_SIDE_M == pytest.approx(500 / np.sqrt(3))
    assert polygon.area == pytest.approx(THEORETICAL_HEX_AREA_M2)
    assert THEORETICAL_HEX_AREA_M2 == pytest.approx(216_506.35094610968)


def test_grid_alignment_and_ids_are_deterministic() -> None:
    first = generate_grid((-600, -600, 600, 600))
    second = generate_grid((-600, -600, 600, 600))

    assert first["hex_id"].tolist() == second["hex_id"].tolist()
    assert first["hex_id"].is_unique
    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()


def test_tiny_grid_has_no_overlaps_and_expected_shared_side() -> None:
    grid = generate_grid((-600, -600, 600, 600))
    pairs = STRtree(grid.geometry.to_numpy()).query(grid.geometry.to_numpy(), predicate="overlaps")
    assert all(
        grid.geometry.iloc[left].intersection(grid.geometry.iloc[right]).area <= 1e-6
        for left, right in pairs.T
        if left < right
    )

    left = grid.loc[(grid.grid_col == 0) & (grid.grid_row == 0), "geometry"].iloc[0]
    right = grid.loc[(grid.grid_col == 1) & (grid.grid_row == 0), "geometry"].iloc[0]
    assert left.intersection(right).area <= 1e-6
    assert left.boundary.intersection(right.boundary).length == pytest.approx(HEX_SIDE_M)


def test_pixel_center_assignment_is_one_to_one_for_tiny_points() -> None:
    expected = [(0, 0), (1, 0), (-1, 0), (0, 1)]
    centers = [hex_center(col, row) for col, row in expected]
    x = np.array([center[0] for center in centers])
    y = np.array([center[1] for center in centers])
    cols, rows = pixel_centers_to_grid_indices(x, y)

    assert list(zip(cols.tolist(), rows.tolist(), strict=True)) == expected


def test_synthetic_composition_conservation_and_zero_terrestrial_exclusion(tmp_path: Path) -> None:
    raster_path = tmp_path / "synthetic.tif"
    output_path = tmp_path / "analysis_units.gpkg"
    provenance_path = tmp_path / "analysis_units.provenance.json"
    write_synthetic_raster(raster_path)

    units, provenance = build_analysis_units(
        raster_path=raster_path,
        output_path=output_path,
        provenance_path=provenance_path,
        audit_path=None,
        study_area_path=None,
    )
    assert len(units) == 1
    unit = units.iloc[0]
    assert unit.hex_id == "h_0_0"
    assert unit.nmd_valid_pixels == 6
    assert unit.terrestrial_pixels == 4
    assert unit.candidate_pixels == 1
    assert unit.habitat_context_pixels == 1
    assert unit.inland_water_pixels == 1
    assert unit.artificial_constraint_pixels == 1
    assert unit.peat_extraction_pixels == 1
    assert unit.sea_pixels == 1
    assert unit.candidate_fraction_of_terrestrial == 0.25
    assert unit.candidate_area_m2 == 100
    assert (
        unit.candidate_fraction_of_terrestrial != unit.candidate_area_m2 / THEORETICAL_HEX_AREA_M2
    )

    counts = provenance["counts"]
    assert counts["initial_generated_hex_count"] > counts["retained_terrestrial_hex_count"]
    assert counts["nmd_valid_hex_count"] == 1
    assert counts["zero_terrestrial_hexes_removed"] == counts["initial_generated_hex_count"] - 1
    assert output_path.exists()
    assert provenance_path.exists()


def test_synthetic_aggregate_totals_match_all_tracked_roles(tmp_path: Path) -> None:
    raster_path = tmp_path / "synthetic.tif"
    output_path = tmp_path / "analysis_units.gpkg"
    provenance_path = tmp_path / "analysis_units.provenance.json"
    write_synthetic_raster(raster_path)

    units, _ = build_analysis_units(
        raster_path=raster_path,
        output_path=output_path,
        provenance_path=provenance_path,
        audit_path=None,
        study_area_path=None,
    )
    assert units.nmd_valid_pixels.sum() == 6
    assert units.terrestrial_pixels.sum() == 4
    assert units.candidate_pixels.sum() == 1
    assert units.habitat_context_pixels.sum() == 1
    assert units.wetland_context_pixels.sum() == 0
    assert units.inland_water_pixels.sum() == 1
    assert units.artificial_constraint_pixels.sum() == 1
    assert units.transitional_forest_pixels.sum() == 0
    assert units.peat_extraction_pixels.sum() == 1
    assert units.sea_pixels.sum() == 1
