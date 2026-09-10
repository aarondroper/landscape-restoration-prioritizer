import type { ExpressionSpecification, FillLayerSpecification, LineLayerSpecification } from "maplibre-gl";
import type { PresetScoreField } from "../data/types";
import {
  CANDIDATE_FILL_LAYER_ID,
  CANDIDATE_OUTLINE_LAYER_ID,
  CANDIDATE_SOURCE_ID,
} from "./mapConfig";

export function getCandidateFillPaint(scoreField: PresetScoreField): FillLayerSpecification["paint"] {
  const scoreExpression: ExpressionSpecification = ["get", scoreField];
  return {
    "fill-color": [
      "interpolate",
      ["linear"],
      scoreExpression,
      0,
      "#edf2f7",
      25,
      "#c6dbef",
      50,
      "#73a9cf",
      75,
      "#2d6a91",
      100,
      "#123b59",
    ],
    "fill-opacity": 0.66,
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
  filter: ["boolean", ["feature-state", "hover"], false],
  paint: {
    "line-color": "#0b2538",
    "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 10, 2, 14, 3],
    "line-opacity": 0.95,
  },
};
