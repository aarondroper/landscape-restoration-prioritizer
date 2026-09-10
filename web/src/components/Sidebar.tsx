import { Leaf } from "lucide-react";
import type { DeliveryMetadata, PresetDefinition, PresetId } from "../data/types";
import { MapDisplayControls } from "./MapDisplayControls";
import { PresetSelector } from "./PresetSelector";
import { ScoreLegend } from "./ScoreLegend";

interface SidebarProps {
  metadata: DeliveryMetadata;
  activePreset: PresetId;
  presets: Record<PresetId, PresetDefinition>;
  priorityVisible: boolean;
  boundariesVisible: boolean;
  onPresetChange: (preset: PresetId) => void;
  onPriorityChange: (visible: boolean) => void;
  onBoundariesChange: (visible: boolean) => void;
}

export function Sidebar({
  metadata,
  activePreset,
  presets,
  priorityVisible,
  boundariesVisible,
  onPresetChange,
  onPriorityChange,
  onBoundariesChange,
}: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Prioritizer controls">
      <div className="project-identity">
        <div className="brand-mark" aria-hidden="true">
          <Leaf size={18} strokeWidth={1.8} />
        </div>
        <div>
          <h1>Landscape Restoration Prioritizer</h1>
          <p className="study-area">Skåne, Sweden</p>
        </div>
      </div>
      <p className="identity-copy">
        Screen agricultural landscapes for restoration opportunities using habitat,
        connectivity, riparian, protection, and land-availability context.
      </p>

      <div className="sidebar-content">
        <PresetSelector activePreset={activePreset} presets={presets} onChange={onPresetChange} />
        <ScoreLegend />
        <MapDisplayControls
          priorityVisible={priorityVisible}
          boundariesVisible={boundariesVisible}
          onPriorityChange={onPriorityChange}
          onBoundariesChange={onBoundariesChange}
        />
      </div>

      <div className="model-note">
        <div className="model-note-value">{metadata.feature_count.toLocaleString("en-US")}</div>
        <div className="model-note-label">candidate analysis units</div>
        <p>500 m hexagons</p>
        <small>Screening-scale decision support — not parcel-level restoration recommendations.</small>
      </div>
    </aside>
  );
}
