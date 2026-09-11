import type { ComponentScoreField, WeightVector } from "../data/types";
import { COMPONENTS, normalizeWeights, type RawWeightVector } from "../lib/weighting";
import { Icon } from "./Icon";

interface WeightControlsProps {
  rawWeights: RawWeightVector;
  onChange: (field: ComponentScoreField, value: number) => void;
}

export function WeightControls({ rawWeights, onChange }: WeightControlsProps) {
  const effectiveWeights: WeightVector = normalizeWeights(rawWeights);
  return (
    <section className="weight-controls sidebar-section" aria-labelledby="weight-controls-title">
      <div className="section-heading-row">
        <h2 id="weight-controls-title" className="section-label">Adjust component weights</h2>
      </div>
      <p className="weight-note">Weights are normalized automatically.</p>
      <div className="weight-list">
        {COMPONENTS.map((component) => (
          <label className="weight-row" key={component.field}>
            <Icon className="component-icon" name={component.icon} aria-hidden="true" />
            <span className="weight-label">{component.label}</span>
            <output className="weight-value" htmlFor={`weight-${component.field}`}>
              {Math.round(effectiveWeights[component.field] * 100)}%
            </output>
            <input
              id={`weight-${component.field}`}
              className="weight-slider"
              type="range"
              min="0"
              max="100"
              step="1"
              value={rawWeights[component.field]}
              onChange={(event) => onChange(component.field, Number(event.target.value))}
              aria-label={`${component.label} relative weight`}
            />
          </label>
        ))}
      </div>
    </section>
  );
}
