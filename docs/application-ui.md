# Application UI

The application is a desktop-first landscape-planning interface for Skåne. The
workspace has a control panel, a central MapLibre map, and an optional selected-
area inspector. The map remains the primary surface.

## Controls and scoring

The control panel contains the project identity, prioritization preset, component
weights, map layers, and a Top Candidates shortlist. Presets are loaded from
`/data/presets.json`. Switching a preset changes the displayed precomputed score
field without refetching the candidate GeoJSON.

Custom weights are non-negative slider values from 0 to 100. They are normalized
when calculating the client-side ranking, and the effective percentages are
shown beside each slider. Custom ranking uses the delivered component scores; it
does not create a second analytical model.

The candidate map uses one continuous 0–100 relative-score ramp. Candidate
priority is visible by default and can be hidden without disabling hover,
selection, or candidate hit-testing.

## Top Candidates

The shortlist shows the top five entries for the active named preset from the
static shortlist companion artifact. Each row is a keyboard-accessible button
showing rank, score, and analysis-unit ID. Selecting a row uses the same state
and inspector as selecting a map feature, and centers the map on that unit.

Changing the preset updates the shortlist while preserving the current map
selection and camera. The shortlist is ranking support, not a restoration
recommendation.

## Map Layers

The map-layer controls expose:

- Candidate priority;
- Protected areas; and
- Wetland & inland water.

The two contextual layers are off by default. They are display-only, have no
pointer events or inspector content, and are loaded only when first enabled.
The protected layer represents terrestrial protected context. The wetland and
inland-water layer is generalized mapped context, not a comprehensive
watercourse network. Failed optional loads show a retry action without affecting
the candidate map or ranking controls.

## Selected-area inspector

Clicking a candidate opens an inspector with Overview, Components, and Details
tabs. The Components tab shows the five component scores and active weights. The
Details tab shows delivered raw/supporting facts with units and caveats.

The deterministic “Why this area?” explanation is generated from the delivered
component scores. It describes the strongest dimensions and may identify low
availability or riparian context. It does not claim parcel feasibility, exact
distances, biodiversity outcomes, terrain quality, stream proximity, or habitat
connectivity beyond the documented model semantics.

## Accessibility and responsive behavior

Native select, checkbox, and range-input semantics are retained. Shortlist rows
are buttons, the inspector uses Radix UI Tabs for keyboard navigation, and
interactive controls have visible focus states. A narrow-screen fallback moves
the inspector over the map and compresses the control panel; a full mobile
redesign is outside the current interface scope.

Icons are loaded from the semantic SVG assets in `web/public/icons/`.
