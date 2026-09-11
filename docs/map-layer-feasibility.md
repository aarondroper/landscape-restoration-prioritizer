# Map-layer feasibility audit

Step 31 is a measurement-only audit. The analytical raster, protected-area
GeoPackage, candidate units, scores, and production frontend were not changed.
All vectors measured here are display-only diagnostics derived from the
finalized analytical inputs. The model continues to use the original 10 m NMD
raster and the original protected-area processing.

The audit used the local artifacts `data/processed/nmd/nmd2023_v2_1_skane.tif`,
`data/processed/protected_areas.gpkg`, and
`data/processed/candidate_units.gpkg`. Raster masks were written window by
window and polygonized as contiguous binary patches; individual 10 m pixels
were not emitted as standalone frontend features. GeoJSON measurements use
compact projected-coordinate FeatureCollections. Gzip is deterministic level
9. Brotli was not available in the audit environment.

Generalization variants for raster-derived layers:

| Variant | Minimum display patch | Boundary simplification |
| --- | ---: | ---: |
| Light | 0.25 ha | 5 m, topology-preserving |
| Moderate | 1 ha | 15 m, topology-preserving |
| Strong | 5 ha | 30 m, topology-preserving |

## Recommendations at a glance

| Candidate | Recommendation | User-facing name | Loading |
| --- | --- | --- | --- |
| Protected areas | INCLUDE, terrestrial-semantic moderate display | Protected areas | Lazy |
| Wetland & inland water | INCLUDE, moderate display | Wetland & inland water | Lazy |
| Semi-natural habitat | EXCLUDE from lightweight MVP | — | — |
| Candidate agricultural land | EXCLUDE; redundant with candidates and inspector | — | — |

The proposed final Map Layers list is therefore:

1. Candidate priority
2. Protected areas
3. Wetland & inland water

The cross-layer feasibility assessment is:

| Layer | Explanatory/model fit | Cartography and browser cost | Preprocessing/maintenance | Runtime dependency |
| --- | --- | --- | --- | --- |
| Protected areas | High; terrestrial variant matches protected-terrestrial scoring semantics | Moderate display is 571 features / 40,607 vertices | Low after existing union; rerun when authoritative source is refreshed | None; static asset |
| Wetland & inland water | High; exact `riparian_focal_fraction` classes, explicitly not a stream network | Moderate display is 8,369 features / 419,746 vertices | Moderate; rerun mask polygonization and documented generalization | None; static asset |
| Semi-natural habitat | High; one input proxy explains two score components | Strong display is still 9,757 features / 1.21M vertices | High at regional extent; would need stronger generalization or tiles | None for static output; tiles become likely later |
| Candidate agricultural land | Low incremental value; duplicates candidate map and inspector | A class-3 polygon would be fragmented and redundant | Moderate/high extra vectorization for little user value | None; static output |

The approved MVP adoption status is: Protected areas — adopted; Wetland &
inland water — adopted; Semi-natural habitat — excluded; Candidate
agricultural land — excluded. No separate Ecological Network layer is
introduced.

## Protected areas

### Provenance and semantic choice

The authoritative source scope is the existing selected Naturvårdsverket
network: national parks, nature reserves, Natura 2000 SCI, SPA, and SPA/SCI.
The source GeoPackage contains 431 national-protection MultiPolygon features
and 276 Natura 2000 MultiPolygon features: 707 source geometries in total.
The existing physical union is one MultiPolygon feature after overlap
deduplication.

The marine-inclusive footprint is the closest vector representation of the
legal source, but it is a poor direct explanation of the score: much of the
raw footprint is marine while the analytical protected support is defined by
NMD pixel centers in the `terrestrial_land` role. The recommended display is
therefore variant B, a terrestrial-only mask made from the deduplicated
footprint clipped to Skåne and rasterized with `all_touched=False`, excluding
NMD no-data, inland water (61), and sea (62). This corresponds closely to the
protected-terrestrial semantics used by Protected-Area Reinforcement. It is a
display representation, not a replacement for the legal footprint or scoring
artifact.

### Measurements

