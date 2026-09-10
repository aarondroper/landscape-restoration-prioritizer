import { PRESET_IDS } from "./types";
import type { ComponentScoreField, PresetDefinition, PresetId } from "./types";

export const PRESETS_URL = "/data/presets.json";

const PRESET_DESCRIPTIONS: Record<PresetId, string> = {
  balanced: "Equal emphasis across all five model dimensions.",
  connectivity_first:
    "Emphasizes ecological-network configuration and proximity to existing protected areas.",
  riparian_restoration:
    "Emphasizes focal wetland and inland-water restoration context.",
};

const COMPONENT_FIELDS: ComponentScoreField[] = [
  "habitat_context_score",
  "ecological_network_score",
  "riparian_opportunity_score",
  "protected_area_reinforcement_score",
  "restoration_land_availability_score",
];

export async function loadPresets(): Promise<Record<PresetId, PresetDefinition>> {
  const response = await fetch(PRESETS_URL);
  if (!response.ok) throw new Error(`Preset metadata request failed (${response.status})`);
  const raw = (await response.json()) as Record<string, { name?: unknown; weights?: unknown }>;

  const presets = {} as Record<PresetId, PresetDefinition>;
  for (const id of PRESET_IDS) {
    const entry = raw[id];
    if (!entry || typeof entry.name !== "string" || !entry.weights || typeof entry.weights !== "object") {
      throw new Error(`Preset metadata is missing the ${id} definition`);
    }
    const weights = {} as Record<ComponentScoreField, number>;
    for (const field of COMPONENT_FIELDS) {
      const value = (entry.weights as Record<string, unknown>)[field];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new Error(`Preset metadata has an invalid ${field} weight for ${id}`);
      }
      weights[field] = value;
    }
    presets[id] = { id, name: entry.name, description: PRESET_DESCRIPTIONS[id], weights };
  }
  return presets;
}
