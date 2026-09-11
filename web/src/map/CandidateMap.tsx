import { useEffect, useRef, useState } from "react";
import type { GeoJSONSourceSpecification } from "@maplibre/maplibre-gl-style-spec";
import {
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
  type ErrorEvent,
  type MapGeoJSONFeature,
  type MapLayerMouseEvent,
  type MapSourceDataEvent,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { parseCandidateProperties } from "../data/candidateProperties";
import { PRESET_SCORE_FIELDS } from "../data/types";
import type { ActivePresetId, CandidateProperties, DeliveryMetadata, ShortlistItem, WeightVector } from "../data/types";
import {
  candidateBoundaryLayer,
  candidateFillLayer,
  candidateHoverOutlineLayer,
  candidateSelectedFillLayer,
  candidateSelectedHaloLayer,
  candidateSelectedOutlineLayer,
  getCandidateSelectedFilter,
  getCandidateFillPaint,
} from "./candidateLayers";
import {
  CONTEXT_LAYER_CONFIG,
  reorderContextLayers,
  type ContextLayerId,
  type ContextLayerStatus,
} from "./contextualLayers";
import {
  CANDIDATE_DATA_URL,
  CANDIDATE_FILL_LAYER_ID,
  CANDIDATE_SELECTED_FILL_LAYER_ID,
  CANDIDATE_SELECTED_HALO_LAYER_ID,
  CANDIDATE_SELECTED_OUTLINE_LAYER_ID,
  CANDIDATE_SOURCE_ID,
  BASEMAP_STYLE_URL,
} from "./mapConfig";
import {
  getCandidateResourceTiming,
  reportCandidateMapPerformance,
  toCandidateResourceTiming,
  type CandidateMapPerformance,
} from "./performance";

interface CandidateMapProps {
  metadata: DeliveryMetadata;
  activePreset: ActivePresetId;
  weights: WeightVector;
  selectedCandidateId?: string;
  priorityVisible: boolean;
  protectedAreasVisible: boolean;
  wetlandInlandWaterVisible: boolean;
  contextLayerRetry: Record<ContextLayerId, number>;
  onContextLayerStatusChange: (layer: ContextLayerId, status: ContextLayerStatus) => void;
  onSelect: (candidate: CandidateProperties) => void;
  focusRequest?: Pick<ShortlistItem, "hex_id" | "longitude" | "latitude"> & { selectOnFocus?: boolean };
}

function bboxFromMetadata(metadata: DeliveryMetadata): [[number, number], [number, number]] {
  const bbox = metadata.bbox_epsg_4326;
  return [
    [bbox.min_longitude, bbox.min_latitude],
    [bbox.max_longitude, bbox.max_latitude],
  ];
}

export function CandidateMap({
  metadata,
  activePreset,
  weights,
  selectedCandidateId,
  priorityVisible,
  protectedAreasVisible,
  wetlandInlandWaterVisible,
  contextLayerRetry,
  onContextLayerStatusChange,
  onSelect,
  focusRequest,
}: CandidateMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const hoveredIdRef = useRef<string | number | null>(null);
  const selectedHexIdRef = useRef(selectedCandidateId);
  const activePresetRef = useRef(activePreset);
  const weightsRef = useRef(weights);
  const priorityVisibleRef = useRef(priorityVisible);
  const protectedAreasVisibleRef = useRef(protectedAreasVisible);
  const wetlandInlandWaterVisibleRef = useRef(wetlandInlandWaterVisible);
  const contextStatusRef = useRef<Record<ContextLayerId, ContextLayerStatus>>({
    protectedAreas: "idle",
    wetlandInlandWater: "idle",
  });
  const contextRequestedRef = useRef<Record<ContextLayerId, boolean>>({
    protectedAreas: false,
    wetlandInlandWater: false,
  });
  const focusRequestRef = useRef(focusRequest);
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState<string>();

  useEffect(() => {
    selectedHexIdRef.current = selectedCandidateId;
  }, [selectedCandidateId]);

  useEffect(() => {
    activePresetRef.current = activePreset;
    weightsRef.current = weights;
    priorityVisibleRef.current = priorityVisible;
    protectedAreasVisibleRef.current = protectedAreasVisible;
    wetlandInlandWaterVisibleRef.current = wetlandInlandWaterVisible;
  }, [
    activePreset,
    priorityVisible,
    protectedAreasVisible,
    wetlandInlandWaterVisible,
    weights,
  ]);

  useEffect(() => {
    focusRequestRef.current = focusRequest;
  }, [focusRequest]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const metrics: CandidateMapPerformance = {
      mapInitStartMs: performance.now(),
      expectedFeatureCount: metadata.feature_count,
    };
    let candidateReadyReported = false;
    let map: MapLibreMap;

    try {
      map = new MapLibreMap({
        container: containerRef.current,
        style: BASEMAP_STYLE_URL,
        collectResourceTiming: import.meta.env.DEV,
        center: [13.5, 56],
        zoom: 8,
        bearing: 0,
        pitch: 0,
      });
      mapRef.current = map;
      map.addControl(new NavigationControl({ showCompass: true }), "top-right");
      map.addControl(new ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-left");
    } catch (error) {
      const message = error instanceof Error ? error.message : "MapLibre could not initialize";
      const errorTimer = window.setTimeout(() => setMapError(message), 0);
      return () => window.clearTimeout(errorTimer);
    }

    const clearHover = () => {
      if (hoveredIdRef.current !== null && map.getSource(CANDIDATE_SOURCE_ID)) {
        map.setFeatureState({ source: CANDIDATE_SOURCE_ID, id: hoveredIdRef.current }, { hover: false });
      }
      hoveredIdRef.current = null;
      map.getCanvas().style.cursor = "";
    };

    const finishMetrics = () => {
      if (candidateReadyReported) return;
      candidateReadyReported = true;
      metrics.firstIdleAtMs = performance.now();
      metrics.candidateSourceToIdleMs =
        metrics.candidateSourceLoadedAtMs === undefined
          ? undefined
          : metrics.firstIdleAtMs - metrics.candidateSourceLoadedAtMs;
      metrics.totalCandidateReadyMs = metrics.firstIdleAtMs - metrics.mapInitStartMs;
      metrics.resourceTiming = getCandidateResourceTiming(CANDIDATE_DATA_URL);
      reportCandidateMapPerformance(metrics);
    };

    const onLoad = () => {
      metrics.mapLoadAtMs = performance.now();
      metrics.mapInitToLoadMs = metrics.mapLoadAtMs - metrics.mapInitStartMs;
      map.fitBounds(bboxFromMetadata(metadata), { padding: 40, duration: 0 });
      metrics.candidateSourceRequestedAtMs = performance.now();
      map.addSource(CANDIDATE_SOURCE_ID, { type: "geojson", data: CANDIDATE_DATA_URL });
      metrics.candidateSourceAddedAtMs = performance.now();
      map.addLayer(candidateFillLayer);
      map.addLayer(candidateBoundaryLayer);
      map.addLayer(candidateHoverOutlineLayer);
      map.addLayer(candidateSelectedFillLayer);
      map.addLayer(candidateSelectedHaloLayer);
      map.addLayer(candidateSelectedOutlineLayer);
      map.setPaintProperty(CANDIDATE_FILL_LAYER_ID, "fill-color", getMapFillPaint(activePresetRef.current, weightsRef.current));
      map.setPaintProperty(
        CANDIDATE_FILL_LAYER_ID,
        "fill-opacity",
        priorityVisibleRef.current ? 0.78 : 0,
      );
      const selectedFilter = getCandidateSelectedFilter(selectedHexIdRef.current);
      map.setFilter(CANDIDATE_SELECTED_FILL_LAYER_ID, selectedFilter);
      map.setFilter(CANDIDATE_SELECTED_HALO_LAYER_ID, selectedFilter);
      map.setFilter(CANDIDATE_SELECTED_OUTLINE_LAYER_ID, selectedFilter);
      if (focusRequestRef.current) {
        focusCandidate(map, focusRequestRef.current);
        if (focusRequestRef.current.selectOnFocus) {
          map.once("idle", () => selectFocusedCandidate(map, focusRequestRef.current, onSelect));
        }
      }
      setMapReady(true);
    };

    const onSourceData = (event: MapSourceDataEvent) => {
      if (event.sourceId !== CANDIDATE_SOURCE_ID || !event.isSourceLoaded) return;
      if (metrics.candidateSourceLoadedAtMs !== undefined) return;
      metrics.candidateSourceLoadedAtMs = performance.now();
      metrics.candidateSourceLoadMs =
        metrics.candidateSourceLoadedAtMs - (metrics.candidateSourceRequestedAtMs ?? metrics.mapInitStartMs);
      metrics.resourceTiming = toCandidateResourceTiming(event.resourceTiming?.[0]) ?? metrics.resourceTiming;
      map.once("idle", finishMetrics);
    };

    const onMove = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature || feature.id === undefined) return;
      map.getCanvas().style.cursor = "pointer";
      if (hoveredIdRef.current !== null && hoveredIdRef.current !== feature.id) {
        map.setFeatureState({ source: CANDIDATE_SOURCE_ID, id: hoveredIdRef.current }, { hover: false });
      }
      hoveredIdRef.current = feature.id;
      map.setFeatureState({ source: CANDIDATE_SOURCE_ID, id: feature.id }, { hover: true });
    };

    const onClick = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const candidate = parseCandidateProperties(feature as MapGeoJSONFeature);
      if (candidate) onSelect(candidate);
    };

    const onMapError = (event: ErrorEvent) => {
      const message = event.error.message ?? "MapLibre reported an unknown error";
      const lowerMessage = message.toLowerCase();
      const category = lowerMessage.includes("candidates.geojson")
        ? "candidate data"
        : lowerMessage.includes("webgl") || lowerMessage.includes("worker")
          ? "WebGL/worker"
          : "basemap/style";
      setMapError(`${category}: ${message}`);
    };

    map.on("load", onLoad);
    map.on("sourcedata", onSourceData);
    map.on("mousemove", CANDIDATE_FILL_LAYER_ID, onMove);
    map.on("mouseleave", CANDIDATE_FILL_LAYER_ID, clearHover);
    map.on("click", CANDIDATE_FILL_LAYER_ID, onClick);
    map.on("error", onMapError);

    return () => {
      clearHover();
      map.remove();
      mapRef.current = null;
    };
  }, [metadata, onContextLayerStatusChange, onSelect]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    const layerRequests: { id: ContextLayerId; visible: boolean }[] = [
      { id: "protectedAreas", visible: protectedAreasVisible },
      { id: "wetlandInlandWater", visible: wetlandInlandWaterVisible },
    ];

    for (const request of layerRequests) {
      const config = CONTEXT_LAYER_CONFIG[request.id];
      const sourceExists = Boolean(map.getSource(config.sourceId));
      const layersExist = config.layers.every((layer) => Boolean(map.getLayer(layer.id)));

      if (!request.visible) {
        for (const layer of config.layers) {
          if (map.getLayer(layer.id)) map.setLayoutProperty(layer.id, "visibility", "none");
        }
        continue;
      }

      if (sourceExists && layersExist) {
        for (const layer of config.layers) {
          map.setLayoutProperty(layer.id, "visibility", "visible");
        }
        if (contextStatusRef.current[request.id] !== "ready") {
          contextStatusRef.current[request.id] = "ready";
          onContextLayerStatusChange(request.id, "ready");
        }
        continue;
      }
      if (contextRequestedRef.current[request.id]) continue;

      contextRequestedRef.current[request.id] = true;
      contextStatusRef.current[request.id] = "loading";
      onContextLayerStatusChange(request.id, "loading");
      void fetch(config.dataUrl)
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json();
        })
        .then((data: unknown) => {
          if (mapRef.current !== map) return;
          if (!map.getSource(config.sourceId)) {
            map.addSource(config.sourceId, {
              type: "geojson",
              data: data as GeoJSONSourceSpecification["data"],
            });
          }
          for (const layer of config.layers) {
            if (!map.getLayer(layer.id)) map.addLayer(layer, CANDIDATE_FILL_LAYER_ID);
          }
          reorderContextLayers(map);
          const visible = request.id === "protectedAreas"
            ? protectedAreasVisibleRef.current
            : wetlandInlandWaterVisibleRef.current;
          for (const layer of config.layers) {
            map.setLayoutProperty(layer.id, "visibility", visible ? "visible" : "none");
          }
          contextStatusRef.current[request.id] = "ready";
          onContextLayerStatusChange(request.id, "ready");
        })
        .catch(() => {
          contextRequestedRef.current[request.id] = false;
          contextStatusRef.current[request.id] = "error";
          onContextLayerStatusChange(request.id, "error");
        });
    }
  }, [
    contextLayerRetry,
    mapReady,
    onContextLayerStatusChange,
    protectedAreasVisible,
    wetlandInlandWaterVisible,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusRequest || !map.isStyleLoaded()) return;
    focusCandidate(map, focusRequest);
    if (focusRequest.selectOnFocus) {
      const selectFrame = window.requestAnimationFrame(() => selectFocusedCandidate(map, focusRequest, onSelect));
      return () => window.cancelAnimationFrame(selectFrame);
    }
  }, [focusRequest, onSelect]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded() || !map.getLayer(CANDIDATE_FILL_LAYER_ID)) return;
    map.setPaintProperty(
      CANDIDATE_FILL_LAYER_ID,
      "fill-color",
      getMapFillPaint(activePreset, weights),
    );
  }, [activePreset, weights]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    if (map.getLayer(CANDIDATE_FILL_LAYER_ID)) {
      map.setPaintProperty(CANDIDATE_FILL_LAYER_ID, "fill-opacity", priorityVisible ? 0.78 : 0);
    }
  }, [priorityVisible]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded() || !map.getSource(CANDIDATE_SOURCE_ID)) return;
    const selectedFilter = getCandidateSelectedFilter(selectedCandidateId);
    for (const layerId of [
      CANDIDATE_SELECTED_FILL_LAYER_ID,
      CANDIDATE_SELECTED_HALO_LAYER_ID,
      CANDIDATE_SELECTED_OUTLINE_LAYER_ID,
    ]) {
      if (map.getLayer(layerId)) map.setFilter(layerId, selectedFilter);
    }
  }, [selectedCandidateId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const resizeFrame = window.requestAnimationFrame(() => map.resize());
    return () => window.cancelAnimationFrame(resizeFrame);
  }, [selectedCandidateId]);

  return (
    <div className="map-region">
      <div ref={containerRef} className="map-container" aria-label="Candidate priority map" />
      {mapError ? <div className="map-error" role="alert">Map error: {mapError}</div> : null}
    </div>
  );
}

