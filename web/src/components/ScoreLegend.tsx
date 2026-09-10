import { SCORE_RAMP_CSS } from "../map/candidateLayers";

export function ScoreLegend() {
  return (
    <section className="sidebar-section legend-section" aria-labelledby="legend-title">
      <div className="section-heading-row">
        <h2 id="legend-title" className="section-label">
          Restoration priority
        </h2>
        <span className="quiet-label">Relative model score</span>
      </div>
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
    </section>
  );
}
