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

## Hosting: Cloudflare Pages Direct Upload

The selected host is **Cloudflare Pages using Direct Upload**. Git-integrated
Pages builds are intentionally not used: the repository does not track the
generated environmental/model delivery artifacts, and the validated local
`web/dist/` directory is the reproducible production input. Cloudflare is not
expected to run the Python/geospatial processing pipeline.

From `web/`, deploy the already-built static assets with:

~~~bash
npx wrangler pages deploy dist --project-name=landscape-restoration-prioritizer
~~~

The production Pages project is
[`landscape-restoration-prioritizer.pages.dev`](https://landscape-restoration-prioritizer.pages.dev/).
The first production deployment completed on `main` from Step 28 commit
`ea2aede`; its deployment-specific URL is
[`c327558a.landscape-restoration-prioritizer.pages.dev`](https://c327558a.landscape-restoration-prioritizer.pages.dev/).
Deployment date/time UTC is recorded as `2026-09-11T09:23:11Z`, the Cloudflare
response timestamp observed immediately after the successful upload. No
custom domain is configured.

Wrangler must be authenticated with the normal interactive flow:

~~~bash
npx wrangler login
~~~

The Vite configuration assumes deployment at the site root. The application
requests absolute paths such as `/data/candidates.geojson` and
`/data/presets.json`; no repository subpath is supported. A custom domain is
deferred.

The static host must serve the copied JSON/GeoJSON assets without rewriting
their content types and should enable normal HTTP compression:

- gzip and/or Brotli for `application/json`, GeoJSON, and other static JSON assets;
- gzip and/or Brotli for JavaScript and CSS.

Cloudflare Pages has a 25 MiB single-static-asset limit, exactly
`25 * 1024 * 1024 = 26,214,400` bytes. The current
`web/dist/data/candidates.geojson` is 24,979,911 bytes, leaving 1,234,489
bytes (1.1773 MiB) of margin. `web/scripts/sync-data.mjs` fails the sync/build
at or above the exact limit. This guard does not alter the GeoJSON contents.

The approximately 25 MB uncompressed candidate GeoJSON is about 4.02 MB with gzip level 9 in the local artifact measurement. Precompressed files are not committed.

The application uses MapLibre GL JS with an explicit Vite worker URL and OpenFreeMap's no-key basemap style. Production use therefore also depends on the selected host allowing outbound browser requests to the basemap service.

After deployment, validate the root page and each data asset with HTTP requests,
including a compression-negotiated request for `candidates.geojson`. Record
Cloudflare's `Content-Encoding`, `Content-Type`, `Cache-Control`, `ETag`, and
any `Content-Length`/transfer-size values. Normal edge compression and
revalidation semantics are expected; aggressive immutable caching is not
appropriate for the unhashed GeoJSON filename.

The production validation returned HTTP 200 for the root page, all four data
assets, and the built JavaScript, CSS, and MapLibre worker assets. The root was
served as `text/html; charset=utf-8`; the candidate file as
`application/geo+json`; and the three companion files as `application/json`.
With `Accept-Encoding: br, gzip, zstd`, Cloudflare returned the candidate with
`Content-Encoding: br`, `Cache-Control: public, max-age=0, must-revalidate`,
ETag `W/"ad9eb5d72dae2360f7cb18a532c4151e"`, no `Content-Length` header, and a
3,796,916-byte compressed transfer.

External real-browser production smoke validation completed successfully. The
validated production URL is
[`https://landscape-restoration-prioritizer.pages.dev/`](https://landscape-restoration-prioritizer.pages.dev/).

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

- [x] Authenticate Wrangler and deploy `web/dist/` to the Pages project.
- [x] Confirm Brotli response and correct content types for JSON/GeoJSON, JavaScript, and CSS.
- [x] Confirm the root path and all four data assets return HTTP 200.
- [x] Confirm the OpenFreeMap style endpoint is reachable from the deployed context.
- [ ] Confirm required data-source attribution and reuse terms for the chosen publication context.
- [ ] Make a software-license decision for the repository; no license is currently present.
- [x] Run the backend and frontend quality gates from the approved checkout.
- [x] Run production HTTP smoke checks for the app shell and all four data assets.
- [x] Perform a final external browser smoke check after deployment.
- [ ] Add a polished application screenshot and repository metadata before portfolio publication.

The analytical model, five component definitions, three preset values, static delivery contract, and approved MVP workflow are otherwise development-complete. Search, export, URL state, additional overlays, and mobile-specific redesign remain optional future enhancements rather than launch requirements.
