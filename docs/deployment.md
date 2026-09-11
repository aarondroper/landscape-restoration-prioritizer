# Deployment

The application is a static React/TypeScript site. Python processing produces
the delivery artifacts locally, Vite packages them with the frontend, and the
browser loads the resulting files from the same site origin. No API, database,
authentication, or runtime environmental-data service is required.

## Build

From the repository root, after the analytical source-data prerequisites are
available:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m restoration_prioritizer.prioritization_model
.venv/bin/python -m restoration_prioritizer.web_delivery

cd web
npm ci
npm run build
```

The frontend build runs `npm run data:sync`, which copies generated delivery
artifacts into ignored `web/public/data/` files.

## Cloudflare Pages

The production project is `landscape-restoration-prioritizer`. Deploy the built
static directory from `web/` with:

```bash
npx wrangler pages deploy dist --project-name=landscape-restoration-prioritizer
```

Wrangler must be authenticated through its normal login flow. The application
assumes deployment at the site root because its data requests use absolute paths
such as `/data/candidates.geojson`.

The current production URL is:

https://landscape-restoration-prioritizer.pages.dev/

The host should serve JSON and GeoJSON with suitable content types and enable
gzip or Brotli compression for JSON, GeoJSON, JavaScript, and CSS. Cloudflare
Pages limits a single static asset to 25 MiB (`26,214,400` bytes); the data-sync
script guards the candidate GeoJSON against reaching that limit.

The protected-area and wetland/inland-water assets are optional and lazy-loaded
by the frontend. A custom domain is not required by the application.
