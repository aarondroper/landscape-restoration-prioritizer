import type { ActivePresetId, DeliveryMetadata, PresetDefinition, ShortlistItem, WeightIndexItem } from "../data/types";
import { formatAnalysisUnitId, formatScore } from "../lib/formatting";
import { Icon } from "./Icon";

export interface TopCandidate {
  candidate: ShortlistItem | WeightIndexItem;
  rank: number;
  score: number;
}

interface TopCandidatesProps {
  activePreset: ActivePresetId;
  presetDefinition: PresetDefinition;
  metadata: DeliveryMetadata;
  candidates?: TopCandidate[];
  status: "loading" | "ready" | "error" | "updating";
  selectedCandidateId?: string;
  onSelect: (candidate: ShortlistItem | WeightIndexItem) => void;
}

export function TopCandidates({
  activePreset,
  presetDefinition,
  metadata,
  candidates,
  status,
  selectedCandidateId,
  onSelect,
}: TopCandidatesProps) {
  return (
    <section className="top-candidates sidebar-section" aria-labelledby="top-candidates-heading">
      <div className="section-heading-row">
        <h2 className="section-label" id="top-candidates-heading">
          <Icon className="section-icon" name="top-candidates.svg" aria-hidden="true" />
          Top candidates
        </h2>
        <span className="quiet-label">{activePreset === "custom" ? "Custom" : presetDefinition.name}</span>
      </div>
      <p className="candidate-population">
        {metadata.feature_count.toLocaleString("en-US")} candidate analysis units <span aria-hidden="true">·</span> 500 m hexagons
      </p>
      <p className="top-candidates-context">
        Screening shortlist · {activePreset === "custom" ? "ranked by active model weights" : "relative model score under active preset"}
      </p>
      <p className="top-candidates-caveat">Screening-scale decision support — not parcel-level restoration recommendations.</p>
      {status === "loading" ? <p className="shortlist-status">Loading shortlist…</p> : null}
      {status === "error" ? <p className="shortlist-status">{activePreset === "custom" ? "Custom shortlist unavailable" : "Shortlist unavailable"}</p> : null}
      {status === "updating" ? <p className="shortlist-status">Updating custom shortlist…</p> : null}
      {status === "ready" && candidates ? (
        <ol className="candidate-shortlist">
          {candidates.slice(0, 5).map((entry) => (
            <li key={entry.candidate.hex_id}>
              <button
                className="candidate-shortlist-row"
                type="button"
                aria-current={entry.candidate.hex_id === selectedCandidateId ? "true" : undefined}
                data-selected={entry.candidate.hex_id === selectedCandidateId}
                onClick={() => onSelect(entry.candidate)}
              >
                <span className="candidate-rank">#{entry.rank}</span>
                <span className="candidate-shortlist-copy">
                  <span className="candidate-shortlist-score">
                    {formatScore(entry.score)}
                  </span>
                  <span className="candidate-shortlist-id">Analysis unit {formatAnalysisUnitId(entry.candidate.hex_id)}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
