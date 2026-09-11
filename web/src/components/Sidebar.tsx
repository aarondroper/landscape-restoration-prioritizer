import { Leaf } from "lucide-react";
import type {
  DeliveryMetadata,
  PresetDefinition,
  PresetId,
  ShortlistItem,
} from "../data/types";
import { MapDisplayControls } from "./MapDisplayControls";
import { PresetSelector } from "./PresetSelector";
import { ScoreLegend } from "./ScoreLegend";
import { TopCandidates } from "./TopCandidates";

interface SidebarProps {
  metadata: DeliveryMetadata;
  activePreset: PresetId;
  presets: Record<PresetId, PresetDefinition>;
  priorityVisible: boolean;
  boundariesVisible: boolean;
  onPresetChange: (preset: PresetId) => void;
  onPriorityChange: (visible: boolean) => void;
  onBoundariesChange: (visible: boolean) => void;
  shortlist?: ShortlistItem[];
  shortlistStatus: "loading" | "ready" | "error";
  selectedCandidateId?: string;
  onCandidateSelect: (candidate: ShortlistItem) => void;
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
  shortlist,
  shortlistStatus,
  selectedCandidateId,
  onCandidateSelect,
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
        <TopCandidates
          activePreset={presets[activePreset]}
          candidates={shortlist}
          status={shortlistStatus}
          selectedCandidateId={selectedCandidateId}
          onSelect={onCandidateSelect}
        />
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
