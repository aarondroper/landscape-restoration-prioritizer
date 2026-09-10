import type { MapGeoJSONFeature } from "maplibre-gl";
import type { CandidateProperties } from "./types";

function finiteNumber(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : undefined;
}

export function parseCandidateProperties(feature: MapGeoJSONFeature): CandidateProperties | undefined {
  const raw = feature.properties as Record<string, unknown> | undefined;
  if (!raw) return undefined;
  const hexId = String(raw.hex_id ?? feature.id ?? "");
  if (!hexId) return undefined;

  const numericFields = [
    "balanced_score",
    "connectivity_first_score",
    "riparian_restoration_score",
    "habitat_context_score",
    "ecological_network_score",
    "riparian_opportunity_score",
    "protected_area_reinforcement_score",
    "restoration_land_availability_score",
    "habitat_context_local_fraction",
    "opposing_balance_ratio",
    "riparian_focal_fraction",
    "nearest_protected_hex_steps",
    "protected_focal_fraction",
    "candidate_land_area_ha",
    "artificial_focal_fraction",
  ] as const;
  const values = Object.fromEntries(
    numericFields.map((field) => [field, finiteNumber(raw[field])]),
  ) as Record<(typeof numericFields)[number], number | undefined>;
  if (numericFields.some((field) => values[field] === undefined)) return undefined;

  return {
    hex_id: hexId,
    ...(values as Record<(typeof numericFields)[number], number>),
    boundary_edge_flag:
      raw.boundary_edge_flag === true || raw.boundary_edge_flag === "true" || raw.boundary_edge_flag === 1,
  };
}
