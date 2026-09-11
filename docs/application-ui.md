# Application UI

Step 26 turns the MapLibre foundation into a desktop-first landscape-planning
tool for Skåne, Sweden. The application uses a three-part workspace: a
320-pixel control panel, a dominant central map, and an optional 390-pixel
selected-area inspector. The map expands into the space used by the inspector
when no candidate is selected.

## Design and behavior

The interface uses warm near-white panels, quiet borders, charcoal text, and
muted forest/teal accents. The map remains the primary surface. Its candidate
fill uses the active precomputed score field and one shared absolute 0–100
ordered ramp, with stops at 0, 25, 40, 55, 70, 85, and 100; the legend uses
the same ramp and describes the values as relative model scores from 0 to 100.

The left panel contains the project identity, a compact prioritization-preset
selector, adjustable component weights, Map Layers with candidate priority, a
compact Top Candidates screening shortlist, and the screening-scale model
note. The shortlist shows the top five entries for a canonical preset from the
static top-50 delivery companion. Custom weights use a separately lazy-loaded
component-score index and client-side ranking; it is not a second analytical
model.
Preset metadata and canonical weights are loaded from `/data/presets.json`.
Switching a preset updates the MapLibre fill paint expression and selected
candidate score without refetching GeoJSON. Custom slider values are raw
non-negative relative weights from 0 to 100; they are normalized at calculation
time and the effective percentages are shown beside each slider.

Each shortlist row is a keyboard-accessible button showing rank, the active
preset score, and the analysis-unit ID. Selecting a row uses the same selected
candidate state and inspector as a map click, then centers the map with a
restrained 650 ms `easeTo` at zoom 11.5 or the current closer zoom. Changing
the preset changes the shortlist immediately but preserves the current
selection and camera. The shortlist is screening/ranking support, not a
recommendation guarantee.

Clicking a candidate opens the selected-area inspector and applies a persistent
selected fill plus a two-layer outline: a wider warm off-white halo beneath a
narrower opaque deep-teal primary outline. All selected layers use the normal
`hex_id` filter and clear to a no-selection filter; the selection persists when
the active preset changes. Hover remains a thinner, weaker feature-state
outline. The inspector has Overview, Components, and Details tabs. The
Components tab shows all five finalized component scores as horizontal bars and
subtly shows the active preset weights. The Details tab exposes the delivered
raw/supporting facts with units and caveats. Radix UI Tabs supplies keyboard
accessible tab behavior; the preset select, weight sliders, and Map Layers
checkbox retain native semantics with labels and visible keyboard focus states.

## Explanation policy

“Why this area?” is deterministic UI copy generated from the five delivered
component scores. Display-only explanation bands are: strong at `>=75`,
moderate at `>=50`, and weaker below `50`. The helper reports the two strongest
dimensions, then may add the low availability or low riparian limitation. The
language is deliberately limited to the supported semantics of the delivered
fields; it does not claim parcel feasibility, exact distances, biodiversity
outcomes, terrain quality, stream proximity, or habitat-block connection.

## Real-data-only content

The application presents only the existing candidate GeoJSON properties and
the approved preset metadata. It does not show protected-area polygons,
rivers/streams, land-cover layers, terrain, hillshade, imagery, place search,
filters, full ranking browsers, exports, or other mock-up placeholders. `hex_id` is called
an analysis unit and the geometry is treated as a deterministic 500 m hexagon,
not an H3 cell or parcel.

## Validation and deferred work

The application includes a basic narrow-screen fallback: the left panel
compresses into a shorter top region and the inspector overlays the map. Map
resize is requested when the inspector opens or closes. Loading and error
states are concise and product-oriented.

No browser runtime was installed in the validation environment, so visual
inspection of map rendering, hover, click, and responsive behavior remains a
local user-review step. Development and production-preview HTTP smoke checks,
the frontend build gates, and the backend gates remain the automated checks.

Deferred features include search/geocoding, advanced filters,
exporting, URL/share state, methodology UI, additional environmental layers,
PMTiles, APIs, PostGIS, deployment, and a full mobile redesign.

## Custom icon assets

Placeholder assets live in `web/public/icons/` and are loaded through semantic
slots. The exact replacement filenames are:

- `app-logo.svg`
- `component-habitat.svg`
- `component-network.svg`
- `component-riparian.svg`
- `component-protected.svg`
- `component-availability.svg`
- `top-candidates.svg`
- `selected-area.svg`

Replace any placeholder by overwriting the corresponding SVG; no React code
needs to change. Prefer a square `viewBox` such as `0 0 24 24`, a transparent
background, no embedded raster images, reasonably simple paths, and no
page-sized hard-coded dimensions. The current implementation uses `<img>`;
external SVG `currentColor` does not inherit from the page, so author final
fill/stroke colors directly in replacement files.
