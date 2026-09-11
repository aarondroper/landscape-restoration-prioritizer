export const PRESET_IDS = ["balanced", "connectivity_first", "riparian_restoration"] as const;
export type PresetId = (typeof PRESET_IDS)[number];
export const CUSTOM_PRESET_ID = "custom" as const;
export type ActivePresetId = PresetId | typeof CUSTOM_PRESET_ID;

export type PresetScoreField =
  | "balanced_score"
  | "connectivity_first_score"
  | "riparian_restoration_score";

export const PRESET_SCORE_FIELDS: Record<PresetId, PresetScoreField> = {
  balanced: "balanced_score",
  connectivity_first: "connectivity_first_score",
  riparian_restoration: "riparian_restoration_score",
};

export const COMPONENT_SCORE_FIELDS = {
  habitatContext: "habitat_context_score",
  ecologicalNetwork: "ecological_network_score",
  riparianOpportunity: "riparian_opportunity_score",
  protectedAreaReinforcement: "protected_area_reinforcement_score",
  restorationLandAvailability: "restoration_land_availability_score",
} as const;

export type ComponentScoreField =
  (typeof COMPONENT_SCORE_FIELDS)[keyof typeof COMPONENT_SCORE_FIELDS];

export type WeightVector = Record<ComponentScoreField, number>;

export interface PresetDefinition {
  id: PresetId;
  name: string;
  description: string;
  weights: WeightVector;
}

export interface DeliveryMetadata {
  bbox_epsg_4326: {
    min_longitude: number;
    min_latitude: number;
    max_longitude: number;
    max_latitude: number;
  };
  feature_count: number;
  file_size_bytes: number;
  sha256?: string;
  dataset_name?: string;
  delivery_architecture?: string;
  maplibre_suitability?: {
    classification: string;
    vector_tiling_justified_from_artifact_alone: boolean;
  };
}

export interface CandidateProperties {
  hex_id: string;
  balanced_score: number;
  connectivity_first_score: number;
  riparian_restoration_score: number;
  habitat_context_score: number;
  ecological_network_score: number;
  riparian_opportunity_score: number;
  protected_area_reinforcement_score: number;
  restoration_land_availability_score: number;
  habitat_context_local_fraction: number;
  opposing_balance_ratio: number;
  riparian_focal_fraction: number;
  nearest_protected_hex_steps: number;
  protected_focal_fraction: number;
  candidate_land_area_ha: number;
  artificial_focal_fraction: number;
  boundary_edge_flag: boolean;
}

export interface ShortlistItem extends CandidateProperties {
  rank: number;
  longitude: number;
  latitude: number;
}

export interface WeightIndexItem {
  hex_id: string;
  longitude: number;
  latitude: number;
  habitat_context_score: number;
  ecological_network_score: number;
  riparian_opportunity_score: number;
  protected_area_reinforcement_score: number;
  restoration_land_availability_score: number;
}

export interface CandidateShortlists {
  generated_at_utc: string;
  top_n: number;
  ranking_source: string;
  tie_break: string;
  presets: Record<PresetId, ShortlistItem[]>;
}
