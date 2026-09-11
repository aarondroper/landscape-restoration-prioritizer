import type {
  ExpressionSpecification,
  FillLayerSpecification,
  FilterSpecification,
  LineLayerSpecification,
} from "maplibre-gl";
import type { PresetScoreField } from "../data/types";
import type { ComponentScoreField, WeightVector } from "../data/types";
import {
  CANDIDATE_BOUNDARY_LAYER_ID,
  CANDIDATE_FILL_LAYER_ID,
  CANDIDATE_OUTLINE_LAYER_ID,
  CANDIDATE_SELECTED_FILL_LAYER_ID,
  CANDIDATE_SELECTED_HALO_LAYER_ID,
  CANDIDATE_SELECTED_OUTLINE_LAYER_ID,
  CANDIDATE_SOURCE_ID,
} from "./mapConfig";
import { SCORE_RAMP, SCORE_RAMP_CSS } from "../lib/scoreScale";

export { SCORE_RAMP, SCORE_RAMP_CSS };

export function getCandidateFillPaint(
  scoreField: PresetScoreField | WeightVector,
): FillLayerSpecification["paint"] {
  const scoreExpression: ExpressionSpecification = typeof scoreField === "string"
    ? ["get", scoreField]
    : getWeightedScoreExpression(scoreField);
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

function getWeightedScoreExpression(weights: WeightVector): ExpressionSpecification {
  const fields: ComponentScoreField[] = [
    "habitat_context_score",
    "ecological_network_score",
    "riparian_opportunity_score",
    "protected_area_reinforcement_score",
    "restoration_land_availability_score",
  ];
  const terms = fields.map((field) => ["*", ["get", field], weights[field]]);
  return ["/", ["+", ...terms], fields.reduce((sum, field) => sum + weights[field], 0)] as ExpressionSpecification;
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
