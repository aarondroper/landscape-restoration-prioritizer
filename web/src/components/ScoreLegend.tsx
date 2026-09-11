import { SCORE_RAMP_CSS } from "../lib/scoreScale";

export function ScoreLegend({ priorityVisible = true }: { priorityVisible?: boolean }) {
  return (
    <div className="layer-legend" data-visible={priorityVisible} aria-label="Relative model score color scale">
      <span className="quiet-label">Relative model score</span>
      <div className="score-ramp" style={{ background: SCORE_RAMP_CSS }} aria-hidden="true" />
      <div className="legend-scale" aria-label="Score scale from 0 to 100">
        {[0, 25, 50, 75, 100].map((score) => (
          <span key={score}>{score}</span>
        ))}
      </div>
      <div className="legend-ends" aria-hidden="true">
        <span>Lower</span>
        <span>Higher</span>
      </div>
    </div>
  );
}
