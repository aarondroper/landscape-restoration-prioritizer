import type {
  ActivePresetId,
  ComponentScoreField,
  DeliveryMetadata,
  PresetDefinition,
  PresetId,
  ShortlistItem,
  WeightIndexItem,
} from "../data/types";
import type { RawWeightVector } from "../lib/weighting";
import { Icon } from "./Icon";
import { MapDisplayControls } from "./MapDisplayControls";
import { PresetSelector } from "./PresetSelector";
import { TopCandidates } from "./TopCandidates";
import { WeightControls } from "./WeightControls";

interface SidebarProps {
  metadata: DeliveryMetadata;
  activePreset: ActivePresetId;
  presets: Record<PresetId, PresetDefinition>;
  presetDefinition: PresetDefinition;
  rawWeights: RawWeightVector;
  priorityVisible: boolean;
  onPresetChange: (preset: ActivePresetId) => void;
  onWeightChange: (field: ComponentScoreField, value: number) => void;
  onPriorityChange: (visible: boolean) => void;
  shortlist?: { candidate: ShortlistItem | WeightIndexItem; rank: number; score: number }[];
  shortlistStatus: "loading" | "ready" | "error" | "updating";
  selectedCandidateId?: string;
  onCandidateSelect: (candidate: ShortlistItem | WeightIndexItem) => void;
}

export function Sidebar({
  metadata,
  activePreset,
  presets,
  presetDefinition,
  rawWeights,
  priorityVisible,
  onPresetChange,
  onWeightChange,
  onPriorityChange,
  shortlist,
  shortlistStatus,
  selectedCandidateId,
  onCandidateSelect,
}: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Prioritizer controls">
      <div className="project-identity">
        <div className="brand-mark" aria-hidden="true">
          <Icon name="app-logo.svg" />
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
        <WeightControls rawWeights={rawWeights} onChange={onWeightChange} />
        <MapDisplayControls
          priorityVisible={priorityVisible}
          onPriorityChange={onPriorityChange}
        />
        <TopCandidates
          activePreset={activePreset}
          presetDefinition={presetDefinition}
          metadata={metadata}
          candidates={shortlist}
          status={shortlistStatus}
          selectedCandidateId={selectedCandidateId}
          onSelect={onCandidateSelect}
        />
      </div>
    </aside>
  );
}
