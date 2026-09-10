import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

// Temporary no-key basemap for technical validation; replace during visual design work.
export const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
export const CANDIDATE_DATA_URL = "/data/candidates.geojson";
export const CANDIDATE_SOURCE_ID = "candidates";
export const CANDIDATE_FILL_LAYER_ID = "candidate-fill";
export const CANDIDATE_OUTLINE_LAYER_ID = "candidate-hover-outline";

// MapLibre GL JS v6 is ESM-only and needs an explicit worker URL in Vite builds.
setWorkerUrl(workerUrl);
