# Landscape Restoration Prioritizer

A reproducible Skåne landscape-restoration screening model combining environmental geospatial analysis with an interactive decision-support application.

## What it does

The client question is:

> Where should a conservation organization investigate restoration opportunities first, and why?

The model works at regional screening scale across **Skåne, Sweden**. It helps compare candidate analysis units; it does not make parcel recommendations, estimate probability, or establish implementation feasibility.

## Application

The React + MapLibre application supports three finalized scenarios:

- **Balanced**
- **Connectivity First**
- **Riparian Restoration**

Users can inspect the regional candidate surface, discover the top five candidates for the active scenario, navigate directly to a candidate, and inspect why its relative score has the value shown. Scores are population-relative model scores for screening-scale decision support.

## Live demo

[Open the deployed application](https://landscape-restoration-prioritizer.pages.dev/)

## Model

Each candidate analysis unit is scored across five components:

- **Habitat Context** — surrounding mapped habitat context.
- **Ecological Network Context** — opposing-side habitat configuration.
- **Riparian Opportunity** — mapped focal wetland and inland-water context.
- **Protected-Area Reinforcement** — reinforcement context relative to terrestrial formal protection.
- **Restoration Land Availability** — mapped candidate arable land available within the analysis unit.

The three scenarios apply transparent, stakeholder-facing weights to these five finalized component scores. They are not learned coefficients or ecological probabilities.

## Data sources

The MVP uses:

- SCB DeSO 2025 for the Skåne study-area boundary;
- Naturvårdsverket NMD2023 Basskikt v2.1 for land-cover semantics;
- Naturvårdsverket protected-area data; and
- Natura 2000 data.

See [docs/data-sources.md](docs/data-sources.md) for source decisions, provenance, and attribution considerations.

## Architecture

~~~text
authoritative environmental data
    ↓
reproducible Python processing
    ↓
finalized candidate scores
    ↓
deterministic static delivery artifacts
    ↓
React + MapLibre application
~~~

The frontend is a static site. The delivery contract contains one candidate GeoJSON file plus metadata, preset metadata, and a deterministic candidate-shortlist JSON companion. No API, database, authentication, PMTiles, or hosting-provider configuration is required by the current MVP.

## Running locally

Create the Python environment and install development dependencies:

~~~bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
~~~

The analytical pipeline requires the source data and processing prerequisites documented in [docs/methodology.md](docs/methodology.md) and [docs/data-sources.md](docs/data-sources.md). Once those prerequisites are satisfied, generate the canonical model and web-delivery artifacts from the repository root:

~~~bash
.venv/bin/python -m restoration_prioritizer.prioritization_model
.venv/bin/python -m restoration_prioritizer.web_delivery
~~~

Build and preview the static frontend:

~~~bash
cd web
npm ci
npm run build
npm run preview
~~~

npm run build runs npm run data:sync automatically and copies the generated delivery artifacts into ignored web/public/data/ assets. The frontend currently assumes deployment at the site root; see [docs/production-readiness.md](docs/production-readiness.md) for the complete build contract and launch checklist.

## Methodology and caveats

This is regional, screening-scale decision support using population-relative component and scenario scores. Candidate units are deterministic nominal 500 m hexagons, not parcels, properties, or implementation sites. The outputs describe mapped environmental context and model weighting; they do not claim ecological quality, restoration probability, certainty, formal recommendation, landowner permission, or implementation feasibility.

The methodology and delivery contracts are documented in [docs/methodology.md](docs/methodology.md), [docs/web-delivery.md](docs/web-delivery.md), and [docs/application-ui.md](docs/application-ui.md).

## Technology

Python, GeoPandas, Rasterio, Shapely, PyProj, NumPy, and pandas power the geospatial pipeline and scoring model. The application uses React, TypeScript, Vite, MapLibre GL JS, and OpenFreeMap's no-key basemap style.

## Quality gates

~~~bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
cd web
npm ci
npm run data:sync
npm run lint
npm run typecheck
npm run build
~~~

## Application preview

A final application screenshot should be added here before public repository publication. No screenshot is committed yet.

## Project status

The MVP analytical model and application workflow are development-complete. Public deployment, a software-license decision, final repository metadata, and a polished application screenshot remain publication decisions rather than additional product features.
