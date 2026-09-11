import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CandidateDetails } from "./components/CandidateDetails";
import { Sidebar } from "./components/Sidebar";
import { loadDeliveryMetadata } from "./data/metadata";
import { loadPresets } from "./data/presets";
import { loadCandidateShortlists } from "./data/shortlists";
import { loadCandidateWeightIndex } from "./data/weightIndex";
import type {
  ActivePresetId,
  CandidateProperties,
  ComponentScoreField,
  DeliveryMetadata,
  PresetDefinition,
  PresetId,
  ShortlistItem,
  WeightIndexItem,
} from "./data/types";
import { PRESET_SCORE_FIELDS } from "./data/types";
import { DEFAULT_RAW_WEIGHTS, normalizeWeights, rankWeightIndex, rawWeightsForPreset, type RawWeightVector } from "./lib/weighting";
import { CandidateMap } from "./map/CandidateMap";
import type { ContextLayerId, ContextLayerStatuses, ContextLayerStatus } from "./map/contextualLayers";
import "./styles.css";

type FocusRequest = Pick<ShortlistItem, "hex_id" | "longitude" | "latitude"> & { selectOnFocus?: boolean };

export default function App() {
  const [metadata, setMetadata] = useState<DeliveryMetadata>();
  const [presets, setPresets] = useState<Record<PresetId, PresetDefinition>>();
  const [activePreset, setActivePreset] = useState<ActivePresetId>("balanced");
  const [rawWeights, setRawWeights] = useState<RawWeightVector>(DEFAULT_RAW_WEIGHTS);
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateProperties>();
  const [shortlists, setShortlists] = useState<Record<PresetId, ShortlistItem[]>>();
  const [shortlistStatus, setShortlistStatus] = useState<"loading" | "ready" | "error">("loading");
  const [focusRequest, setFocusRequest] = useState<FocusRequest>();
  const [priorityVisible, setPriorityVisible] = useState(true);
  const [contextLayerVisibility, setContextLayerVisibility] = useState<Record<ContextLayerId, boolean>>({
    protectedAreas: false,
    wetlandInlandWater: false,
  });
  const [contextLayerStatuses, setContextLayerStatuses] = useState<ContextLayerStatuses>({
    protectedAreas: "idle",
    wetlandInlandWater: "idle",
  });
  const [contextLayerRetry, setContextLayerRetry] = useState<Record<ContextLayerId, number>>({
    protectedAreas: 0,
    wetlandInlandWater: 0,
  });
  const [weightIndex, setWeightIndex] = useState<WeightIndexItem[]>();
  const [weightIndexStatus, setWeightIndexStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [rankedWeightsKey, setRankedWeightsKey] = useState<string>();
  const weightIndexRequest = useRef<Promise<WeightIndexItem[]> | undefined>(undefined);
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

  const ensureWeightIndex = useCallback(() => {
    if (weightIndex) return Promise.resolve(weightIndex);
    if (weightIndexRequest.current) return weightIndexRequest.current;
    setWeightIndexStatus("loading");
    const request = loadCandidateWeightIndex()
      .then((loaded) => {
        setWeightIndex(loaded);
        setWeightIndexStatus("ready");
        return loaded;
      })
      .catch((reason: unknown) => {
        setWeightIndexStatus("error");
        throw reason;
      });
    weightIndexRequest.current = request;
    return request;
  }, [weightIndex]);

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

  const handleContextLayerChange = useCallback((layer: ContextLayerId, visible: boolean) => {
    setContextLayerVisibility((current) => ({ ...current, [layer]: visible }));
  }, []);

  const handleContextLayerStatusChange = useCallback((layer: ContextLayerId, status: ContextLayerStatus) => {
    setContextLayerStatuses((current) => ({ ...current, [layer]: status }));
  }, []);

  const handleContextLayerRetry = useCallback((layer: ContextLayerId) => {
    setContextLayerRetry((current) => ({ ...current, [layer]: current[layer] + 1 }));
  }, []);

  const handleShortlistSelect = useCallback((candidate: ShortlistItem | WeightIndexItem) => {
    const isFullCandidate = "balanced_score" in candidate;
    if (isFullCandidate) setSelectedCandidate(candidate);
    setFocusRequest({
      hex_id: candidate.hex_id,
      longitude: candidate.longitude,
      latitude: candidate.latitude,
      selectOnFocus: !isFullCandidate,
    });
  }, []);

  const handlePresetChange = useCallback((preset: ActivePresetId) => {
    if (preset === "custom") {
      setActivePreset("custom");
      void ensureWeightIndex().catch(() => undefined);
      return;
    }
    setActivePreset(preset);
    setRawWeights(rawWeightsForPreset(presets?.[preset]?.weights ?? DEFAULT_RAW_WEIGHTS));
  }, [ensureWeightIndex, presets]);

  const handleWeightChange = useCallback((field: ComponentScoreField, value: number) => {
    setActivePreset("custom");
    setRawWeights((current) => {
      const next = { ...current, [field]: value };
      if (Object.values(next).every((weight) => weight === 0)) next[field] = 1;
      return next;
    });
    void ensureWeightIndex().catch(() => undefined);
  }, [ensureWeightIndex]);

  const activePresetDefinition = activePreset === "custom"
    ? {
        id: "balanced" as PresetId,
        name: "Custom",
        description: "Uses your adjusted relative weights across all five model dimensions.",
        weights: normalizeWeights(rawWeights),
      }
    : presets?.[activePreset];

  const [customTopCandidates, setCustomTopCandidates] = useState<
    { candidate: WeightIndexItem; rank: number; score: number }[]
  >();
  const rawWeightsKey = JSON.stringify(rawWeights);

  useEffect(() => {
    if (activePreset !== "custom" || !weightIndex) return;
    const timer = window.setTimeout(() => {
      setCustomTopCandidates(
        rankWeightIndex(weightIndex, rawWeights).map((candidate) => ({
          candidate,
          rank: candidate.rank,
          score: candidate.score,
        })),
      );
      setRankedWeightsKey(rawWeightsKey);
    }, 140);
    return () => window.clearTimeout(timer);
  }, [activePreset, rawWeights, rawWeightsKey, weightIndex]);

  const canonicalTopCandidates = useMemo(() => {
    if (activePreset === "custom" || !shortlists?.[activePreset]) return undefined;
    const scoreField = PRESET_SCORE_FIELDS[activePreset];
    return shortlists[activePreset].slice(0, 5).map((candidate) => ({
      candidate,
      rank: candidate.rank,
      score: candidate[scoreField],
    }));
  }, [activePreset, shortlists]);

  const displayedCandidates = activePreset === "custom" ? customTopCandidates : canonicalTopCandidates;
  const displayedShortlistStatus = activePreset === "custom"
    ? weightIndexStatus === "error" ? "error" : weightIndexStatus === "loading" || !customTopCandidates ? "loading" : rankedWeightsKey !== rawWeightsKey ? "updating" : "ready"
    : shortlistStatus;

  return (
    <main className="app-shell">
      {error ? (
        <div className="app-error" role="alert">
          Application data error: {error}
        </div>
      ) : metadata && presets && activePresetDefinition ? (
        <>
          <Sidebar
            metadata={metadata}
            activePreset={activePreset}
            presets={presets}
            rawWeights={rawWeights}
            priorityVisible={priorityVisible}
            contextLayerVisibility={contextLayerVisibility}
            contextLayerStatuses={contextLayerStatuses}
            onPresetChange={handlePresetChange}
            onWeightChange={handleWeightChange}
            onPriorityChange={setPriorityVisible}
            onContextLayerChange={handleContextLayerChange}
            onContextLayerRetry={handleContextLayerRetry}
            shortlist={displayedCandidates}
            shortlistStatus={displayedShortlistStatus}
            presetDefinition={activePresetDefinition}
            selectedCandidateId={selectedCandidate?.hex_id}
            onCandidateSelect={handleShortlistSelect}
          />
          <section className="map-stage" aria-label="Restoration opportunity map">
            <CandidateMap
              metadata={metadata}
              activePreset={activePreset}
              weights={normalizeWeights(rawWeights)}
              selectedCandidateId={selectedCandidate?.hex_id}
              priorityVisible={priorityVisible}
              protectedAreasVisible={contextLayerVisibility.protectedAreas}
              wetlandInlandWaterVisible={contextLayerVisibility.wetlandInlandWater}
              contextLayerRetry={contextLayerRetry}
              onContextLayerStatusChange={handleContextLayerStatusChange}
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
              activePreset={activePreset}
              preset={activePresetDefinition}
              weights={normalizeWeights(rawWeights)}
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
