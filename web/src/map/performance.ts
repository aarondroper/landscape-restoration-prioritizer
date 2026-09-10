export interface CandidateResourceTiming {
  transferSize?: number;
  encodedBodySize?: number;
  decodedBodySize?: number;
  duration?: number;
}

export interface CandidateMapPerformance {
  mapInitStartMs: number;
  mapLoadAtMs?: number;
  candidateSourceRequestedAtMs?: number;
  candidateSourceAddedAtMs?: number;
  candidateSourceLoadedAtMs?: number;
  firstIdleAtMs?: number;
  mapInitToLoadMs?: number;
  candidateSourceLoadMs?: number;
  candidateSourceToIdleMs?: number;
  totalCandidateReadyMs?: number;
  expectedFeatureCount: number;
  resourceTiming?: CandidateResourceTiming;
}

declare global {
  interface Window {
    __candidateMapPerformance?: CandidateMapPerformance;
  }
}

export function getCandidateResourceTiming(url: string): CandidateResourceTiming | undefined {
  const resource = performance
    .getEntriesByName(new URL(url, window.location.href).href)
    .find((entry): entry is PerformanceResourceTiming => entry.entryType === "resource");
  if (!resource) return undefined;
  return {
    transferSize: resource.transferSize,
    encodedBodySize: resource.encodedBodySize,
    decodedBodySize: resource.decodedBodySize,
    duration: resource.duration,
  };
}

export function toCandidateResourceTiming(
  resource: PerformanceResourceTiming | undefined,
): CandidateResourceTiming | undefined {
  if (!resource) return undefined;
  return {
    transferSize: resource.transferSize,
    encodedBodySize: resource.encodedBodySize,
    decodedBodySize: resource.decodedBodySize,
    duration: resource.duration,
  };
}

export function reportCandidateMapPerformance(metrics: CandidateMapPerformance): void {
  if (!import.meta.env.DEV) return;
  window.__candidateMapPerformance = metrics;
  console.info("Candidate map performance:", metrics);
}
