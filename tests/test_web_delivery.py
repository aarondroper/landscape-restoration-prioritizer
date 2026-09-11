import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon

from restoration_prioritizer.prioritization_model import (
    BOUNDARY_FLAG,
    PRESET_SCORE_FIELDS,
    SCORE_FIELDS,
    preset_metadata,
)
from restoration_prioritizer.web_delivery import (
    DELIVERY_FRACTIONS,
    DELIVERY_HECTARE_FIELDS,
    DELIVERY_INTEGER_FIELDS,
    DELIVERY_PROPERTY_FIELDS,
    DELIVERY_SCORE_FIELDS,
    PROPERTY_DESCRIPTIONS,
    TOP_N,
    WebDeliveryError,
    _feature_records,
    _rounding_audit,
    _validate_preset_metadata,
    assemble_delivery_frame,
    build_candidate_shortlists,
    reconcile_ids,
    round_delivery_properties,
    serialize_geojson,
)


def _sources() -> tuple[gpd.GeoDataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    ids = ["h_02", "h_01"]
    candidate = gpd.GeoDataFrame(
        {
            "hex_id": ids,
            "geometry": [
                Polygon(
                    [(400000, 6200000), (400500, 6200000), (400250, 6200433), (400000, 6200000)]
                ),
                Polygon(
                    [(401000, 6200000), (401500, 6200000), (401250, 6200433), (401000, 6200000)]
                ),
            ],
        },
        crs="EPSG:3006",
    )
    score_source = pd.DataFrame({"hex_id": ids, BOUNDARY_FLAG: [False, True]})
    for index, field in enumerate(SCORE_FIELDS, start=1):
        score_source[field] = [index + 0.123456, index + 1.234567]
    for index, field in enumerate(PRESET_SCORE_FIELDS.values(), start=1):
        score_source[field] = [index + 0.123456, index + 1.234567]
    component_sources = {
        "Habitat Context": pd.DataFrame(
            {
                "hex_id": ids,
                "habitat_context_local_fraction": [0.1234567, 0.2345678],
                BOUNDARY_FLAG: [False, True],
            }
        ),
        "Ecological Network Context": pd.DataFrame(
            {
                "hex_id": ids,
                "opposing_balance_ratio": [0.3333333, 0.4444444],
                BOUNDARY_FLAG: [False, True],
            }
        ),
        "Riparian Opportunity": pd.DataFrame(
            {
                "hex_id": ids,
                "riparian_focal_fraction": [0.5555555, 0.6666666],
                BOUNDARY_FLAG: [False, True],
            }
        ),
        "Protected-Area Reinforcement": pd.DataFrame(
            {
                "hex_id": ids,
                "nearest_protected_hex_steps": [5, 4],
                "protected_focal_fraction": [0.0123456, 0.0234567],
                BOUNDARY_FLAG: [False, True],
            }
        ),
        "Restoration Land Availability": pd.DataFrame(
            {
                "hex_id": ids,
                "candidate_land_area_ha": [10.1234, 20.5678],
                "artificial_focal_fraction": [0.0345678, 0.0456789],
                BOUNDARY_FLAG: [False, True],
            }
        ),
    }
    return candidate, score_source, component_sources


def _assembled() -> gpd.GeoDataFrame:
    candidate, scores, components = _sources()
    result, audit = assemble_delivery_frame(candidate, scores, components, expected_count=None)
    assert audit["exact_one_feature_per_candidate"]
    return result


def test_exact_id_reconciliation_and_missing_source_rejection() -> None:
    result = reconcile_ids(pd.Series(["h_01", "h_02"]), pd.Series(["h_02", "h_01"]), "source")
    assert result["exact_one_to_one"]
    with pytest.raises(WebDeliveryError, match="does not reconcile exactly"):
        reconcile_ids(pd.Series(["h_01", "h_02"]), pd.Series(["h_01"]), "source")


def test_geometry_transforms_from_epsg_3006_to_wgs84_without_axis_reversal() -> None:
    frame = _assembled().to_crs("EPSG:4326")
    assert frame.crs.to_epsg() == 4326
    min_lon, min_lat, max_lon, max_lat = frame.total_bounds
    assert 11 < min_lon < max_lon < 15.5
    assert 54.5 < min_lat < max_lat < 57.5
    assert max_lon - min_lon < 1
    assert max_lat - min_lat < 1


def test_feature_id_and_required_property_contract() -> None:
    records = _feature_records(_assembled().to_crs("EPSG:4326"))
    assert [record["id"] for record in records] == [
        record["properties"]["hex_id"] for record in records
    ]
    assert list(records[0]["properties"]) == list(DELIVERY_PROPERTY_FIELDS)
    assert set(records[0]["properties"]) == set(PROPERTY_DESCRIPTIONS)


def test_experimental_and_unrequested_fields_are_absent() -> None:
    properties = _feature_records(_assembled().to_crs("EPSG:4326"))[0]["properties"]
    excluded = {
        "connectivity_medium_score",
        "riparian_strong_score",
        "candidate_pixels",
        "habitat_context_adjacent_fraction",
        "contribution",
        "temporary_rank",
    }
    assert excluded.isdisjoint(properties)


def test_deterministic_rounding_for_scores_fractions_and_hectares() -> None:
    frame = _assembled().drop(columns="geometry")
    rounded = round_delivery_properties(frame)
    assert rounded.loc[0, DELIVERY_SCORE_FIELDS[0]] == 2.235
    assert rounded.loc[0, DELIVERY_FRACTIONS[0]] == 0.23457
    assert rounded.loc[0, DELIVERY_HECTARE_FIELDS[0]] == 20.57
    assert rounded.loc[0, DELIVERY_INTEGER_FIELDS[0]] == 4


def test_boolean_boundary_flag_and_no_nonfinite_serialization() -> None:
    frame = _assembled()
    rounded = round_delivery_properties(frame.drop(columns="geometry"))
    assert rounded[BOUNDARY_FLAG].dtype == bool
    assert rounded[BOUNDARY_FLAG].tolist() == [True, False]
    with pytest.raises(WebDeliveryError, match="non-serializable or non-finite"):
        serialize_geojson(
            [{"type": "Feature", "id": "x", "properties": {"x": np.nan}, "geometry": None}]
        )


def test_deterministic_feature_order() -> None:
    records = _feature_records(_assembled().to_crs("EPSG:4326"))
    assert [record["id"] for record in records] == ["h_01", "h_02"]
    assert serialize_geojson(records) == serialize_geojson(records)


def test_duplicate_candidate_id_rejected() -> None:
    candidate, scores, components = _sources()
    candidate.loc[1, "hex_id"] = "h_02"
    with pytest.raises(WebDeliveryError, match="duplicate hex_id"):
        assemble_delivery_frame(candidate, scores, components, expected_count=None)


def test_missing_component_source_id_rejected() -> None:
    candidate, scores, components = _sources()
    components["Habitat Context"] = components["Habitat Context"].iloc[:1]
    with pytest.raises(WebDeliveryError, match="does not reconcile exactly"):
        assemble_delivery_frame(candidate, scores, components, expected_count=None)


def test_output_geojson_parses_successfully() -> None:
    records = _feature_records(_assembled().to_crs("EPSG:4326"))
    parsed = json.loads(serialize_geojson(records))
    assert parsed["type"] == "FeatureCollection"
    assert len(parsed["features"]) == 2


def test_metadata_property_contract_matches_actual_geojson() -> None:
    records = _feature_records(_assembled().to_crs("EPSG:4326"))
    metadata_property_names = list(PROPERTY_DESCRIPTIONS)
    assert metadata_property_names == list(records[0]["properties"])
    assert metadata_property_names == list(DELIVERY_PROPERTY_FIELDS)


def test_metadata_preset_weights_match_canonical_model_definitions(tmp_path) -> None:
    path = tmp_path / "presets.json"
    path.write_text(json.dumps(preset_metadata()), encoding="utf-8")
    assert _validate_preset_metadata(path) == preset_metadata()


def test_source_values_reconcile_with_rounded_delivery_values() -> None:
    frame = _assembled().drop(columns="geometry")
    delivered = round_delivery_properties(frame)
    audit = _rounding_audit(frame, delivered)
    assert audit["unexpected_mismatch_count"] == 0
    assert audit["maximum_absolute_rounding_difference"] > 0
    assert not audit["rankings_recomputed_from_rounded_values"]


def test_shortlists_use_full_precision_scores_before_delivery_rounding() -> None:
    frame = _assembled()
    frame.loc[frame["hex_id"] == "h_02", "balanced_score"] = 80.0004
    frame.loc[frame["hex_id"] == "h_01", "balanced_score"] = 80.0003

    shortlists, audit = build_candidate_shortlists(frame, top_n=2)

    assert [entry["hex_id"] for entry in shortlists["balanced"]] == ["h_02", "h_01"]
    assert [entry["balanced_score"] for entry in shortlists["balanced"]] == [80.0, 80.0]
    assert audit["presets"]["balanced"]["ranked_from"] == "balanced_score"


def test_shortlists_tie_break_by_hex_id_and_have_explicit_ranks() -> None:
    frame = _assembled()
    for preset_field in PRESET_SCORE_FIELDS.values():
        frame[preset_field] = 42.5

    shortlists, _ = build_candidate_shortlists(frame, top_n=2)

    for entries in shortlists.values():
        assert [entry["hex_id"] for entry in entries] == ["h_01", "h_02"]
        assert [entry["rank"] for entry in entries] == [1, 2]


def test_shortlist_values_follow_delivery_rounding_contract() -> None:
    shortlists, _ = build_candidate_shortlists(_assembled(), top_n=2)
    entry = next(item for item in shortlists["balanced"] if item["hex_id"] == "h_02")

    assert entry["balanced_score"] == 1.123
    assert entry["habitat_context_score"] == 1.123
    assert entry["habitat_context_local_fraction"] == 0.12346
    assert entry["candidate_land_area_ha"] == 10.12
    assert entry["nearest_protected_hex_steps"] == 5
    assert entry["boundary_edge_flag"] is False
    assert len(f"{entry['longitude']:.6f}".split(".")[1]) == 6
    assert len(f"{entry['latitude']:.6f}".split(".")[1]) == 6


def test_shortlist_centroid_is_projected_then_transformed_and_in_bounds() -> None:
    frame = _assembled()
    shortlists, audit = build_candidate_shortlists(frame, top_n=2)
    entry = next(item for item in shortlists["balanced"] if item["hex_id"] == "h_02")

    geometry = frame.loc[frame["hex_id"] == "h_02", "geometry"].iloc[0]
    expected = gpd.GeoSeries([geometry.centroid], crs="EPSG:3006").to_crs("EPSG:4326").iloc[0]
    assert (entry["longitude"], entry["latitude"]) == (
        round(expected.x, 6),
        round(expected.y, 6),
    )
    assert audit["centroid_source_crs"] == "EPSG:3006"
    assert audit["centroid_output_crs"] == "EPSG:4326"
    assert audit["presets"]["balanced"]["coordinates_within_skane_bounds"]


def test_shortlists_include_only_finalized_presets_and_reconcile_ids() -> None:
    frame = _assembled()
    shortlists, _ = build_candidate_shortlists(frame, top_n=2)

    assert set(shortlists) == {"balanced", "connectivity_first", "riparian_restoration"}
    candidate_ids = set(frame["hex_id"])
    for entries in shortlists.values():
        assert len(entries) == 2
        assert {entry["hex_id"] for entry in entries} <= candidate_ids
        assert all(np.isfinite([entry["longitude"], entry["latitude"]]).all() for entry in entries)


def test_shortlist_generation_is_deterministic() -> None:
    first, first_audit = build_candidate_shortlists(_assembled(), top_n=2)
    second, second_audit = build_candidate_shortlists(_assembled(), top_n=2)

    assert first == second
    assert first_audit == second_audit


def test_default_shortlist_top_n_is_exactly_fifty() -> None:
    frame = pd.concat([_assembled()] * 25, ignore_index=True)
    frame["hex_id"] = [f"h_{index:03d}" for index in range(len(frame))]
    shortlists, _ = build_candidate_shortlists(frame, top_n=TOP_N)

    assert TOP_N == 50
    for entries in shortlists.values():
        assert len(entries) == TOP_N
        assert [entry["rank"] for entry in entries] == list(range(1, TOP_N + 1))
