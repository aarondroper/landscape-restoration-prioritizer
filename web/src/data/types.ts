export const PRESET_SCORE_FIELDS = {
  balanced: "balanced_score",
  connectivityFirst: "connectivity_first_score",
  riparianRestoration: "riparian_restoration_score",
} as const;

export const COMPONENT_SCORE_FIELDS = {
  habitatContext: "habitat_context_score",
  ecologicalNetwork: "ecological_network_score",
  riparianOpportunity: "riparian_opportunity_score",
  protectedAreaReinforcement: "protected_area_reinforcement_score",
  restorationLandAvailability: "restoration_land_availability_score",
} as const;

export type PresetScoreField = (typeof PRESET_SCORE_FIELDS)[keyof typeof PRESET_SCORE_FIELDS];
export type ComponentScoreField =
  (typeof COMPONENT_SCORE_FIELDS)[keyof typeof COMPONENT_SCORE_FIELDS];

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
}
