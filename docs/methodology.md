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

The Step 6 analysis unit is a regular **500 m flat-to-flat pointy-top hexagon**
in **EPSG:3006**. Its side length is `500 / sqrt(3) = 288.6751346 m` and its
theoretical complete area is `216,506.3509 m²` (21.6506351 ha). Every durable
unit retains the complete regular geometry; it is not clipped to the coastline,
administrative boundary, NMD footprint, or candidate pixels. Hexagons are
analysis units, not parcels, and their boundaries have no ecological or legal
meaning. They form a consistent regional screening tessellation.

The grid is anchored mathematically at the fixed EPSG:3006 origin `(0, 0)`.
For integer grid coordinates `(col, row)`, the center is
`((col + row / 2) * 500 m, row * 1.5 * side)`, and the stable identifier is
`h_<col>_<row>`. The same CRS, width, anchor, and raster extent therefore
regenerate the same grid independently of boundary vertex ordering. The
processed NMD raster supplies the factual spatial extent. Cells with no
terrestrial NMD pixel are discarded from the Step 6 factual grid. Step 6 does
not apply a candidate eligibility threshold; that separate population
definition is specified below for Step 7.

The candidate-land definition, including treatment of land cover and obvious
constraints, is explicitly defined by the Step 5 NMD semantic contract below.
It identifies pixels for later investigation; it does not establish
restoration suitability, availability, or feasibility.

The canonical analytical CRS is **SWEREF 99 TM (EPSG:3006)**, the national
Swedish projected CRS used for metre-based distance, area, and hex-grid
operations. The selected NMD2023 Basskikt v2.1 is a 10 m EPSG:3006 raster, so
the primary land-cover backbone does not require an initial CRS conversion.
Source-specific CRS conversion, raster resampling, and precision decisions
will be documented during ingestion validation.

Step 4 validated the live NMD2023 Basskikt v2.1 delivery at the source
contract: one EPSG:3006, 10 m, unsigned-16 raster band with PackBits TIFF
compression. The source raster declares an all-valid GDAL mask; the supplied
VAT legend identifies code 0 as the no-data entry and code 62 as `Hav`/sea.
For the coverage gate, code 0 and sea pixels are excluded from the terrestrial
NMD data footprint, while the output raster preserves the original sea code.

The coverage metadata layer `NV_NMD2023_version_baskartering` was read from the
delivered GeoPackage. Its `Version` values were `Endast v0.x` and `v2.0 och
v0.x`; Skåne intersects the latter current-v2.x extent. Against the generated
SCB `study_area` artifact, the check found 11349.354 km² of terrestrial valid
NMD data and no uncovered terrestrial pixels. It found 5753.0955 km² of sea
and three code-0 no-data pixels inside the administrative/statistical extent;
these are not treated as missing terrestrial NMD coverage. The NMD v2.1
delivery is accepted as the primary Skåne raster source. The approved semantic
interpretation of those classes is defined below; it is a land-cover contract,
not a suitability or scoring model.

## NMD semantic contract (Step 5)

NMD supplies land-cover structure. It does not directly measure ecological
quality, habitat condition, biodiversity, ownership, restoration cost, actual
restoration feasibility, or conservation consent/legal availability. The
application therefore uses careful factual and analytical labels such as
candidate land, habitat-context proxy, wetland context, and artificial
constraint. A candidate pixel is a location for later investigation, not a
claim that restoration is appropriate or available.

The primary restoration-candidate definition for MVP v1 is exactly NMD class 3
(`Åkermark`, arable agricultural land). This is deliberately limited to
clearly human-managed land cover where conversion/restoration investigation is
conceptually plausible. Existing forest, wetlands, open vegetated land,
temporarily non-forest areas, artificial land, water, and sea are not primary
candidate land. Peat extraction (class 54, `Torvtäkt`) is tracked as a separate
factual group and is not silently added to the candidate mask.

The source-controlled factual groups are mutually exclusive:

| Factual group | NMD codes |
| --- | --- |
| `no_data` | 0 |
| `arable` | 3 |
| `artificial_building` | 51 |
| `artificial_other` | 52 |
| `artificial_transport` | 53 |
| `peat_extraction` | 54 |
| `inland_water` | 61 |
| `sea` | 62 |
| `established_forest_firm_ground` | 111–117 |
| `transitional_forest_firm_ground` | 118 |
| `established_forest_wetland` | 121–127 |
| `transitional_forest_wetland` | 128 |
| `open_wetland` | 200, 211–218, 221–228 |
| `open_nonvegetated` | 411 |
| `open_vegetated` | 4211–4213, 4221–4223, 4231–4233 |

The analytical roles are derived from those groups and can overlap:

- `primary_candidate`: exactly `arable` / code 3.
- `habitat_context_proxy`: established forest on firm ground, established
  forest on wetland, open wetland, and open vegetated land. This is a
  structural land-cover proxy, not a claim of semi-natural habitat, high
  quality, or biodiversity. In particular, NMD forest classes do not establish
  forest naturalness or management intensity.
- `wetland_context`: established forest on wetland, transitional forest on
  wetland, and open wetland. Class 128 is wetland context, but not established
  habitat context.
- `inland_water_context`: exactly class 61. Sea / marine class 62 is outside
  the MVP riparian role and current model.
- `artificial_constraint`: classes 51–53. Peat extraction remains separate.
- `transitional_forest`: classes 118 and 128 as a factual supporting role only;
  neither is assigned a favorable or unfavorable score.
