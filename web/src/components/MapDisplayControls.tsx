import { Layers3 } from "lucide-react";

interface MapDisplayControlsProps {
  priorityVisible: boolean;
  boundariesVisible: boolean;
  onPriorityChange: (visible: boolean) => void;
  onBoundariesChange: (visible: boolean) => void;
}

export function MapDisplayControls({
  priorityVisible,
  boundariesVisible,
  onPriorityChange,
  onBoundariesChange,
}: MapDisplayControlsProps) {
  return (
    <section className="sidebar-section map-display" aria-labelledby="map-display-title">
      <div className="section-heading-row">
        <h2 id="map-display-title" className="section-label">
          Map display
        </h2>
        <Layers3 size={15} strokeWidth={1.7} aria-hidden="true" />
      </div>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={priorityVisible}
          onChange={(event) => onPriorityChange(event.target.checked)}
        />
        <span>Candidate priority</span>
      </label>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={boundariesVisible}
          onChange={(event) => onBoundariesChange(event.target.checked)}
        />
        <span>Candidate boundaries</span>
      </label>
    </section>
  );
}
