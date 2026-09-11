# Data sources

This document records the external datasets used by the application, their
roles, and the main interpretation and reuse constraints. Raw environmental
datasets and generated geospatial artifacts are kept outside Git.

## Current source stack

| Role | Source | Use in the application |
| --- | --- | --- |
| Study boundary | [SCB DeSO 2025](https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/demografiska-statistikomraden-deso/), WFS layer `stat:DeSO_2025` | Select `lanskod=12` and dissolve the polygons to define the Skåne study extent. |
| Land-cover backbone | [Naturvårdsverket NMD2023 Basskikt v2.1](https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/) | Provides the categorical 10 m land-cover pixels used for candidate land and contextual roles. |
| Protected areas | Naturvårdsverket [SkyddadeOmraden](https://geodata.naturvardsverket.se/naturvardsregistret/wfs) and [Natura 2000](https://geodata.naturvardsverket.se/n2000/wfs) WFS layers | Provides the formal protected-area network used by Protected-Area Reinforcement and its display layer. |

The sources are used in their native/projected Swedish CRS, EPSG:3006, where
possible. The analytical grid, areas, distances, and pixel assignments are all
calculated in that metre-based CRS. Browser delivery transforms display
geometry to EPSG:4326.

## Source details

### SCB DeSO 2025

The anonymous SCB WFS is a reproducible boundary source rather than an
environmental indicator. The ingestion selects `lanskod='12'`, requests
EPSG:3006 geometry, and dissolves the returned polygons. DeSO follows the
2025-01-01 boundary/reference vintage and may include territorial water; NMD
land-cover semantics are applied later when defining terrestrial analysis and
candidate areas. SCB open data is CC0.

### NMD2023 Basskikt v2.1

The [official product description and download directory](https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/)
describe a 10 m EPSG:3006 raster with 54 thematic classes. The product is the
land-cover backbone for habitat, network, riparian, and mapped land-availability
context. NMD is land-cover information: it does not directly measure habitat
quality, biodiversity, ownership, cost, consent, or restoration feasibility.

The ingestion validates the raster CRS, resolution, data type, archive members,
and coverage metadata before clipping the national product to the study area.
Code 0 is treated as no-data and code 62 as sea for terrestrial coverage checks;
the source class values are otherwise preserved. NMD open data is CC0.

### Protected areas

The protected network includes national parks and nature reserves from
`SkyddadeOmraden`, together with Natura 2000 sites of type `SCI`, `SPA`, or
`SPA/SCI`. The two source families are physically unioned before area-based
analysis so overlapping legal designations are not counted twice.

Source features intersecting a 5 km buffer around the study geometry are
retained to provide nearby context. This buffer does not expand the candidate
population or define a scoring threshold. The legal source footprint can include
marine territory. Analytical protected support is therefore derived from NMD
pixel centers that fall inside the union and have the `terrestrial_land` role.

The protected-area display is a terrestrial-semantic generalized derivative of
that union. It is context for map interpretation, not a parcel restriction map
and not a replacement for the legal source geometry.

### Wetland and inland water

The application uses NMD wetland roles and inland-water class 61 for Riparian
Opportunity. Sea, class 62, is excluded. The optional map layer shows the same
mapped wetland and inland-water context, with sea excluded.

This layer is not a stream or river network and should not be interpreted as a
comprehensive hydrology dataset. Narrow channels may be absent from the NMD
representation.

## Alternatives and known gaps

The source stack favors authoritative, scriptable data that is spatially
compatible with a 500 m screening unit and practical to reproduce publicly.

- TUVA is the strongest future enrichment for surveyed meadow/pasture and
  semi-natural habitat information, but its product-level field semantics and
  reuse terms should be confirmed before ingestion.
- Lantmäteriet Hydrografi Nedladdning would provide more detailed water geometry,
  but access and redistribution terms add authorization and legal constraints.
- Lantmäteriet terrain data could support slope, but its access and processing
  burden are not justified by the current model.
- SCB is used instead of a direct Lantmäteriet county product because the SCB
  WFS is anonymous and scriptable.
- OpenStreetMap is not used as a substitute for the official source baseline.

These choices leave known limitations: source dates and resolutions differ,
NMD broad classes are proxies rather than ecological-quality observations, and
cross-county context outside the Skåne raster is not imputed.

## Attribution and reuse

The repository contains derived scores, geometries, metadata, and processing
code; it does not redistribute the raw NMD archive, WFS downloads, or other
production environmental source files. Users reproducing the analysis should
consult the linked agency pages for current attribution and reuse terms.

The documented source facts currently include CC0 for SCB DeSO 2025 and NMD
open data. The Naturvårdsverket protected-area and Natura 2000 source pages are
the authoritative references for current source descriptions and reuse terms.
