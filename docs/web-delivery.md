# Web-delivery candidate dataset

The web-delivery command materializes the static delivery contract for the
Landscape Restoration Prioritizer. It is a delivery adapter over approved
analytical artifacts; it does not change the model, scores, weights, candidate
geometry, or environmental data.

## Build command and outputs

Run from the repository root:

```bash
python -m restoration_prioritizer.web_delivery
```

The command writes:

- `data/processed/delivery/candidates.geojson`
- `data/processed/delivery/candidates.metadata.json`
- `data/processed/delivery/candidate_shortlists.json`
- `data/processed/delivery/candidate_weight_index.json`
- `data/processed/delivery/protected_areas.geojson`
- `data/processed/delivery/wetland_inland_water.geojson`

The GeoJSON is a single compact UTF-8 RFC-compatible `FeatureCollection`,
ordered by ascending `hex_id`. Each feature has a feature-level `id` equal to
its `hex_id`. The output contains one feature for every eligible candidate
hexagon and no geometry variants.

## Canonical sources

- Geometry: `data/processed/candidate_units.gpkg`, layer `candidate_units`,
  source CRS EPSG:3006.
- Final scores: `data/processed/prioritization/prioritization_scores.csv`.
- Habitat explanatory field:
  `data/processed/components/habitat_context.csv`.
- Ecological-network explanatory field:
  `data/processed/components/ecological_network.csv`.
- Riparian explanatory field:
  `data/processed/components/riparian_opportunity.csv`.
- Protected-area explanatory fields:
  `data/processed/components/protected_area_reinforcement.csv`.
- Restoration Land Availability explanatory fields (historical filename):
  `data/processed/components/land_restoration_feasibility.csv`.

All joins are exact one-to-one joins by `hex_id`. The source geometry is
transformed from EPSG:3006 to EPSG:4326/WGS84 for browser delivery. Hexagons
are not regenerated, simplified, buffered, or otherwise reshaped.

## Property contract

Every feature contains exactly these properties:

```text
hex_id
balanced_score
connectivity_first_score
riparian_restoration_score
habitat_context_score
ecological_network_score
riparian_opportunity_score
protected_area_reinforcement_score
restoration_land_availability_score
habitat_context_local_fraction
opposing_balance_ratio
riparian_focal_fraction
nearest_protected_hex_steps
protected_focal_fraction
candidate_land_area_ha
artificial_focal_fraction
boundary_edge_flag
```

The three preset scores are delivered in the same feature so the frontend can
switch presets without another network request. The canonical preset
definitions remain those in `prioritization_model.py` and `presets.json`:

| Preset | Habitat | Network | Riparian | Protection | Availability |
| --- | ---: | ---: | ---: | ---: | ---: |
| Balanced | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 |
| Connectivity First | 0.20 | 0.30 | 0.10 | 0.25 | 0.15 |
| Riparian Restoration | 0.20 | 0.10 | 0.35 | 0.15 | 0.20 |

Scores and component values use the canonical analytical values as their
source. The delivery serialization rounds only the web copy:

- component and preset scores: 3 decimal places;
- fractions and ratios: 5 decimal places;
- candidate hectares: 2 decimal places;
- protected-network steps: integer;
- boundary flag: boolean;
- EPSG:4326 coordinates: 6 decimal places.

Rounding reconciliation is recorded in the metadata. The command does not
re-rank candidates from rounded values; analytical rankings remain based on
the full-precision canonical artifacts. Higher values mean stronger priority
under the selected preset or component.

## Candidate shortlist companion artifact

`candidate_shortlists.json` contains the top 50 candidates for each finalized
preset: `balanced`, `connectivity_first`, and `riparian_restoration`. It ranks
the full-precision canonical scores from
`data/processed/prioritization/prioritization_scores.csv` by preset score
descending, then `hex_id` ascending, and assigns explicit ranks 1 through 50.
Rounded display scores are never used to determine rank.

Each entry contains `rank`, `longitude`, `latitude`, and the same candidate
property contract as `candidates.geojson`. Centroids are calculated from the
EPSG:3006 candidate geometry in `candidate_units.gpkg`, then transformed to
EPSG:4326/WGS84; they are not calculated directly in geographic coordinates.
Entries use the same delivery rounding conventions as the GeoJSON: scores to 3
decimals, fractions and ratios to 5, candidate hectares to 2,
protected-network steps as integers, boundary flags as booleans, and
coordinates to 6 decimals. The artifact includes top-N, ranking, centroid,
precision, and preset metadata for static frontend validation.

## Delivery architecture assessment

The delivery audit measures the actual uncompressed and gzip-equivalent payload,
bytes per feature, geometry coordinate counts, Polygon/MultiPolygon counts, and an
approximate geometry-versus-property payload breakdown. It also checks JSON
parsing, exact property presence, finite numeric values, source IDs, duplicate
feature IDs, source/transformed geometry validity, and plausible Skåne bounds.

The current architecture is one static GeoJSON FeatureCollection consumed by
the React/MapLibre frontend. The approved MVP does not add PMTiles, MBTiles,
Tippecanoe, APIs, PostGIS, or a second candidate geometry representation.

The delivery command also creates two display-only contextual GeoJSON assets.
`protected_areas.geojson` is the terrestrial-semantic moderate protected-area
mask. `wetland_inland_water.geojson` is the moderate generalized NMD wetland
and inland-water mask using the exact `riparian_focal_fraction` classes, with
sea excluded. Both are EPSG:4326, contain polygonal geometry and empty feature
property objects, and use a 1 ha minimum patch plus 15 m
topology-preserving simplification. They are display derivatives; the
wetland/water asset does not replace or feed back into Riparian Opportunity or
any other score.

The frontend does not preload either environmental asset. Each is fetched from
the static `/data/` directory only when its Map Layers checkbox is first
enabled, then its in-memory MapLibre source is retained for visibility toggles
within the session. A failed optional fetch is reported beside its control and
can be retried without affecting candidate priority, weighting, shortlist, or
inspection behavior.

The current real-artifact benchmark is:

- 26,395 features;
- 21,796,432 uncompressed bytes (825.8 bytes per feature);
- 2,835,899 bytes at gzip level 9 (107.4 gzip bytes per feature);
- 7.69:1 uncompressed-to-gzip compression ratio;
- approximately 33.96% geometry/feature-ID/`hex_id` payload and 66.04%
  additional properties by the documented difference benchmark.

The measured result is classified **CLEARLY SUITABLE FOR SINGLE GEOJSON** from
the artifact alone: every feature is a simple Polygon, the median and maximum
exterior ring coordinate counts are both 7, and all scores are precomputed.
The approved MVP browser review found the single-GeoJSON delivery adequate for
the product workflow. Deployment should still validate the chosen host's HTTP
compression, content types, and end-to-end browser loading behavior.

## Interpretation caveats

This is a regional decision-support screening dataset, not a restoration
probability, legal designation, parcel dataset, or implementation decision.
The boundary flag is diagnostic only. The explanatory fields expose direct
raw inputs used by the finalized components; they are not additional model
components. No environmental datasets are downloaded by the delivery command;
the contextual assets are generated from existing local processed inputs.
