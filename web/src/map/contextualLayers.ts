import type { FillLayerSpecification, LineLayerSpecification } from "maplibre-gl";
import {
  CANDIDATE_FILL_LAYER_ID,
  PROTECTED_AREAS_SOURCE_ID,
  PROTECTED_AREAS_FILL_LAYER_ID,
  PROTECTED_AREAS_OUTLINE_LAYER_ID,
  WETLAND_INLAND_WATER_SOURCE_ID,
  WETLAND_INLAND_WATER_FILL_LAYER_ID,
  WETLAND_INLAND_WATER_OUTLINE_LAYER_ID,
} from "./mapConfig";

export const CONTEXT_LAYER_COLORS = {
  protectedFill: "#8b7f98",
  protectedOutline: "#6d627b",
  wetlandFill: "#6f9eae",
  wetlandOutline: "#496f7d",
} as const;

export type ContextLayerId = "protectedAreas" | "wetlandInlandWater";
export type ContextLayerStatus = "idle" | "loading" | "ready" | "error";
export type ContextLayerStatuses = Record<ContextLayerId, ContextLayerStatus>;

export const protectedAreasFillLayer: FillLayerSpecification = {
  id: PROTECTED_AREAS_FILL_LAYER_ID,
  type: "fill",
  source: PROTECTED_AREAS_SOURCE_ID,
  minzoom: 5,
  maxzoom: 14,
  paint: {
    "fill-color": CONTEXT_LAYER_COLORS.protectedFill,
    "fill-opacity": 0.17,
  },
};

export const protectedAreasOutlineLayer: LineLayerSpecification = {
  id: PROTECTED_AREAS_OUTLINE_LAYER_ID,
  type: "line",
  source: PROTECTED_AREAS_SOURCE_ID,
  minzoom: 5,
  paint: {
    "line-color": CONTEXT_LAYER_COLORS.protectedOutline,
    "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 10, 0.8, 14, 1.15],
    "line-opacity": 0.58,
  },
};

export const wetlandInlandWaterFillLayer: FillLayerSpecification = {
  id: WETLAND_INLAND_WATER_FILL_LAYER_ID,
  type: "fill",
  source: WETLAND_INLAND_WATER_SOURCE_ID,
  minzoom: 5,
  maxzoom: 14,
  paint: {
    "fill-color": CONTEXT_LAYER_COLORS.wetlandFill,
    "fill-opacity": [
      "interpolate",
      ["linear"],
      ["zoom"],
      5,
      0.15,
      7,
      0.17,
      8,
      0.19,
      10,
      0.22,
      11,
      0.25,
      14,
      0.26,
    ],
  },
};

export const wetlandInlandWaterOutlineLayer: LineLayerSpecification = {
  id: WETLAND_INLAND_WATER_OUTLINE_LAYER_ID,
  type: "line",
  source: WETLAND_INLAND_WATER_SOURCE_ID,
  minzoom: 5,
  paint: {
    "line-color": CONTEXT_LAYER_COLORS.wetlandOutline,
    "line-width": [
      "interpolate",
      ["linear"],
      ["zoom"],
      5,
      0.2,
      7,
      0.25,
      8,
      0.3,
      10,
      0.5,
      11,
      0.75,
      14,
      0.9,
    ],
    "line-opacity": [
      "interpolate",
      ["linear"],
      ["zoom"],
      5,
      0.08,
      7,
      0.12,
      8,
      0.16,
      10,
      0.3,
      11,
      0.5,
      14,
      0.55,
    ],
  },
};

export const CONTEXT_LAYER_ORDER = [
  WETLAND_INLAND_WATER_FILL_LAYER_ID,
  WETLAND_INLAND_WATER_OUTLINE_LAYER_ID,
  PROTECTED_AREAS_FILL_LAYER_ID,
  PROTECTED_AREAS_OUTLINE_LAYER_ID,
] as const;

export const CONTEXT_LAYER_CONFIG = {
  protectedAreas: {
    sourceId: PROTECTED_AREAS_SOURCE_ID,
    dataUrl: "/data/protected_areas.geojson",
    layers: [protectedAreasFillLayer, protectedAreasOutlineLayer],
  },
  wetlandInlandWater: {
    sourceId: WETLAND_INLAND_WATER_SOURCE_ID,
    dataUrl: "/data/wetland_inland_water.geojson",
    layers: [wetlandInlandWaterFillLayer, wetlandInlandWaterOutlineLayer],
  },
} as const;

export function reorderContextLayers(map: {
  getLayer: (id: string) => unknown;
  moveLayer: (id: string, before?: string) => unknown;
}) {
  for (const layerId of CONTEXT_LAYER_ORDER) {
    if (map.getLayer(layerId)) map.moveLayer(layerId, CANDIDATE_FILL_LAYER_ID);
  }
}
