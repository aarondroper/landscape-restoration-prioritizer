import type { ComponentScoreField, WeightIndexItem } from "./types";

export const WEIGHT_INDEX_URL = "/data/candidate_weight_index.json";

const REQUIRED_FIELDS = [
  "hex_id",
  "longitude",
  "latitude",
  "habitat_context_score",
  "ecological_network_score",
  "riparian_opportunity_score",
  "protected_area_reinforcement_score",
  "restoration_land_availability_score",
] as const;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validateItem(value: unknown, index: number): WeightIndexItem {
  if (!value || typeof value !== "object") throw new Error(`Weight index entry ${index + 1} is invalid`);
  const item = value as Record<string, unknown>;
  if (Object.keys(item).sort().join("|") !== [...REQUIRED_FIELDS].sort().join("|")) {
    throw new Error(`Weight index entry ${index + 1} has an invalid schema`);
  }
  if (typeof item.hex_id !== "string" || !isFiniteNumber(item.longitude) || !isFiniteNumber(item.latitude)) {
    throw new Error(`Weight index entry ${index + 1} is missing ID or coordinates`);
  }
  for (const field of REQUIRED_FIELDS.slice(3) as readonly ComponentScoreField[]) {
    if (!isFiniteNumber(item[field])) throw new Error(`Weight index entry ${index + 1} has invalid ${field}`);
  }
  return item as unknown as WeightIndexItem;
}

export async function loadCandidateWeightIndex(): Promise<WeightIndexItem[]> {
  const response = await fetch(WEIGHT_INDEX_URL);
  if (!response.ok) throw new Error(`Candidate weight index request failed (${response.status})`);
  const raw: unknown = await response.json();
  if (!Array.isArray(raw)) throw new Error("Candidate weight index must be an array");
  return raw.map(validateItem);
}
