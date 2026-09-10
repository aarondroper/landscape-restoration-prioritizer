import type {
  ExpressionSpecification,
  FillLayerSpecification,
  FilterSpecification,
  LineLayerSpecification,
} from "maplibre-gl";
import type { PresetScoreField } from "../data/types";
import {
  CANDIDATE_BOUNDARY_LAYER_ID,
  CANDIDATE_FILL_LAYER_ID,
  CANDIDATE_OUTLINE_LAYER_ID,
  CANDIDATE_SELECTED_FILL_LAYER_ID,
  CANDIDATE_SELECTED_HALO_LAYER_ID,
  CANDIDATE_SELECTED_OUTLINE_LAYER_ID,
  CANDIDATE_SOURCE_ID,
} from "./mapConfig";

export const SCORE_RAMP = [
  { value: 0, color: "#f1e9bf" },
  { value: 25, color: "#d6dea6" },
  { value: 40, color: "#abc99a" },
  { value: 55, color: "#77ae91" },
  { value: 70, color: "#4b9187" },
  { value: 85, color: "#286f72" },
  { value: 100, color: "#154f5b" },
] as const;

export const SCORE_RAMP_CSS = `linear-gradient(90deg, ${SCORE_RAMP.map(
  (stop) => `${stop.color} ${stop.value}%`,
).join(", ")})`;

export function getCandidateFillPaint(scoreField: PresetScoreField): FillLayerSpecification["paint"] {
  const scoreExpression: ExpressionSpecification = ["get", scoreField];
  return {
    "fill-color": [
      "interpolate",
      ["linear"],
      scoreExpression,
      ...SCORE_RAMP.flatMap((stop) => [stop.value, stop.color]),
    ],
    "fill-opacity": 0.78,
  };
}

export const candidateFillLayer: FillLayerSpecification = {
  id: CANDIDATE_FILL_LAYER_ID,
  type: "fill",
  source: CANDIDATE_SOURCE_ID,
  paint: getCandidateFillPaint("balanced_score"),
};

export const candidateHoverOutlineLayer: LineLayerSpecification = {
  id: CANDIDATE_OUTLINE_LAYER_ID,
  type: "line",
  source: CANDIDATE_SOURCE_ID,
  paint: {
    "line-color": "#193d42",
    "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.8, 10, 1.2, 14, 2],
    "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.72, 0],
  },
};

export const candidateBoundaryLayer: LineLayerSpecification = {
  id: CANDIDATE_BOUNDARY_LAYER_ID,
  type: "line",
  source: CANDIDATE_SOURCE_ID,
  paint: {
    "line-color": "#52766d",
    "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.15, 9, 0.35, 13, 0.8],
    "line-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.04, 8, 0.07, 11, 0.28, 13, 0.6],
  },
};

const NO_SELECTED_CANDIDATE_ID = "__no_selected_candidate__";

export function getCandidateSelectedFilter(selectedHexId?: string): FilterSpecification {
  return ["==", ["get", "hex_id"], selectedHexId ?? NO_SELECTED_CANDIDATE_ID];
}

export const candidateSelectedFillLayer: FillLayerSpecification = {
  id: CANDIDATE_SELECTED_FILL_LAYER_ID,
  type: "fill",
  source: CANDIDATE_SOURCE_ID,
  filter: getCandidateSelectedFilter(),
  paint: {
    "fill-color": "#f6f1d1",
    "fill-opacity": 0.2,
  },
};

export const candidateSelectedHaloLayer: LineLayerSpecification = {
  id: CANDIDATE_SELECTED_HALO_LAYER_ID,
  type: "line",
  source: CANDIDATE_SOURCE_ID,
  filter: getCandidateSelectedFilter(),
  layout: {
    "line-join": "round",
  },
  paint: {
    "line-color": "#f8f4df",
    "line-width": ["interpolate", ["linear"], ["zoom"], 5, 4, 10, 4.25, 14, 5],
    "line-opacity": 1,
  },
};

export const candidateSelectedOutlineLayer: LineLayerSpecification = {
  id: CANDIDATE_SELECTED_OUTLINE_LAYER_ID,
  type: "line",
  source: CANDIDATE_SOURCE_ID,
  filter: getCandidateSelectedFilter(),
  layout: {
    "line-join": "round",
  },
  paint: {
    "line-color": "#143d42",
    "line-width": ["interpolate", ["linear"], ["zoom"], 5, 2, 10, 2.5, 14, 3],
    "line-opacity": 1,
  },
};
