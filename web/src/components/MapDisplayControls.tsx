import {
  CONTEXT_LAYER_COLORS,
  type ContextLayerId,
  type ContextLayerStatuses,
} from "../map/contextualLayers";
import { Icon } from "./Icon";
import { ScoreLegend } from "./ScoreLegend";

interface MapDisplayControlsProps {
  priorityVisible: boolean;
  onPriorityChange: (visible: boolean) => void;
  contextLayerVisibility: Record<ContextLayerId, boolean>;
  contextLayerStatuses: ContextLayerStatuses;
  onContextLayerChange: (layer: ContextLayerId, visible: boolean) => void;
  onContextLayerRetry: (layer: ContextLayerId) => void;
}

export function MapDisplayControls({
  priorityVisible,
  onPriorityChange,
  contextLayerVisibility,
  contextLayerStatuses,
  onContextLayerChange,
  onContextLayerRetry,
}: MapDisplayControlsProps) {
  const contextLayer = (
    id: ContextLayerId,
    label: string,
    description: string,
    fillColor: string,
    outlineColor: string,
  ) => {
    const status = contextLayerStatuses[id];
    return (
      <div className="context-layer-row" key={id}>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={contextLayerVisibility[id]}
            onChange={(event) => onContextLayerChange(id, event.target.checked)}
            aria-describedby={`${id}-legend`}
          />
          <span>{label}</span>
        </label>
        <div id={`${id}-legend`} className="context-layer-legend">
          <span
            className="context-layer-swatch"
            style={{ backgroundColor: fillColor, borderColor: outlineColor }}
            aria-hidden="true"
          />
          <span>{description}</span>
          {status === "loading" ? <span className="context-layer-status">Loading…</span> : null}
          {status === "error" ? (
            <>
              <span className="context-layer-status" role="status">Unable to load</span>
              <button
                type="button"
                className="context-layer-retry"
                onClick={() => onContextLayerRetry(id)}
              >
                Retry
              </button>
            </>
          ) : null}
        </div>
      </div>
    );
  };

  return (
    <section className="sidebar-section map-layers" aria-labelledby="map-layers-title">
      <div className="section-heading-row">
        <h2 id="map-layers-title" className="section-label">
          <Icon className="section-icon" name="map-layers.svg" aria-hidden="true" />
          Map layers
        </h2>
      </div>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={priorityVisible}
          onChange={(event) => onPriorityChange(event.target.checked)}
        />
        <span>Candidate priority</span>
      </label>
      <ScoreLegend priorityVisible={priorityVisible} />
      {contextLayer(
        "protectedAreas",
        "Protected areas",
        "Protected terrestrial context",
        CONTEXT_LAYER_COLORS.protectedFill,
        CONTEXT_LAYER_COLORS.protectedOutline,
      )}
      {contextLayer(
        "wetlandInlandWater",
        "Wetland & inland water",
        "Generalized mapped wetland and inland water",
        CONTEXT_LAYER_COLORS.wetlandFill,
        CONTEXT_LAYER_COLORS.wetlandOutline,
      )}
    </section>
  );
}
