# Landscape Restoration Prioritizer

Landscape Restoration Prioritizer is a regional habitat-restoration screening
tool for Skåne län, Sweden. It is designed to help a conservation organization
identify areas for further investigation of opportunities to strengthen
ecological networks, improve riparian function, and expand semi-natural habitat.

The planned analytical pipeline uses approximately 500 m hexagonal candidate
cells, five independently reported relative component scores, and configurable
weighted presets. It is decision support—not a parcel-level restoration
recommendation or a scientific probability model.

## Status

Habitat Context, Ecological Network Context, Riparian Opportunity,
Protected-Area Reinforcement, and Restoration Land Availability are implemented
for the MVP using their finalized component definitions. The equal-weight
prioritization baseline and the Connectivity First and Riparian Restoration
scenario presets are finalized for the MVP.

## Intended architecture

Authoritative public datasets will later flow through reproducible Python
ingestion and geospatial processing into normalized analytical indicators,
candidate analysis units, and a lightweight web-ready artifact for a React +
MapLibre frontend. Delivery format and any use of local PostGIS remain open
until data and analytical needs are understood.

## Development setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the checks with:

```bash
python -m pytest
ruff check .
ruff format --check .
```

Raw, interim, processed, and generated geospatial data are excluded from Git;
see [`docs/methodology.md`](docs/methodology.md) and
[`docs/data-sources.md`](docs/data-sources.md) for the current contract and
source-planning status.

## Generate the study-area artifact

With the project environment active, run:

```bash
python -m restoration_prioritizer.study_area
```

This uses the SCB WFS server-side filter `lanskod='12'`, saves the exact
filtered response under `data/raw/scb/`, and writes the dissolved one-feature
GeoPackage and provenance manifest under `data/processed/`.

## Generate the NMD2023 Skåne raster

With the project environment active, run:

```bash
python -m restoration_prioritizer.nmd
```

The first run downloads and CRC-checks the official NMD2023 v2.1 ZIP (about
2.5 GiB) under ignored raw data. It extracts only the required raster and
coverage GeoPackage, verifies terrestrial Skåne coverage, writes the native
10 m subset to `data/processed/nmd/nmd2023_v2_1_skane.tif`, records factual
provenance, and removes the expanded national interim files after success.
Reruns reuse a valid cached archive and regenerate the downstream artifact.
The command fails closed if valid terrestrial pixels fall outside current
v2.x coverage metadata.

## Audit the NMD2023 semantic contract

With the processed Skåne raster present, run:

```bash
python -m restoration_prioritizer.nmd_semantics
```

This performs a block-wise semantic audit and writes the ignored JSON report
to `data/processed/nmd/nmd2023_v2_1_semantic_audit.json`. It does not create
semantic-mask rasters.

## Generate deterministic NMD analysis units

With the processed Skåne raster and Step 5 semantic audit present, run:

```bash
python -m restoration_prioritizer.analysis_units
```

This writes the ignored regular pointy-top hexagon layer to
`data/processed/analysis_units.gpkg` and its generated audit/provenance summary
to `data/processed/analysis_units.provenance.json`. Complete hexagons are
retained; only cells with zero terrestrial NMD pixels are removed. The command
reports candidate-fraction and candidate-area threshold sensitivities as
diagnostics only and does not select an eligibility threshold.

## Generate the MVP candidate population

With the Step 6 analysis-unit artifact present, run:

```bash
python -m restoration_prioritizer.candidate_units
```

This applies the fixed inclusive rule `candidate_area_m2 >= 50_000` and
`candidate_fraction_of_terrestrial >= 0.25`, then writes the ignored candidate
layer to `data/processed/candidate_units.gpkg` and its specific audit/provenance
summary to `data/processed/candidate_units.provenance.json`. It retains the
complete regular hexagons and does not calculate ecological indicators or
prioritization scores.

## Generate raw Habitat Context indicators

With the Step 6 and Step 7 processed artifacts present, run:

```bash
python -m restoration_prioritizer.habitat_context
```

