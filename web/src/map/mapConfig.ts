import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

// Provisional no-key basemap retained for this product pass; revisit with later delivery evidence.
export const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
export const CANDIDATE_DATA_URL = "/data/candidates.geojson";
export const CANDIDATE_SOURCE_ID = "candidates";
export const CANDIDATE_FILL_LAYER_ID = "candidate-fill";
export const CANDIDATE_OUTLINE_LAYER_ID = "candidate-hover-outline";
export const CANDIDATE_BOUNDARY_LAYER_ID = "candidate-boundaries";
export const CANDIDATE_SELECTED_FILL_LAYER_ID = "candidate-selected-fill";
export const CANDIDATE_SELECTED_HALO_LAYER_ID = "candidate-selected-halo";
export const CANDIDATE_SELECTED_OUTLINE_LAYER_ID = "candidate-selected-outline";

// MapLibre GL JS v6 is ESM-only and needs an explicit worker URL in Vite builds.
setWorkerUrl(workerUrl);
