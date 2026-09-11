# Web delivery

The web-delivery command creates deterministic static artifacts from the
canonical analytical outputs. It does not recalculate scores, weights, or
geometry.

## Build command and artifacts

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

The frontend synchronization script copies the required artifacts into ignored
files under `web/public/data/` before development and production builds.

## Candidate delivery contract

The candidate source is one compact GeoJSON `FeatureCollection`, ordered by
ascending `hex_id`. Each feature has a feature-level `id` equal to `hex_id` and
retains its complete candidate hexagon geometry. Source geometry is read in
EPSG:3006 and transformed to EPSG:4326/WGS84 for browser delivery.

Every feature contains:

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

The three preset scores are delivered together so preset switching requires no
additional candidate-data request. The shortlist companion contains the top 50
candidates for each named preset, ranked from full-precision scores and then by
`hex_id`. The weight index contains the component scores and centroids needed for
Custom client-side ranking.

## Precision and determinism

Delivery serialization rounds only the web copy:

- scores and component values to 3 decimal places;
- fractions and ratios to 5 decimal places;
- candidate hectares to 2 decimal places;
- protected-network steps to integers; and
- EPSG:4326 coordinates to 6 decimal places.

Rankings are calculated before rounding. Outputs use stable feature ordering,
explicit property schemas, finite numeric values, and exact one-to-one joins by
`hex_id`.

## Contextual display assets

`protected_areas.geojson` is a terrestrial-semantic protected-area derivative.
`wetland_inland_water.geojson` is a generalized NMD wetland and inland-water
derivative using the same mapped classes as Riparian Opportunity, with sea
excluded. Both are EPSG:4326 polygon assets with empty feature properties.

The display derivatives use a 1 ha minimum patch and 15 m topology-preserving
simplification. They are not analytical inputs and do not replace the source
geometries or feed back into any score.

The frontend does not preload these optional assets. Each is fetched when its
Map Layers control is first enabled and retained in memory for later visibility
toggles. Candidate priority remains the primary map surface.

## Static architecture

The application is a static React/TypeScript site. MapLibre loads the candidate
GeoJSON as one source and uses an explicit Vite worker URL. The frontend assumes
deployment at the site root and requests assets using absolute `/data/` paths.
The current candidate dataset is a suitable single-GeoJSON payload because its
features are simple polygons with precomputed scores; no runtime API or database
is required.
