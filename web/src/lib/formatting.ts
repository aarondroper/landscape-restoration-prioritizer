export function formatScore(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : "—";
}

export function formatPercent(fraction: number, decimals = 1): string {
  return Number.isFinite(fraction) ? `${(fraction * 100).toFixed(decimals)}%` : "—";
}

export function formatHectares(value: number): string {
  return Number.isFinite(value) ? `${value.toFixed(2)} ha` : "—";
}

export function formatRatio(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : "—";
}

export function formatProtectedDistance(steps: number): string {
  if (!Number.isFinite(steps)) return "—";
  if (steps === 0) return "Within this unit";
  if (steps === 1) return "Adjacent hex context";
  return `${Math.round(steps)} grid steps`;
}

export function formatAnalysisUnitId(hexId: string): string {
  const match = /^h_(-?\d+)_(-?\d+)$/.exec(hexId);
  if (!match) return hexId;
  return `H-${match[1].replace(/^-/, "")}-${match[2].replace(/^-/, "")}`;
}
