import type { PresetDefinition, ShortlistItem } from "../data/types";
import { formatScore } from "../lib/formatting";

interface TopCandidatesProps {
  activePreset: PresetDefinition;
  candidates?: ShortlistItem[];
  status: "loading" | "ready" | "error";
  selectedCandidateId?: string;
  onSelect: (candidate: ShortlistItem) => void;
}

export function TopCandidates({
  activePreset,
  candidates,
  status,
  selectedCandidateId,
  onSelect,
}: TopCandidatesProps) {
  return (
    <section className="top-candidates sidebar-section" aria-labelledby="top-candidates-heading">
      <div className="section-heading-row">
        <h2 className="section-label" id="top-candidates-heading">Top candidates</h2>
        <span className="quiet-label">{activePreset.name}</span>
      </div>
      <p className="top-candidates-context">Screening shortlist · relative model score under active preset</p>
      {status === "loading" ? <p className="shortlist-status">Loading shortlist…</p> : null}
      {status === "error" ? <p className="shortlist-status">Shortlist unavailable</p> : null}
      {status === "ready" && candidates ? (
        <ol className="candidate-shortlist">
          {candidates.slice(0, 5).map((candidate) => (
            <li key={candidate.hex_id}>
              <button
                className="candidate-shortlist-row"
                type="button"
                aria-current={candidate.hex_id === selectedCandidateId ? "true" : undefined}
                data-selected={candidate.hex_id === selectedCandidateId}
                onClick={() => onSelect(candidate)}
              >
                <span className="candidate-rank">#{candidate.rank}</span>
                <span className="candidate-shortlist-copy">
                  <span className="candidate-shortlist-score">
                    {formatScore(candidate[`${activePreset.id}_score`])}
                  </span>
                  <span className="candidate-shortlist-id">Analysis unit {candidate.hex_id}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
