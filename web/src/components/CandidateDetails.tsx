import * as Tabs from "@radix-ui/react-tabs";
import { X } from "lucide-react";
import { PRESET_SCORE_FIELDS } from "../data/types";
import type { CandidateProperties, PresetDefinition } from "../data/types";
import { explainCandidate } from "../lib/candidateExplanation";
import {
  formatHectares,
  formatPercent,
  formatProtectedDistance,
  formatRatio,
  formatScore,
} from "../lib/formatting";
import { ComponentScores } from "./ComponentScores";

interface CandidateDetailsProps {
  candidate: CandidateProperties;
  preset: PresetDefinition;
  onClose: () => void;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function CandidateDetails({ candidate, preset, onClose }: CandidateDetailsProps) {
  const explanations = explainCandidate(candidate);
  return (
    <aside className="details-panel" aria-label="Selected candidate details">
      <div className="details-header">
        <div>
          <span className="eyebrow">Selected area</span>
          <p className="detail-title">Analysis unit</p>
          <h2>{candidate.hex_id}</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close selected area">
          <X size={18} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
      <div className="selected-score">
        <span>Relative model score</span>
        <strong>{formatScore(candidate[PRESET_SCORE_FIELDS[preset.id]])}</strong>
        <small>{preset.name} · screening-scale decision support</small>
      </div>

      <Tabs.Root className="detail-tabs" defaultValue="overview">
        <Tabs.List className="tab-list" aria-label="Selected area information">
          <Tabs.Trigger className="tab-trigger" value="overview">Overview</Tabs.Trigger>
          <Tabs.Trigger className="tab-trigger" value="components">Components</Tabs.Trigger>
          <Tabs.Trigger className="tab-trigger" value="details">Details</Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content className="tab-content" value="overview">
          <section className="detail-section">
            <h3>Why this area?</h3>
            <div className="explanation-list">
              {explanations.map((explanation) => <p key={explanation}>{explanation}</p>)}
            </div>
          </section>
          <section className="detail-section">
            <h3>Key facts</h3>
            <dl className="facts-list">
              <Fact label="Candidate land" value={formatHectares(candidate.candidate_land_area_ha)} />
              <Fact label="Focal riparian context" value={formatPercent(candidate.riparian_focal_fraction)} />
              <Fact label="Protected-network distance" value={formatProtectedDistance(candidate.nearest_protected_hex_steps)} />
              <Fact label="Direct protected overlap" value={formatPercent(candidate.protected_focal_fraction)} />
            </dl>
          </section>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="components">
          <section className="detail-section">
            <h3>Component scores</h3>
            <ComponentScores candidate={candidate} preset={preset} />
          </section>
        </Tabs.Content>
        <Tabs.Content className="tab-content" value="details">
          <section className="detail-section">
            <h3>Model details</h3>
            <dl className="facts-list details-list">
              <Fact label="Analysis unit" value={candidate.hex_id} />
              <Fact label="Candidate land" value={formatHectares(candidate.candidate_land_area_ha)} />
              <Fact label="Habitat context" value={formatPercent(candidate.habitat_context_local_fraction)} />
              <Fact label="Network balance" value={formatRatio(candidate.opposing_balance_ratio)} />
              <Fact label="Riparian context" value={formatPercent(candidate.riparian_focal_fraction)} />
              <Fact label="Protected grid distance" value={`${candidate.nearest_protected_hex_steps} steps`} />
              <Fact label="Protected focal overlap" value={formatPercent(candidate.protected_focal_fraction)} />
              <Fact label="Mapped artificial context" value={formatPercent(candidate.artificial_focal_fraction)} />
              <Fact label="Boundary-edge" value={candidate.boundary_edge_flag ? "Yes" : "No"} />
            </dl>
            <p className="detail-caveat">Scores are screening-scale, population-relative decision-support measures derived from mapped environmental context. Analysis units are not parcels.</p>
          </section>
        </Tabs.Content>
      </Tabs.Root>
    </aside>
  );
}
