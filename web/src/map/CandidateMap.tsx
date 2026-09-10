import { useEffect, useRef, useState } from "react";
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
import type { CandidateProperties, DeliveryMetadata } from "../data/types";
import {
  candidateFillLayer,
  candidateHoverOutlineLayer,
} from "./candidateLayers";
import {
  CANDIDATE_DATA_URL,
  CANDIDATE_FILL_LAYER_ID,
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
}

interface InspectionFeature {
  id: string;
  properties: Pick<
    CandidateProperties,
    | "hex_id"
    | "balanced_score"
    | "connectivity_first_score"
    | "riparian_restoration_score"
  >;
}

function formatScore(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(3) : "—";
}

function inspectFeature(feature: MapGeoJSONFeature): InspectionFeature | undefined {
  const properties = feature.properties as Partial<CandidateProperties>;
  const id = String(feature.id ?? properties.hex_id ?? "");
  if (!id) return undefined;
  return {
    id,
    properties: {
      hex_id: String(properties.hex_id ?? id),
      balanced_score: Number(properties.balanced_score),
      connectivity_first_score: Number(properties.connectivity_first_score),
      riparian_restoration_score: Number(properties.riparian_restoration_score),
    },
  };
}

function bboxFromMetadata(metadata: DeliveryMetadata): [[number, number], [number, number]] {
  const bbox = metadata.bbox_epsg_4326;
  return [
    [bbox.min_longitude, bbox.min_latitude],
    [bbox.max_longitude, bbox.max_latitude],
  ];
}

export function CandidateMap({ metadata }: CandidateMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const hoveredIdRef = useRef<string | number | null>(null);
  const [inspection, setInspection] = useState<InspectionFeature>();
  const [mapError, setMapError] = useState<string>();

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
      map.addControl(new NavigationControl({ showCompass: false }), "top-right");
      map.addControl(new ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-left");
    } catch (error) {
      const message = error instanceof Error ? error.message : "MapLibre could not initialize";
      const errorTimer = window.setTimeout(() => setMapError(message), 0);
      return () => window.clearTimeout(errorTimer);
    }

    const clearHover = () => {
      if (hoveredIdRef.current !== null && map.getSource(CANDIDATE_SOURCE_ID)) {
        map.setFeatureState(
          { source: CANDIDATE_SOURCE_ID, id: hoveredIdRef.current },
          { hover: false },
        );
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
      map.fitBounds(bboxFromMetadata(metadata), { padding: 36, duration: 0 });
      metrics.candidateSourceRequestedAtMs = performance.now();
      map.addSource(CANDIDATE_SOURCE_ID, {
        type: "geojson",
        data: CANDIDATE_DATA_URL,
      });
      metrics.candidateSourceAddedAtMs = performance.now();
      map.addLayer(candidateFillLayer);
      map.addLayer(candidateHoverOutlineLayer);
    };

    const onSourceData = (event: MapSourceDataEvent) => {
      if (event.sourceId !== CANDIDATE_SOURCE_ID || !event.isSourceLoaded) return;
      if (metrics.candidateSourceLoadedAtMs !== undefined) return;
      metrics.candidateSourceLoadedAtMs = performance.now();
      metrics.candidateSourceLoadMs =
        metrics.candidateSourceLoadedAtMs - (metrics.candidateSourceRequestedAtMs ?? metrics.mapInitStartMs);
      metrics.resourceTiming =
        toCandidateResourceTiming(event.resourceTiming?.[0]) ?? metrics.resourceTiming;
      map.once("idle", finishMetrics);
    };

    const onMove = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature || feature.id === undefined) return;
      map.getCanvas().style.cursor = "pointer";
      if (hoveredIdRef.current !== null && hoveredIdRef.current !== feature.id) {
        map.setFeatureState(
          { source: CANDIDATE_SOURCE_ID, id: hoveredIdRef.current },
          { hover: false },
        );
      }
      hoveredIdRef.current = feature.id;
      map.setFeatureState({ source: CANDIDATE_SOURCE_ID, id: feature.id }, { hover: true });
    };

    const onClick = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const inspected = inspectFeature(feature);
      if (inspected) setInspection(inspected);
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
  }, [metadata]);

  return (
    <div className="map-region">
      <div ref={containerRef} className="map-container" aria-label="Candidate priority map" />
      {mapError ? (
        <div className="map-error" role="alert">
          Map error: {mapError}
        </div>
      ) : null}
      {inspection ? (
        <aside className="inspection-panel" aria-label="Temporary candidate inspection">
          <div className="inspection-heading">
            <span>Technical inspection</span>
            <button type="button" onClick={() => setInspection(undefined)} aria-label="Close inspection">
              ×
            </button>
          </div>
          <dl>
            <dt>hex_id</dt>
            <dd>{inspection.properties.hex_id}</dd>
            <dt>Balanced score</dt>
            <dd>{formatScore(inspection.properties.balanced_score)}</dd>
            <dt>Connectivity First</dt>
            <dd>{formatScore(inspection.properties.connectivity_first_score)}</dd>
            <dt>Riparian Restoration</dt>
            <dd>{formatScore(inspection.properties.riparian_restoration_score)}</dd>
          </dl>
        </aside>
      ) : null}
    </div>
  );
}
