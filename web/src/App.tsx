import { useCallback, useEffect, useState } from "react";
import { CandidateDetails } from "./components/CandidateDetails";
import { Sidebar } from "./components/Sidebar";
import { loadDeliveryMetadata } from "./data/metadata";
import { loadPresets } from "./data/presets";
import type {
  CandidateProperties,
  DeliveryMetadata,
  PresetDefinition,
  PresetId,
} from "./data/types";
import { CandidateMap } from "./map/CandidateMap";
import "./styles.css";

export default function App() {
  const [metadata, setMetadata] = useState<DeliveryMetadata>();
  const [presets, setPresets] = useState<Record<PresetId, PresetDefinition>>();
  const [activePreset, setActivePreset] = useState<PresetId>("balanced");
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateProperties>();
  const [priorityVisible, setPriorityVisible] = useState(true);
  const [boundariesVisible, setBoundariesVisible] = useState(true);
  const [error, setError] = useState<string>();

  useEffect(() => {
    Promise.all([loadDeliveryMetadata(), loadPresets()])
      .then(([loadedMetadata, loadedPresets]) => {
        setMetadata(loadedMetadata);
        setPresets(loadedPresets);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Application data could not be loaded");
      });
  }, []);

  const handleSelect = useCallback((candidate: CandidateProperties) => {
    setSelectedCandidate(candidate);
  }, []);

  const activePresetMetadata = presets?.[activePreset];
  return (
    <main className="app-shell">
      {error ? (
        <div className="app-error" role="alert">
          Application data error: {error}
        </div>
      ) : metadata && presets && activePresetMetadata ? (
        <>
          <Sidebar
            metadata={metadata}
            activePreset={activePreset}
            presets={presets}
            priorityVisible={priorityVisible}
            boundariesVisible={boundariesVisible}
            onPresetChange={setActivePreset}
            onPriorityChange={setPriorityVisible}
            onBoundariesChange={setBoundariesVisible}
          />
          <section className="map-stage" aria-label="Restoration opportunity map">
            <CandidateMap
              metadata={metadata}
              activePreset={activePreset}
              selectedCandidateId={selectedCandidate?.hex_id}
              priorityVisible={priorityVisible}
              boundariesVisible={boundariesVisible}
              onSelect={handleSelect}
            />
            {!selectedCandidate ? (
              <div className="map-hint">Select a candidate hexagon to inspect its restoration profile.</div>
            ) : null}
          </section>
          {selectedCandidate ? (
            <CandidateDetails
              candidate={selectedCandidate}
              preset={activePresetMetadata}
              onClose={() => setSelectedCandidate(undefined)}
            />
          ) : null}
        </>
      ) : (
        <div className="app-loading" role="status">
          <span className="loading-dot" aria-hidden="true" />
          Loading restoration model…
        </div>
      )}
    </main>
  );
}
