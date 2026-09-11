import { Layers3 } from "lucide-react";
import { ScoreLegend } from "./ScoreLegend";

interface MapDisplayControlsProps {
  priorityVisible: boolean;
  onPriorityChange: (visible: boolean) => void;
}

export function MapDisplayControls({
  priorityVisible,
  onPriorityChange,
}: MapDisplayControlsProps) {
  return (
    <section className="sidebar-section map-layers" aria-labelledby="map-layers-title">
      <div className="section-heading-row">
        <h2 id="map-layers-title" className="section-label">
          Map layers
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
      <ScoreLegend priorityVisible={priorityVisible} />
    </section>
  );
}
