import {
  COMPONENT_SCORE_FIELDS,
  PRESET_SCORE_FIELDS,
} from "../data/types";
import type {
  ActivePresetId,
  CandidateProperties,
  ComponentScoreField,
  PresetId,
  WeightIndexItem,
  WeightVector,
} from "../data/types";

export const COMPONENTS = [
  {
    field: COMPONENT_SCORE_FIELDS.habitatContext,
    label: "Habitat Context",
    description: "Surrounding structural habitat amount.",
    icon: "component-habitat.svg",
  },
  {
    field: COMPONENT_SCORE_FIELDS.ecologicalNetwork,
    label: "Ecological Network Context",
    description: "Opposing-side habitat configuration.",
    icon: "component-network.svg",
  },
  {
    field: COMPONENT_SCORE_FIELDS.riparianOpportunity,
    label: "Riparian Opportunity",
    description: "Focal mapped wetland and inland-water context.",
    icon: "component-riparian.svg",
  },
  {
    field: COMPONENT_SCORE_FIELDS.protectedAreaReinforcement,
    label: "Protected-Area Reinforcement",
    description: "Proximity to terrestrial formal protection.",
    icon: "component-protected.svg",
  },
  {
    field: COMPONENT_SCORE_FIELDS.restorationLandAvailability,
    label: "Restoration Land Availability",
    description: "Amount of mapped eligible arable land.",
    icon: "component-availability.svg",
  },
] as const;

export const COMPONENT_FIELDS = COMPONENTS.map((component) => component.field);
export const DEFAULT_RAW_WEIGHTS: WeightVector = {
  habitat_context_score: 20,
  ecological_network_score: 20,
  riparian_opportunity_score: 20,
  protected_area_reinforcement_score: 20,
  restoration_land_availability_score: 20,
};

export type RawWeightVector = WeightVector;

export function rawWeightsForPreset(weights: WeightVector): RawWeightVector {
  return Object.fromEntries(
    COMPONENT_FIELDS.map((field) => [field, Math.round(weights[field] * 100)]),
  ) as RawWeightVector;
}

export function normalizeWeights(rawWeights: RawWeightVector): WeightVector {
  const total = COMPONENT_FIELDS.reduce((sum, field) => sum + rawWeights[field], 0);
  if (total <= 0) {
    return Object.fromEntries(
      COMPONENT_FIELDS.map((field) => [field, 0.2]),
    ) as WeightVector;
  }
  return Object.fromEntries(
    COMPONENT_FIELDS.map((field) => [field, rawWeights[field] / total]),
  ) as WeightVector;
}

export function weightedScore(
  candidate: Pick<WeightIndexItem, ComponentScoreField>,
  rawWeights: RawWeightVector,
): number {
  const normalized = normalizeWeights(rawWeights);
  return COMPONENT_FIELDS.reduce(
    (score, field) => score + candidate[field] * normalized[field],
    0,
  );
}

export function activeCandidateScore(
  candidate: CandidateProperties,
  activePreset: ActivePresetId,
  rawWeights: RawWeightVector,
): number {
  if (activePreset !== "custom") return candidate[PRESET_SCORE_FIELDS[activePreset]];
  return weightedScore(candidate, rawWeights);
}

export interface RankedWeightIndexItem extends WeightIndexItem {
  rank: number;
  score: number;
}

export function rankWeightIndex(
  candidates: WeightIndexItem[],
  rawWeights: RawWeightVector,
  topN = 5,
): RankedWeightIndexItem[] {
  return candidates
    .map((candidate) => ({ ...candidate, score: weightedScore(candidate, rawWeights) }))
    .sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      return left.hex_id < right.hex_id ? -1 : left.hex_id > right.hex_id ? 1 : 0;
    })
    .slice(0, topN)
    .map((candidate, index) => ({ ...candidate, rank: index + 1 }));
}

export function isPresetId(value: ActivePresetId): value is PresetId {
  return value !== "custom";
}