function getMapFillPaint(activePreset: ActivePresetId, weights: WeightVector) {
  const score = activePreset === "custom" ? weights : PRESET_SCORE_FIELDS[activePreset];
  return getCandidateFillPaint(score)!["fill-color"]!;
}

type FocusRequest = Pick<ShortlistItem, "hex_id" | "longitude" | "latitude"> & { selectOnFocus?: boolean };

function selectFocusedCandidate(
  map: MapLibreMap,
  focus: FocusRequest | undefined,
  onSelect: (candidate: CandidateProperties) => void,
) {
  if (!focus) return;
  const feature = map
    .querySourceFeatures(CANDIDATE_SOURCE_ID)
    .find((item) => String(item.properties?.hex_id ?? item.id ?? "") === focus.hex_id);
  if (!feature) return;
  const candidate = parseCandidateProperties(feature as MapGeoJSONFeature);
  if (candidate) onSelect(candidate);
}

function focusCandidate(
  map: MapLibreMap,
  focus: Pick<ShortlistItem, "hex_id" | "longitude" | "latitude">,
) {
  if (!Number.isFinite(focus.longitude) || !Number.isFinite(focus.latitude)) return;
  map.easeTo({
    center: [focus.longitude, focus.latitude],
    zoom: Math.max(map.getZoom(), 11.5),
    bearing: 0,
    pitch: 0,
    duration: 650,
  });
}
