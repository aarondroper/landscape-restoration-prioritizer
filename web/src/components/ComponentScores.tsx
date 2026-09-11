import type { CandidateProperties, WeightVector } from "../data/types";
import { formatScore } from "../lib/formatting";
import { COMPONENTS } from "../lib/weighting";

interface ComponentScoresProps {
  candidate: CandidateProperties;
  weights: WeightVector;
}

export function ComponentScores({ candidate, weights }: ComponentScoresProps) {
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
                <span>{Math.round(weights[component.field] * 100)}%</span>
              </div>
            </div>
            <div className="component-bar" aria-label={`${component.label}: ${formatScore(score)} out of 100`}>
              <span style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
            </div>
          </div>
        );
      })}
      <p className="component-note">Relative model scores use a common 0–100 scale. Percentages show active weights.</p>
    </div>
  );
}
