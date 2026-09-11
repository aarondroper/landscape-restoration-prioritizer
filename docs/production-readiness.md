# Production readiness

## Architecture

The MVP is a static frontend. The analytical pipeline runs outside the browser and writes deterministic delivery artifacts. Vite packages those artifacts with a React, TypeScript, and MapLibre application; the browser loads the resulting files directly from the same site origin.

The current delivery contract is one candidate GeoJSON source plus three JSON companions:

- `data/processed/delivery/candidates.geojson`
- `data/processed/delivery/candidates.metadata.json`
- `data/processed/delivery/candidate_shortlists.json`
- `data/processed/prioritization/presets.json`

No API, database, authentication, PMTiles, analytics, or hosting-provider integration is part of this MVP.

## Reproducible build sequence

The following assumes a clean checkout and that the analytical data-generation prerequisites are already satisfied:

~~~bash
# From the repository root
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

# Generate the canonical model and static delivery artifacts
.venv/bin/python -m restoration_prioritizer.prioritization_model
.venv/bin/python -m restoration_prioritizer.web_delivery

# Build the frontend
cd web
npm ci
npm run build
~~~

The frontend `prebuild` hook runs `npm run data:sync`. That script copies the four generated artifacts into ignored `web/public/data/` files and verifies the expected candidate count, metadata, and candidate-file hash.

The web-delivery command does not download environmental datasets. The upstream analytical pipeline and its prerequisites are documented in [methodology.md](methodology.md) and [data-sources.md](data-sources.md).

## Hosting assumptions

The Vite configuration is intentionally minimal and assumes deployment at the site root. The application requests absolute paths such as `/data/candidates.geojson` and `/data/presets.json`; it has not been configured for a repository subpath. A hosting provider and any provider-specific base path remain undecided.

The static host must serve the copied JSON/GeoJSON assets without rewriting their content types and should enable normal HTTP compression:

- gzip and/or Brotli for `application/json`, GeoJSON, and other static JSON assets;
- gzip and/or Brotli for JavaScript and CSS.

The approximately 25 MB uncompressed candidate GeoJSON is about 4.02 MB with gzip level 9 in the local artifact measurement. Precompressed files are not committed.

The application uses MapLibre GL JS with an explicit Vite worker URL and OpenFreeMap's no-key basemap style. Production use therefore also depends on the selected host allowing outbound browser requests to the basemap service.

## Production build measurement

Fresh `npm run build` output:

| Asset | Raw size | gzip size |
| --- | ---: | ---: |
| Total `web/dist` | 27,019,634 bytes | — |
| Main JavaScript | 1,292,664 bytes | 353,998 bytes |
| Main CSS | 93,761 bytes | 12,913 bytes |
| MapLibre worker | 506,723 bytes | 143,187 bytes |
| `candidates.geojson` | 24,979,911 bytes | 4,019,348 bytes |
| `candidate_shortlists.json` | 127,871 bytes | 11,911 bytes |
| `candidates.metadata.json` | 17,283 bytes | 3,593 bytes |
| `presets.json` | 897 bytes | 263 bytes |

The build contains one intended `candidates.geojson` file, no source maps, and no unexpected duplicate candidate dataset. Vite reports a non-fatal advisory because the main JavaScript chunk exceeds 500 kB after minification. This is recorded for deployment review; code splitting is not being introduced solely to remove the warning.

## Browser support

The MVP is intended for current evergreen desktop browsers with WebGL enabled. Legacy-browser support is not defined. Final deployment should include an external browser smoke check for the app shell, data requests, map rendering, hover, shortlist navigation, selection, preset switching, and the selected-area inspector.

## Launch checklist

- [ ] Choose and configure a static hosting provider; keep the site-root assumption or deliberately revise the Vite base path.
- [ ] Confirm gzip and/or Brotli responses and correct content types for JSON/GeoJSON, JavaScript, and CSS.
- [ ] Confirm required data-source attribution and reuse terms for the chosen publication context.
- [ ] Make a software-license decision for the repository; no license is currently present.
- [ ] Run the backend and frontend quality gates from a clean checkout.
- [ ] Run production-preview HTTP smoke checks for the app shell and all four data assets.
- [ ] Perform a final external browser smoke check after deployment.
- [ ] Add a polished application screenshot and repository metadata before portfolio publication.

The analytical model, five component definitions, three preset values, static delivery contract, and approved MVP workflow are otherwise development-complete. Search, export, URL state, additional overlays, and mobile-specific redesign remain optional future enhancements rather than launch requirements.
