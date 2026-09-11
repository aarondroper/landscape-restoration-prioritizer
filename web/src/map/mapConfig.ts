import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

// OpenFreeMap provides the current no-key basemap style; deployment remains provider-agnostic.
export const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
export const CANDIDATE_DATA_URL = "/data/candidates.geojson";
export const CANDIDATE_SOURCE_ID = "candidates";
export const CANDIDATE_FILL_LAYER_ID = "candidate-fill";
export const CANDIDATE_OUTLINE_LAYER_ID = "candidate-hover-outline";
export const CANDIDATE_BOUNDARY_LAYER_ID = "candidate-boundaries";
export const CANDIDATE_SELECTED_FILL_LAYER_ID = "candidate-selected-fill";
export const CANDIDATE_SELECTED_HALO_LAYER_ID = "candidate-selected-halo";
export const CANDIDATE_SELECTED_OUTLINE_LAYER_ID = "candidate-selected-outline";
export const PROTECTED_AREAS_SOURCE_ID = "protected-areas";
export const PROTECTED_AREAS_FILL_LAYER_ID = "protected-areas-fill";
export const PROTECTED_AREAS_OUTLINE_LAYER_ID = "protected-areas-outline";
export const WETLAND_INLAND_WATER_SOURCE_ID = "wetland-inland-water";
export const WETLAND_INLAND_WATER_FILL_LAYER_ID = "wetland-inland-water-fill";
export const WETLAND_INLAND_WATER_OUTLINE_LAYER_ID = "wetland-inland-water-outline";

// MapLibre GL JS v6 is ESM-only and needs an explicit worker URL in Vite builds.
setWorkerUrl(workerUrl);
