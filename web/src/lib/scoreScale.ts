export const SCORE_RAMP = [
  { value: 0, color: "#f1e9bf" },
  { value: 25, color: "#d6dea6" },
  { value: 40, color: "#abc99a" },
  { value: 55, color: "#77ae91" },
  { value: 70, color: "#4b9187" },
  { value: 85, color: "#286f72" },
  { value: 100, color: "#154f5b" },
] as const;

export const SCORE_RAMP_CSS = `linear-gradient(90deg, ${SCORE_RAMP.map(
  (stop) => `${stop.color} ${stop.value}%`,
).join(", ")})`;

function hexToRgb(hex: string): [number, number, number] {
  return [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16)) as [number, number, number];
}

function rgbToHex(rgb: [number, number, number]): string {
  return `#${rgb.map((channel) => Math.round(channel).toString(16).padStart(2, "0")).join("")}`;
}

export function getScoreColor(score: number): string {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(score) ? score : 0));
  const upperIndex = SCORE_RAMP.findIndex((stop) => stop.value >= clamped);
  if (upperIndex <= 0) return SCORE_RAMP[0].color;
  if (upperIndex === -1) return SCORE_RAMP[SCORE_RAMP.length - 1].color;
  const lower = SCORE_RAMP[upperIndex - 1];
  const upper = SCORE_RAMP[upperIndex];
  const fraction = (clamped - lower.value) / (upper.value - lower.value);
  const lowerRgb = hexToRgb(lower.color);
  const upperRgb = hexToRgb(upper.color);
  return rgbToHex([
    lowerRgb[0] + (upperRgb[0] - lowerRgb[0]) * fraction,
    lowerRgb[1] + (upperRgb[1] - lowerRgb[1]) * fraction,
    lowerRgb[2] + (upperRgb[2] - lowerRgb[2]) * fraction,
  ]);
}

function relativeLuminance(hex: string): number {
  return hexToRgb(hex).reduce((sum, channel) => {
    const value = channel / 255;
    return sum + (value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  }, 0) / 3;
}

export function getScoreTextColor(score: number): string {
  return relativeLuminance(getScoreColor(score)) < 0.38 ? "#f8fbf7" : "#20352f";
}
