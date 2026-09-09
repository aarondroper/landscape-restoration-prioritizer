# Methodology contract

## Purpose and study area

Landscape Restoration Prioritizer is a regional screening and decision-support
tool for **Skåne län, Sweden**. Its primary client question is:

> Where in Skåne should a conservation organization investigate
> landscape-restoration opportunities that could strengthen ecological
> networks, improve riparian function, and expand semi-natural habitat?

The tool is intended to identify areas for further investigation. It does not
claim that a particular parcel should be restored, and it does not estimate a
scientific probability of restoration suitability.

## Analysis units and candidate land

The planned analysis unit is an approximately **500 m hexagonal cell**. The
exact construction may be adjusted if technical or data considerations justify
it. Cells should ultimately be clipped or filtered to plausible candidate
restoration land rather than scoring every location indiscriminately. The
candidate-land definition, including treatment of land cover and obvious
constraints, remains to be validated against authoritative source data.

The canonical analytical CRS is **SWEREF 99 TM (EPSG:3006)**, the national
Swedish projected CRS used for metre-based distance, area, and hex-grid
operations. The selected NMD2023 Basskikt v2.1 is a 10 m EPSG:3006 raster, so
the primary land-cover backbone does not require an initial CRS conversion.
Source-specific CRS conversion, raster resampling, and precision decisions
will be documented during ingestion validation.

The study-area identifiers are county/län code **12** and NUTS 3 code
**SE224**. The reproducible boundary source is SCB DeSO 2025: select
`lanskod=12` from the anonymous WFS and dissolve the returned polygons.

## Planned analytical components

Each candidate cell will retain five independently available component scores:

1. **Habitat context** — proximity to or presence within landscapes containing
   existing semi-natural habitat.
2. **Ecological network context** — potential to reinforce, connect, enlarge,
   or reduce isolation among existing habitat areas.
3. **Riparian opportunity** — relationship to rivers, lakes, wetlands, and
   other relevant hydrological features.
4. **Protected-area reinforcement** — relationship to nature reserves, Natura
   2000, and the existing conservation network.
5. **Land-restoration feasibility** — land-cover composition and obvious
   constraints, favoring plausibly modifiable land and penalizing built,
   artificial, or otherwise clearly unsuitable areas.

Raw indicators will be transformed into normalized **0–100 relative component
scores** within the study population. The final overall score will be a
weighted mean of the five components. Component values must remain available
alongside the overall score for interpretation and auditability.

The planned weight presets are **Balanced**, **Connectivity first**, and
**Riparian restoration**. These are predefined component-weight configurations,
not separate analytical models.

The first-pass source support is deliberately limited. NMD2023 supplies the
land-cover, broad habitat, wetland, inland-water, and feasibility context;
Naturvårdsverket's `SkyddadeOmraden` and `N2000` WFS layers supply the adopted
protected-area network; and the SCB boundary is used only as a study-area
mask. NMD-only hydrology is accepted for the MVP, with narrow streams recorded
as a known limitation. Slope is deferred rather than added as a feasibility
sub-indicator. Protected-area overlaps must be physically de-duplicated before
area-based analysis.

## Scoring principles

Indicator definitions, transformations, normalization choices, and weights
must be explicit, deterministic, documented, and defensible for a regional
screening tool. Robust or percentile-based normalization may be used where it
is justified by the source-data distributions. Scores express relative
decision-support opportunity within the study population; they are not
absolute ecological value, restoration probability, or a parcel-level
recommendation.

Exact indicators and transformations remain to be validated against the source
data before implementation. This document intentionally does not prescribe
detailed formulas prematurely.

## Current assumptions and limitations

- The study area is Skåne län; its MVP boundary is derived from SCB DeSO 2025
  polygons selected by `lanskod=12` and dissolved.
- NMD2023 Basskikt v2.1 is the selected land-cover backbone, but complete
  Skåne coverage remains an ingestion-time acceptance check because the v2.x
  product has been released progressively.
- A nominal 500 m hexagonal unit is a planning assumption, not a final
  immutable implementation detail.
- Candidate-land filtering, feature-distance definitions, habitat/network
  interpretations, and treatment of missing data require source-specific
  validation.
- NMD inland-water and wetland classes are the MVP riparian source. Narrow
  streams may be underrepresented; a separate hydrographic vector source is
  deferred because available alternatives add access/legal complexity or are
  not openly downloadable.
- The protected-area scope is national parks and nature reserves from
  `SkyddadeOmraden` plus Natura 2000 `SCI`, `SPA`, and `SPA/SCI` polygons.
- TUVA is the first optional enrichment candidate if the MVP needs a more
  explicit surveyed semi-natural-habitat anchor than NMD provides.
- National and regional datasets may differ in date, resolution, classification,
  completeness, and licensing; temporal mismatch and scale effects may affect
  comparability.
- The screening output will require ecological review and local investigation
  before any restoration decision.
- No field validation, parcel-level feasibility assessment, landowner context,
  costs, or implementation constraints are included in this initial contract.
