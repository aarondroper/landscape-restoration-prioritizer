import type { CandidateProperties } from "../data/types";

export const EXPLANATION_BANDS = {
  strong: 75,
  moderate: 50,
} as const;

type ExplanationDimension = {
  field: keyof CandidateProperties;
  label: string;
  strong: string;
  moderate: string;
  weak: string;
};

const DIMENSIONS: ExplanationDimension[] = [
  {
    field: "habitat_context_score",
    label: "Habitat context",
    strong: "Strong surrounding habitat context.",
    moderate: "Moderate surrounding habitat context.",
    weak: "Weaker surrounding habitat context.",
  },
  {
    field: "ecological_network_score",
    label: "Ecological network context",
    strong: "Strong opposing-side habitat configuration, suggesting potential to reinforce ecological structure.",
    moderate: "Moderate opposing-side habitat configuration.",
    weak: "Weaker opposing-side habitat configuration.",
  },
  {
    field: "riparian_opportunity_score",
    label: "Riparian opportunity",
    strong: "Strong mapped focal wetland and inland-water context.",
    moderate: "Moderate mapped focal wetland and inland-water context.",
    weak: "Little or no mapped focal wetland and inland-water context.",
  },
  {
    field: "protected_area_reinforcement_score",
    label: "Protected-area reinforcement",
    strong: "Close to the existing terrestrial protected-area network.",
    moderate: "Moderate proximity to the existing terrestrial protected-area network.",
    weak: "More limited proximity to the existing terrestrial protected-area network.",
  },
  {
    field: "restoration_land_availability_score",
    label: "Restoration land availability",
    strong: "Relatively large amount of mapped candidate arable land.",
    moderate: "Moderate amount of mapped candidate arable land.",
    weak: "Available candidate land is relatively limited within this analysis unit.",
  },
];

function band(score: number): keyof Pick<ExplanationDimension, "strong" | "moderate" | "weak"> {
  if (score >= EXPLANATION_BANDS.strong) return "strong";
  if (score >= EXPLANATION_BANDS.moderate) return "moderate";
  return "weak";
}

export function explainCandidate(candidate: CandidateProperties): string[] {
  const strongest = [...DIMENSIONS]
    .sort((left, right) => Number(candidate[right.field]) - Number(candidate[left.field]))
    .slice(0, 2);
  const explanations = strongest.map((dimension) => dimension[band(Number(candidate[dimension.field]))]);
  if (candidate.restoration_land_availability_score < EXPLANATION_BANDS.moderate) {
    explanations.push("Available candidate land is relatively limited within this analysis unit.");
  } else if (candidate.riparian_opportunity_score < EXPLANATION_BANDS.moderate) {
    explanations.push("Little or no mapped focal wetland and inland-water context.");
  }
  return [...new Set(explanations)].slice(0, 3);
}
