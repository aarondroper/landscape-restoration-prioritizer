import { PRESET_IDS } from "./types";
import type { CandidateShortlists, PresetId, ShortlistItem } from "./types";

export const SHORTLISTS_URL = "/data/candidate_shortlists.json";

const SCORE_FIELDS = [
  "balanced_score",
  "connectivity_first_score",
  "riparian_restoration_score",
  "habitat_context_score",
  "ecological_network_score",
  "riparian_opportunity_score",
  "protected_area_reinforcement_score",
  "restoration_land_availability_score",
] as const;

const FRACTION_FIELDS = [
  "habitat_context_local_fraction",
  "opposing_balance_ratio",
  "riparian_focal_fraction",
  "protected_focal_fraction",
  "artificial_focal_fraction",
] as const;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validateItem(value: unknown, preset: PresetId, index: number): ShortlistItem {
  if (!value || typeof value !== "object") throw new Error(`Shortlist ${preset} entry ${index + 1} is invalid`);
  const item = value as Record<string, unknown>;
  if (
    typeof item.hex_id !== "string" ||
    typeof item.rank !== "number" ||
    !Number.isInteger(item.rank) ||
    !isFiniteNumber(item.longitude) ||
    !isFiniteNumber(item.latitude)
  ) {
    throw new Error(`Shortlist ${preset} entry ${index + 1} is missing rank, ID, or coordinates`);
  }
  if (item.rank < 1 || item.rank > 50) {
    throw new Error(`Shortlist ${preset} entry ${index + 1} has an invalid rank`);
  }
  for (const field of [...SCORE_FIELDS, ...FRACTION_FIELDS, "candidate_land_area_ha", "nearest_protected_hex_steps"]) {
    if (!isFiniteNumber(item[field])) throw new Error(`Shortlist ${preset} entry ${index + 1} has invalid ${field}`);
  }
  if (!Number.isInteger(item.nearest_protected_hex_steps)) {
    throw new Error(`Shortlist ${preset} entry ${index + 1} has invalid protected steps`);
  }
  if (typeof item.boundary_edge_flag !== "boolean") {
    throw new Error(`Shortlist ${preset} entry ${index + 1} has invalid boundary flag`);
  }
  return item as unknown as ShortlistItem;
}

export async function loadCandidateShortlists(): Promise<CandidateShortlists> {
  const response = await fetch(SHORTLISTS_URL);
  if (!response.ok) throw new Error(`Candidate shortlist request failed (${response.status})`);
  const raw = (await response.json()) as Partial<CandidateShortlists>;
  if (
    typeof raw.generated_at_utc !== "string" ||
    typeof raw.top_n !== "number" ||
    !Number.isInteger(raw.top_n) ||
    typeof raw.ranking_source !== "string" ||
    !raw.presets
  ) {
    throw new Error("Candidate shortlist metadata is incomplete");
  }
  const presets = {} as Record<PresetId, ShortlistItem[]>;
  for (const preset of PRESET_IDS) {
    const entries = raw.presets[preset];
    if (!Array.isArray(entries) || entries.length !== raw.top_n) {
      throw new Error(`Candidate shortlist metadata has an invalid ${preset} list`);
    }
    presets[preset] = entries.map((entry, index) => validateItem(entry, preset, index));
  }
  return {
    generated_at_utc: raw.generated_at_utc,
    top_n: raw.top_n,
    ranking_source: raw.ranking_source,
    tie_break: raw.tie_break ?? "preset score descending, then hex_id ascending",
    presets,
  };
}
