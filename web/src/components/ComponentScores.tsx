import type { CandidateProperties, PresetDefinition } from "../data/types";
import { formatScore } from "../lib/formatting";

const COMPONENTS = [
  {
    field: "habitat_context_score",
    label: "Habitat Context",
    description: "Surrounding structural habitat amount.",
  },
  {
    field: "ecological_network_score",
    label: "Ecological Network Context",
    description: "Opposing-side habitat configuration.",
  },
  {
    field: "riparian_opportunity_score",
    label: "Riparian Opportunity",
    description: "Focal mapped wetland and inland-water context.",
  },
  {
    field: "protected_area_reinforcement_score",
    label: "Protected-Area Reinforcement",
    description: "Proximity to terrestrial formal protection.",
  },
  {
    field: "restoration_land_availability_score",
    label: "Restoration Land Availability",
    description: "Amount of mapped eligible arable land.",
  },
] as const;

interface ComponentScoresProps {
  candidate: CandidateProperties;
  preset: PresetDefinition;
}

export function ComponentScores({ candidate, preset }: ComponentScoresProps) {
  return (
    <div className="component-list">
      {COMPONENTS.map((component) => {
        const score = candidate[component.field];
        return (
          <div className="component-score" key={component.field}>
            <div className="component-header">
              <div>
                <div className="component-label">{component.label}</div>
                <div className="component-description">{component.description}</div>
              </div>
              <div className="component-value">
                <strong>{formatScore(score)}</strong>
                <span>{Math.round(preset.weights[component.field] * 100)}%</span>
              </div>
            </div>
            <div className="component-bar" aria-label={`${component.label}: ${formatScore(score)} out of 100`}>
              <span style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
            </div>
          </div>
        );
      })}
      <p className="component-note">Scores use a common 0–100 scale. Percentages show active preset weights.</p>
    </div>
  );
}
