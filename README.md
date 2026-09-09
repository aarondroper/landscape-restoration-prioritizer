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

Ecological Network Context is implemented for the MVP using the finalized
configuration-normalized input. The remaining prioritization components and
overall score remain deferred.

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