- `terrestrial_land`: all valid land-cover groups except no-data, inland water,
  and sea. Artificial surfaces remain terrestrial land even though they are
  constraints.

No mutually exclusive analysis-class raster is created. Future operations can
derive the required masks directly from the compact categorical raster. Exact
spatial indicators, neighborhood definitions, connectivity, normalization,
weights, and scoring remain deferred.

Step 6 measures factual in-cell NMD composition before any later spatial
context scoring. NMD pixel centers are assigned to exactly one hexagon; the
`all_touched` rule is not used. `terrestrial_fraction` is terrestrial pixel
area divided by the complete hex area. Candidate and habitat-context
fractions use terrestrial pixels as their denominator, which preserves the
distinction between coastal/water composition and land composition. These
fractions are descriptive land-cover measurements, not eligibility scores.

## MVP candidate analysis-unit eligibility (Step 7)

The **primary candidate pixel** is exactly NMD class 3 arable land
(`Åkermark`). This is a pixel-level land-cover definition from the Step 5
semantic contract.

The **eligible candidate analysis unit** is a complete regular 500 m
flat-to-flat hexagon from the Step 6 factual grid satisfying both inclusive
conditions:

```text
candidate_area_m2 >= 50,000
candidate_fraction_of_terrestrial >= 0.25
```

Equivalently, the unit contains at least **5 ha of arable land**, and arable
land comprises at least **25% of its terrestrial NMD pixels**. This explicit,
pragmatic MVP rule defines the population to be ranked. It is a screening-domain
definition, not a suitability score, ecological minimum, scientifically
optimized threshold, restoration-feasibility claim, or claim that a retained
unit is actually restorable.

The rule has no additional eligibility conditions. In particular, no
terrestrial-coverage threshold is applied, and mixed habitat, wetland, inland
water, sea, or artificial context does not by itself exclude a hexagon. Those
factual composition fields are retained for later analysis. The complete
regular hex geometry is retained rather than clipped to arable pixels. Later
component indicators will differentiate candidate units based on surrounding
landscape context.

## Raw Habitat Context indicators (Step 8)

The Habitat Context component begins with two **raw indicators** for every
eligible candidate analysis unit. They use the Step 5 `habitat_context_proxy`
role exactly as defined above: established forest on firm ground, established
forest on wetland, open wetland, and open vegetated land. This is a structural
land-cover proxy. It does not measure biodiversity, habitat quality, ecological
condition, forest naturalness, species occurrence, legal protection, or
restoration success probability.

The candidate unit is the focal cell and is excluded from both surrounding
calculations. The immediate indicator aggregates habitat-context and
terrestrial NMD pixels over the six directly adjacent hex positions (hex
distance exactly 1). The local indicator aggregates the same quantities over
all 18 positions with `1 <= hex distance <= 2`: the six first-ring positions
and twelve second-ring positions. The local neighborhood is the regular
hex-grid convention, not a Euclidean circle or polygon buffer. The nominal
center separations are approximately 500 m for the first ring and
approximately 0.9–1.0 km for the second ring depending on direction; these are
analytical scales, not exact ecological influence distances.

For each indicator, the denominator is terrestrial NMD pixels in available
surrounding analysis-grid cells. Sea, inland water, and no-data are not
denominator land. An expected grid position absent from the durable analysis
grid is omitted rather than treated as zero terrestrial habitat. The output
also records how many terrestrial analysis-grid cells were present in each
neighborhood and preserves missing fractions if a neighborhood has zero
terrestrial pixels.

Using integer grid neighborhoods instead of arbitrary circular buffers makes
the calculation deterministic, aligned with the analytical tessellation, and
computationally simple. The full `analysis_units.gpkg` grid supplies context;
`candidate_units.gpkg` supplies only the focal population, so a noncandidate
surrounding cell can contribute habitat context.

These indicators remain **RAW**. No 0–100 normalization, component score,
weight, or overall restoration score is defined in Step 8. The current NMD and
analysis-unit artifacts stop at the Skåne study boundary, so context just
across the Halland or Blekinge county boundary is invisible. Step 8 measures
that potential study-boundary truncation with a diagnostic edge zone but does
not exclude or alter edge candidates and does not add cross-border data.

The study-area identifiers are county/län code **12** and NUTS 3 code
**SE224**. The reproducible boundary source is SCB DeSO 2025: select
`lanskod=12` from the anonymous WFS and dissolve the returned polygons.
The resulting artifact is an administrative/statistical study extent used to
locate Skåne, not a terrestrial land boundary: the DeSO 2025 revision is
complete to the territorial-water boundary and may include marine territory.
Marine areas, inland water, built land, and other unsuitable areas remain for
later NMD-based candidate-land logic and are not removed here.

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

Exact indicators for the remaining components and all later transformations
remain to be validated against their source data. This document intentionally
does not prescribe later formulas prematurely.

## Current assumptions and limitations

- The study area is Skåne län; its MVP boundary is derived from SCB DeSO 2025
  polygons selected by `lanskod=12` and dissolved.
- NMD2023 Basskikt v2.1 is the selected land-cover backbone; its complete
  terrestrial Skåne coverage gate passed during Step 4.
- A nominal 500 m hexagonal unit is a planning assumption, not a final
  immutable implementation detail.
- Candidate pixels are currently exactly NMD class 3 arable land. The
  habitat-context proxy, wetland context, and constraint roles are analytical
  land-cover masks, not ecological-quality or feasibility measurements.
- Feature-distance definitions, habitat/network indicators, treatment of
  missing data, normalization, weights, and scoring require later validation.
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