This uses the full terrestrial analysis grid for context and the candidate
layer only as the focal population. It writes the ignored keyed indicator
table to `data/processed/indicators/habitat_context.csv` and its component
provenance/audit report to
`data/processed/indicators/habitat_context.provenance.json`. The immediate and
local indicators are raw terrestrial-pixel fractions; they are not normalized
or scored.

## Generate the Habitat Context component score

With the Step 8 raw indicator and candidate artifacts present, run:

```bash
python -m restoration_prioritizer.habitat_context_score
```

This ranks `habitat_context_local_fraction` empirically within the eligible
candidate population and writes the ignored component table and audit
provenance under `data/processed/components/`. The first-ring indicator is
retained for diagnostics only.

## Generate raw Ecological Network Context indicators

With the Step 6, Step 7, Step 8, and Step 9 artifacts present, run:

```bash
python -m restoration_prioritizer.ecological_network
```

This evaluates the three opposing first-ring hex-grid axes using the minimum
habitat fraction on each pair of sides, plus two raw configuration ratios. It
writes unselected indicators and diagnostics to
`data/processed/indicators/ecological_network.csv`, with provenance in the
corresponding `.provenance.json` file. No network score is selected or
normalized in this step.

## Generate the Ecological Network Context component score

With the raw Ecological Network Context, candidate, and Habitat Context
artifacts present, run:

```bash
python -m restoration_prioritizer.ecological_network_score
```

This selects `opposing_balance_ratio` and directly scales it as
`ecological_network_score = 100 * opposing_balance_ratio`. It writes the
narrow component table and its audit/provenance manifest under
`data/processed/components/`. The other raw network indicators remain
available as diagnostics and do not contribute to the score.

## Generate raw Riparian Opportunity indicators

With the processed NMD raster, study area, candidate layer, and finalized
Habitat Context and Ecological Network Context artifacts present, run:

```bash
python -m restoration_prioritizer.riparian_opportunity
```

This derives raw wetland-plus-inland-water context directly from NMD2023,
including inland-water-only grid positions omitted from the terrestrial Step 6
analysis-unit artifact. It writes the focal, adjacent, local, and
near-candidate raw indicator table to
`data/processed/indicators/riparian_opportunity.csv` and its audit/provenance
manifest to the corresponding `.provenance.json` file. The near-candidate
field is evaluated as the raw `max(focal, adjacent)` signal; it is not a score
or normalization. The focal, adjacent, near, and local raw fields remain
available for audit and future explanation.

## Generate the Riparian Opportunity component score

With the raw Riparian Opportunity, candidate, Habitat Context, and Ecological
Network Context artifacts present, run:

```bash
python -m restoration_prioritizer.riparian_opportunity_score
```

This selects `riparian_focal_fraction` as the sole scoring input. A zero raw
value remains score 0; positive candidates receive
`100 * average_positive_rank / positive_candidate_count`, with average ranks
for positive ties. It writes the component table and its component-specific
audit/provenance manifest under `data/processed/components/`. Adjacent, near,
and local raw indicators remain diagnostic and do not contribute to the score.

## Generate the Protected-Area Reinforcement source footprint

Run the live Naturvårdsverket ingestion with:

```bash
python -m restoration_prioritizer.protected_areas
```

This retrieves only national parks, nature reserves, and Natura 2000 `SCI`,
`SPA`, and `SPA/SCI` features intersecting the Skåne study geometry plus a
5 km outside-context buffer. It writes the normalized `national_protection`,
`natura2000`, and physically deduplicated `protected_footprint` layers to
`data/processed/protected_areas.gpkg`, with source-specific provenance in
`data/processed/protected_areas.provenance.json`. The 5 km value is source
context only; no Protected-Area Reinforcement score or distance threshold is
defined here.

## Generate raw Protected-Area Reinforcement indicators

With the processed protected footprint, NMD raster, Step 6 grid, candidate
population, and finalized component artifacts present, run:

```bash
python -m restoration_prioritizer.protected_area_reinforcement
```

This derives a grid-scale terrestrial protected-support table by requiring
both pixel-center membership in the approved `protected_footprint` and the
NMD `terrestrial_land` semantic role. It writes raw focal, adjacent, local,
and nearest-hex-step diagnostics for all candidates to
`data/processed/indicators/protected_area_reinforcement.csv`, a compact
terrestrial grid table to `data/processed/protected_terrestrial_grid.csv`, and
the audit/provenance manifest to the corresponding `.provenance.json` path.

