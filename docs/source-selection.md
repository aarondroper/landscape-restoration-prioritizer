# Source-selection notes

This document records the comparison reasoning behind the canonical registry
in [`data-sources.md`](data-sources.md). It is intentionally a research note,
not an ingestion design.

## Decision logic

The MVP favors authoritative Swedish sources that are anonymous, scriptable,
reusable, spatially compatible with EPSG:3006, and proportionate to an
approximately 500 m screening unit. Detail does not justify a source when its
access, legal, or processing burden would make the result difficult to
reproduce.

## Main decisions

### NMD2023 is the land-cover backbone, with a coverage gate

The current official product is NMD2023 Basskikt v2.1, not an unspecified
NMD2023 family. Its product description documents 10 m raster data in
EPSG:3006, 54 classes in four hierarchy levels, and CC0 reuse. The v2.1
national archive was publicly reachable and its advertised size was about 2.7
GB. The delivery is not a convenient regional tile service.

The product documentation says v2.x coverage is being produced progressively
and identifies a coverage/extent metadata layer. The archive was not unpacked
or downloaded: only HTTP headers, ZIP central-directory metadata, and small
metadata material were inspected. Consequently, complete Skåne coverage is
not yet verified at geometry level. Ingestion must first inspect the v2.1
extent metadata and fail closed if any part of the adopted Skåne boundary is
outside the released coverage.

The basskikt is sufficient for a first-pass classification of broad forest,
open, agricultural, built/artificial, wetland, and inland-water context. Its
known weakness is narrow stream representation; that weakness is accepted for
the MVP because the requested units are approximately 500 m and no equally
open, authoritative, low-friction stream-vector alternative was found.

### Protected-area scope is deliberately small

The minimum defensible protected network is:

1. national parks and nature reserves from `SkyddadeOmraden`, using the
   current/gällande status where available; and
2. all Natura 2000 polygons in `N2000` with `SCI`, `SPA`, or `SPA/SCI` type.

Both official WFS services are anonymous, polygonal, EPSG:3006-compatible,
and expose the fields needed for filtering and provenance. They are separate
legal designations but one physical conservation network for later spatial
analysis. Overlap must be de-duplicated before physical-area measures.

### NMD-only hydrology is adequate for the MVP, with a documented limitation

NMD inland-water and wetland classes provide a reproducible broad riparian
context. Lantmäteriet Hydrografi Nedladdning would provide stronger detailed
water geometry, but it requires authorization/account flow and legal approval,
and its terms create additional redistribution constraints. SMHI's open
hydrology material does not solve this: SMHI documents that lake/stream
geometries based on Lantmäteriet are view-only, while the open WFS material is
catchment-oriented.

Therefore no separate hydrography source is selected for the MVP. This is a
containment decision, not a claim that NMD captures every stream. A future
hydrography enhancement should be justified by a demonstrated sensitivity of
the riparian component to missed narrow channels.

### Boundary source favors reproducibility over directness

SCB DeSO 2025 is the adopted boundary source. It has an anonymous WFS,
`lanskod`, a dated boundary version, and EPSG:3006 geometry. Selecting county
code 12 and dissolving the polygons is simple and reproducible. Lantmäteriet's
direct county product is authoritative and preferable geometrically, but the
inspected access path requires authorization/order flow, so it is deferred.

### Slope is deferred

Lantmäteriet Markhöjdmodell Nedladdning is technically capable: 1 m COG tiles,
STAC access, and SWEREF 99 TM/RH2000 metadata. However, it adds authorization
friction, substantial raster processing, and vertical-data decisions. Slope
is not a standalone component, and its incremental value within a 500 m
feasibility screen is not yet established. It is therefore deferred rather
than included merely because the product exists.

### TUVA is the strongest missing-source candidate, but not required for the
first source stack

Jordbruksverket's Ängs- och betesmarksinventeringen/TUVA is an open WFS with
surveyed meadow/pasture polygons and fields for restoration, nature qualities,
trees, water, and inventory dates. It could materially strengthen the
identification of existing semi-natural habitat, which NMD broad classes do
not explicitly guarantee. It remains optional/deferred for the initial MVP
stack because exact product-level reuse terms and field semantics should be
verified at ingestion time. It is the first additional source to revisit.

## Risks carried into implementation

- NMD v2.1 Skåne coverage is an acceptance gate, not yet a verified fact.
- NMD wetland and open-habitat classes require documented interpretation;
  class membership should not be treated as a field-validated nature value.
- Narrow streams may be missed by raster-only riparian context.
- Protected designations overlap and must be physically de-duplicated.
- Source dates differ; retrieval dates, product versions, and update fields
  must be captured for reproducibility.
- Authorization/legal review requirements for Lantmäteriet products remain a
  barrier to a fully public pipeline.