| Representation | Features | Vertices | Geometry | Area | Raw | Gzip |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| A. Deduplicated footprint clipped to Skåne | 1 | 102,190 | MultiPolygon | 3,046.51 km² | 2.70 MB | 1.00 MB |
| B. Terrestrial NMD mask, exact 10 m | 6,008 | 270,920 | Polygon patches | 466.98 km² | 6.31 MB | 633 KB |
| B light: 0.25 ha / 5 m | 770 | 196,888 | Polygon patches | 465.74 km² / 99.73% | 4.22 MB | 501 KB |
| B moderate: 1 ha / 15 m | 571 | 40,607 | Polygon patches | 465.44 km² / 99.67% | 918 KB | 160 KB |
| B strong: 5 ha / 30 m | 404 | 28,536 | Polygon patches | 461.62 km² / 98.85% | 648 KB | 115 KB |

The terrestrial mask is about 15.33% of the clipped marine-inclusive vector
area. Modest simplification is useful: the moderate display retains 99.53% of
the exact mask area before simplification and 99.67% by its simplified
geometry area, while reducing the exact feature count from 6,008 to 571 and
vertices to 40,607. The moderate representation retains meaningful boundaries
and avoids exposing large marine areas that could be misread as terrestrial
protected context.

### Product recommendation and cartography

Include the terrestrial-semantic moderate representation and load it lazily
when first enabled. Its 160 KB gzip payload is small, it has 571 features, and
it has high explanatory value for the protected-area component. Style it as a
restrained desaturated mauve/plum fill at approximately 0.17 opacity with a
slightly darker, thin outline. Place it below candidate priority, and label
the legend `Protected terrestrial context`. Do not imply that the layer is a
parcel-level restriction or that the whole hexagon is protected.

## Wetland & inland water

### Provenance and semantic choice

This layer corresponds to the finalized `riparian_focal_fraction` source role,
not to a stream network. Its exact NMD classes are:

- wetland: 121–127, 128, 200, 211–218, and 221–228;
- inland water: 61;
- sea (62) is excluded.

The display meaning is therefore mapped wetland plus inland water. It should be
called `Wetland & inland water` in the interface. “Hydrology network” or
“streams/rivers” would overstate what NMD represents, especially for narrow
channels.

### Measurements

The authoritative binary mask is 1,012.21 km² (101,220.7 ha). The exact
contiguous-patch polygonization produced 562,194 features and 5,204,753
vertices, with area exactly matching the 1,012.21 km² mask. This is not a
frontend candidate.

| Variant | Features | Vertices | Retained mask area | Simplified display area | Raw | Gzip |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Light: 0.25 ha / 5 m | 25,621 | 1,654,690 | 887.04 km² / 87.63% | 887.04 km² / 87.63% | 37.46 MB | 4.04 MB |
| Moderate: 1 ha / 15 m | 8,369 | 419,746 | 803.24 km² / 79.36% | 806.54 km² / 79.68% | 9.75 MB | 1.47 MB |
| Strong: 5 ha / 30 m | 1,871 | 189,719 | 669.23 km² / 66.12% | 676.31 km² / 66.82% | 4.23 MB | 679 KB |

The light option is QUESTIONABLE under the payload standard and still has
25,621 features plus 1.65 million vertices. The moderate option is GOOD by
gzip size and stays below the 50,000-feature warning, but it deliberately
retains about 80% of mapped area. The strong option is smaller but discards
about one third of mapped area, which is too much for the default display.

### Product recommendation and cartography

Include the moderate generalized layer as a truthful contextual overview and
load it lazily. The audit payload and 8,369 features are acceptable for a
high-value optional layer, though the 420,000 vertices warrant lazy loading
and no automatic frontend fetch. Keep the legend label `Generalized mapped
wetland and inland water`. Use a muted blue-gray fill at approximately 0.24
opacity with a thin related blue outline, and place it below candidate
priority. Candidate priority must remain visually dominant. Include a short
caveat in the UI that this is mapped NMD wetland/inland-water context and not
a comprehensive watercourse network.

## Semi-natural habitat context

### Provenance and semantic choice

This is the display counterpart to the existing NMD `habitat_context_proxy`
role, and it would help explain both Habitat Context and Ecological Network
Context. It uses exactly these finalized classes:

- established forest on firm ground: 111–117;
- established forest on wetland: 121–127;
- open wetland: 200, 211–218, and 221–228;
- open vegetated land: 4211–4213, 4221–4223, and 4231–4233.

Transitional forest classes 118 and 128, arable class 3, inland water, sea,
artificial classes, peat extraction, and open non-vegetated class 411 are not
included. The label `Semi-natural habitat` should remain a qualified display
label: the analytical role is a structural NMD land-cover proxy, not a survey
of habitat quality or naturalness.

