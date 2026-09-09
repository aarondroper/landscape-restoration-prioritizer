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

Step 3 implements the first reproducible data-ingestion slice: the SCB DeSO
2025 administrative/statistical study extent for Skåne. The generated boundary
is not yet the terrestrial candidate-analysis mask; candidate-land logic will
later use NMD-based processing.

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
