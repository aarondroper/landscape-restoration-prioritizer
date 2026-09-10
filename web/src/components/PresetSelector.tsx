import type { PresetDefinition, PresetId } from "../data/types";

interface PresetSelectorProps {
  activePreset: PresetId;
  presets: Record<PresetId, PresetDefinition>;
  onChange: (preset: PresetId) => void;
}

export function PresetSelector({ activePreset, presets, onChange }: PresetSelectorProps) {
  return (
    <fieldset className="preset-selector">
      <legend className="section-label">Prioritization preset</legend>
      <div className="preset-options">
        {(Object.keys(presets) as PresetId[]).map((id) => (
          <label className="preset-option" data-active={activePreset === id} key={id}>
            <input
              type="radio"
              name="prioritization-preset"
              value={id}
              checked={activePreset === id}
              onChange={() => onChange(id)}
            />
            <span>{presets[id].name}</span>
          </label>
        ))}
      </div>
      <p className="preset-description">{presets[activePreset].description}</p>
    </fieldset>
  );
}