### Measurements

The authoritative binary mask is 5,313.19 km² (531,319.48 ha). Exact
polygonization produced 638,313 features and 12,308,938 vertices, so it is
not usable as a frontend payload.

| Variant | Features | Vertices | Retained mask area | Simplified display area | Raw | Gzip |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Light: 0.25 ha / 5 m | 61,207 | 7,237,511 | 5,148.07 km² / 96.89% | 5,148.07 km² / 96.89% | 158.72 MB | 17.55 MB |
| Moderate: 1 ha / 15 m | 25,152 | 2,045,127 | 4,969.21 km² / 93.53% | 4,991.92 km² / 93.95% | 45.94 MB | 7.13 MB |
| Strong: 5 ha / 30 m | 9,757 | 1,208,295 | 4,644.42 km² / 87.41% | 4,692.67 km² / 88.32% | 26.71 MB | 4.37 MB |

Even strong simplification remains QUESTIONABLE: 4.37 MB gzip expands to
26.71 MB raw and retains 1.21 million vertices. Moderate and light exceed
5 MB gzip, and light also exceeds the 50,000-feature warning. The strong
variant retains useful area but its regional geometry complexity is still
high for a static MapLibre source and its large-scale fill can compete with
the priority surface.

### Product recommendation

EXCLUDE from the lightweight MVP. A static GeoJSON is technically possible at
the strong setting, but it is not a good product payload. If this context is
needed later at regional extent with better fidelity, PMTiles/vector tiles or
a deliberately coarser raster-tile product becomes the appropriate direction;
that is evidence against adding it now. No tile pipeline was implemented in
this audit. A future non-tiled alternative would need a separately agreed
coarser display product and a clearly documented area-fidelity tradeoff.

## Candidate agricultural land

The analytical role is exactly NMD class 3 (`primary_candidate` / arable). The
candidate population already contains 26,395 hexagons and 408,984.25 ha of
mapped candidate land in total. The selected-area inspector exposes candidate
land hectares, and the Restoration Land Availability component explains the
same input through its component score. The candidate hexagon map also already
shows where the screening population is.

No diagnostic agricultural polygon vector was generated. A class-3 polygon
layer would be visually redundant, much more fragmented than the candidate
hexagons, and would risk being read as confirmed availability. If ever shown,
the only defensible label is `Candidate agricultural land`; it must not be
called restoration site, available land, or feasible land. Recommendation:
EXCLUDE from the lightweight MVP.

## Ecological Network Context

There is no separate authoritative ecological-network layer to display. The
final score derives from the surrounding habitat-context proxy, six neighboring
hexes, and opposing-side configuration. Synthetic lines or corridors would
add visual content without a source dataset and could imply a certainty the
model does not provide. `Semi-natural habitat` is the appropriate contextual
input for understanding both Habitat Context and Ecological Network Context,
but the audit recommends excluding that layer from this MVP on payload and
rendering grounds.

## Runtime architecture, loading, and cartography

All included candidates can remain static files bundled with the Cloudflare
Pages Direct Upload. There is no runtime external-data dependency: no database,
API, Worker, R2, WMS, WFS, PostGIS, or remote environmental service is needed.

`Candidate priority` remains eager because it is the primary existing map
surface. Both `Protected areas` and `Wetland & inland water` should be lazy:
fetch the static GeoJSON only when first toggled on, retain it in MapLibre for
the session, and keep candidate priority above them. No environmental layer
should be fetched on initial map load. Semi-natural habitat is `DO NOT INCLUDE`
for this MVP; if it is later restored at full regional extent, tiled
infrastructure is the likely practical architecture.

## Approved MVP adoption

The approved lightweight MVP implements only the two evidence-supported
optional layers:

- `Protected areas`: terrestrial-semantic moderate display, 1 ha minimum patch,
  15 m simplification, lazy static GeoJSON.
- `Wetland & inland water`: moderate display, 1 ha minimum patch, 15 m
  simplification, lazy static GeoJSON, with the explicit mapped-context caveat.

Do not implement Semi-natural habitat, Candidate agricultural land, or an
invented Ecological Network layer in Step 32. Do not introduce PMTiles or any
other tiled infrastructure as part of the lightweight MVP.
