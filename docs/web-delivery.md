# Web-delivery candidate dataset

Step 24 materializes the first web-delivery contract for the Landscape
Restoration Prioritizer. It is a delivery adapter over approved analytical
artifacts; it does not change the model, scores, weights, candidate geometry,
or environmental data.

## Build command and outputs

Run from the repository root:

```bash
python -m restoration_prioritizer.web_delivery
```

The command writes:

- `data/processed/delivery/candidates.geojson`
- `data/processed/delivery/candidates.metadata.json`
- `data/processed/delivery/candidate_shortlists.json`

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
- Availability explanatory fields:
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

The three preset scores are delivered in the same feature so a later frontend
can switch presets without another network request. The canonical preset
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

Step 24 measures the actual uncompressed and gzip-equivalent payload, bytes per
feature, geometry coordinate counts, Polygon/MultiPolygon counts, and an
approximate geometry-versus-property payload breakdown. It also checks JSON
parsing, exact property presence, finite numeric values, source IDs, duplicate
feature IDs, source/transformed geometry validity, and plausible Skåne bounds.

The current architecture target is one static GeoJSON FeatureCollection. The
measured artifact determines whether that remains adequate. The final
GeoJSON-versus-vector-tile decision is intentionally pending browser and
delivery review; Step 24 does not add PMTiles, MBTiles, Tippecanoe, APIs,
PostGIS, or a MapLibre/React frontend.

The current real-artifact benchmark is:

- 26,395 features;
- 24,979,911 uncompressed bytes (946.4 bytes per feature);
- 4,021,798 bytes at gzip level 9 (152.4 gzip bytes per feature);
- 6.21:1 uncompressed-to-gzip compression ratio;
- approximately 42.38% geometry/feature-ID/`hex_id` payload and 57.62%
  additional properties by the documented difference benchmark.

The measured result is classified **CLEARLY SUITABLE FOR SINGLE GEOJSON** from
the artifact alone: every feature is a simple Polygon, the median and maximum
exterior ring coordinate counts are both 7, and all scores are precomputed.
Browser parse/render benchmarking remains an appropriate later check before
making a final production delivery decision.

## Interpretation caveats

This is a regional decision-support screening dataset, not a restoration
probability, legal designation, parcel dataset, or implementation decision.
The boundary flag is diagnostic only. The explanatory fields expose direct
raw inputs used by the finalized components; they are not additional model
components. No environmental datasets are downloaded by the delivery command.
