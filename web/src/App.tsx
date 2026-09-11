import { useCallback, useEffect, useState } from "react";
import { CandidateDetails } from "./components/CandidateDetails";
import { Sidebar } from "./components/Sidebar";
import { loadDeliveryMetadata } from "./data/metadata";
import { loadPresets } from "./data/presets";
import { loadCandidateShortlists } from "./data/shortlists";
import type {
  CandidateProperties,
  DeliveryMetadata,
  PresetDefinition,
  PresetId,
  ShortlistItem,
} from "./data/types";
import { CandidateMap } from "./map/CandidateMap";
import "./styles.css";

type FocusRequest = Pick<ShortlistItem, "hex_id" | "longitude" | "latitude">;

export default function App() {
  const [metadata, setMetadata] = useState<DeliveryMetadata>();
  const [presets, setPresets] = useState<Record<PresetId, PresetDefinition>>();
  const [activePreset, setActivePreset] = useState<PresetId>("balanced");
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateProperties>();
  const [shortlists, setShortlists] = useState<Record<PresetId, ShortlistItem[]>>();
  const [shortlistStatus, setShortlistStatus] = useState<"loading" | "ready" | "error">("loading");
  const [focusRequest, setFocusRequest] = useState<FocusRequest>();
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

  useEffect(() => {
    loadCandidateShortlists()
      .then((loaded) => {
        setShortlists(loaded.presets);
        setShortlistStatus("ready");
      })
      .catch(() => setShortlistStatus("error"));
  }, []);

  const handleSelect = useCallback((candidate: CandidateProperties) => {
    setSelectedCandidate(candidate);
  }, []);

  const handleShortlistSelect = useCallback((candidate: ShortlistItem) => {
    setSelectedCandidate(candidate);
    setFocusRequest({
      hex_id: candidate.hex_id,
      longitude: candidate.longitude,
      latitude: candidate.latitude,
    });
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
            shortlist={shortlists?.[activePreset]}
            shortlistStatus={shortlistStatus}
            selectedCandidateId={selectedCandidate?.hex_id}
            onCandidateSelect={handleShortlistSelect}
          />
          <section className="map-stage" aria-label="Restoration opportunity map">
            <CandidateMap
              metadata={metadata}
              activePreset={activePreset}
              selectedCandidateId={selectedCandidate?.hex_id}
              priorityVisible={priorityVisible}
              boundariesVisible={boundariesVisible}
              onSelect={handleSelect}
              focusRequest={focusRequest}
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
