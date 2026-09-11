import type { ActivePresetId, PresetDefinition, PresetId } from "../data/types";

interface PresetSelectorProps {
  activePreset: ActivePresetId;
  presets: Record<PresetId, PresetDefinition>;
  onChange: (preset: ActivePresetId) => void;
}

export function PresetSelector({ activePreset, presets, onChange }: PresetSelectorProps) {
  return (
    <fieldset className="preset-selector">
      <legend className="section-label">Prioritization preset</legend>
      <select
        className="preset-select"
        value={activePreset}
        onChange={(event) => onChange(event.target.value as ActivePresetId)}
        aria-label="Prioritization preset"
      >
        {(Object.keys(presets) as PresetId[]).map((id) => (
          <option value={id} key={id}>{presets[id].name}</option>
        ))}
        <option value="custom">Custom</option>
      </select>
      <p className="preset-description">
        {activePreset === "custom"
          ? "Uses your adjusted relative weights across all five model dimensions."
          : presets[activePreset].description}
      </p>
    </fieldset>
  );
}