## Generate the Protected-Area Reinforcement component

With the raw indicator, candidate population, and finalized component
artifacts present, run:

```bash
python -m restoration_prioritizer.protected_area_reinforcement_score
```

This uses `nearest_protected_hex_steps` as the sole scoring input. Distance
zero receives 100; positive distances receive the approved reverse empirical
average-rank score among non-overlap candidates. Focal, adjacent, and local
protected-terrestrial fractions remain supporting diagnostics. The component
artifact and component-specific provenance are written under
`data/processed/components/`.

## Generate raw mapped land-restoration feasibility indicators

With the Step 6 analysis grid, Step 7 candidate population, and finalized
component artifacts present, run:

```bash
python -m restoration_prioritizer.land_restoration_feasibility
```

This writes the five raw primitives—candidate arable hectares, candidate arable
fraction, focal artificial burden, adjacent artificial context, and local
artificial context—to
`data/processed/indicators/land_restoration_feasibility.csv`, with the audit
manifest in the corresponding `.provenance.json` file. It reuses the Step 5
NMD roles (`primary_candidate` = class 3 and `artificial_constraint` = classes
51–53) and the existing Step 6/7 counts. This is a mapped land-availability /
development-context proxy, not cadastral or socioeconomic feasibility. No raw
indicators are combined, normalized, weighted, or scored by this raw-indicator
step.

## Generate the Restoration Land Availability component

With the Step 19 raw indicator artifact, the candidate population, and the four
finalized ecological component artifacts present, run:

```bash
python -m restoration_prioritizer.land_restoration_feasibility_score
```

This finalizes the historical “Land-Restoration Feasibility” component under
the narrower user-facing interpretation **Restoration Land Availability**.
It ranks `candidate_land_area_ha` across all eligible candidates using the
empirical average-rank formula
`100 * (rank - 1) / (N - 1)`. Candidate fraction is retained only in the
audit, and focal/adjacent/local artificial burden remains diagnostic context;
none of those fields contributes to the score. The component artifact and
component-specific provenance are written under `data/processed/components/`.
This is a relative mapped-land screening measure, not full implementation
feasibility; a 500 m hex is an analytical unit, not a parcel.

## Preserve the equal-weight prioritization baseline

The historical Step 21 `build_balanced_baseline()` function remains available
for reproducibility and writes `balanced_baseline.csv` plus its provenance under
`data/processed/prioritization/`. It integrates the authoritative component
scores with equal 20% weights and does not recalculate or re-normalize any
component. The canonical command below now writes the finalized three-preset
MVP artifact.

## Run the controlled preset sensitivity study

With the approved equal-weight baseline and all five finalized component
artifacts present, run:

```bash
python -m restoration_prioritizer.preset_sensitivity
```

This evaluates exactly three Connectivity First and three Riparian Restoration
scenario vectors against the `balanced_reference`, writes the analytical
`preset_sensitivity.csv` table and its provenance JSON, and reports ranking,
tail-overlap, component-tradeoff, availability, weakness, churn, mover, and
spatial diagnostics. It does not search weights or finalize a thematic preset.

## Generate the canonical MVP prioritization model (Step 23)

With the five finalized component artifacts and approved Step 22 sensitivity
artifacts present, run:

```bash
python -m restoration_prioritizer.prioritization_model
```

This writes the canonical candidate-level table to
`data/processed/prioritization/prioritization_scores.csv`, detailed final-model
provenance to
`data/processed/prioritization/prioritization_scores.provenance.json`, and
stable application-facing preset metadata to
`data/processed/prioritization/presets.json`. The final presets are exactly
`balanced`, `connectivity_first`, and `riparian_restoration`; each uses the
direct weighted mean of the five finalized 0–100 component scores. The command
does not recalculate raw indicators, download environmental datasets, or
generate frontend/map-delivery artifacts.

The earlier `build_balanced_baseline()` function and
`balanced_baseline.csv` artifact remain available for Step 21 reproducibility.
