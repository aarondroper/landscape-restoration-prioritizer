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

Step 6 implements the deterministic 500 m flat-to-flat EPSG:3006 analysis grid
and factual NMD composition for each retained terrestrial unit. Candidate
eligibility, ecological context indicators, and scoring remain deferred.

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
