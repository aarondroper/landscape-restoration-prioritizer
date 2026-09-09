# Data-source decision registry

This is the authoritative source registry for the MVP research baseline. It
records source decisions, not ingestion code. No production environmental
datasets are stored in this repository.

## Minimal MVP source stack

| Role | Authority and exact product | MVP decision | Key access / reproducibility note |
| --- | --- | --- | --- |
| Study boundary | [SCB DeSO 2025](https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/demografiska-statistikomraden-deso/), WFS layer `stat:DeSO_2025` | **ADOPT** | Anonymous WFS; select `lanskod=12` and dissolve. Native/default CRS EPSG:3006; SCB open data is CC0. |
| Land-cover backbone | [Naturvårdsverket NMD2023 Basskikt v2.1](https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/ladda-ner-nationella-marktackedata/) | **ADOPT, subject to coverage gate** | Public national ZIP, 2.7 GB at verification; 10 m GeoTIFF, EPSG:3006, CC0. Skåne coverage was not geometrically verified from the large archive. |
| Protected areas | [Naturvårdsregistret `SkyddadeOmraden`](https://geodata.naturvardsverket.se/naturvardsregistret/wfs) plus [Natura 2000 `N2000`](https://geodata.naturvardsverket.se/n2000/wfs) | **ADOPT** | Anonymous WFS, polygon geometry, EPSG:3006. Filter the minimum defensible categories below; retain retrieval/update metadata. |

The stack is intentionally three source families. NMD supplies broad habitat,
land-cover, wetland, and inland-water context. A separate hydrographic line
source is deferred because the accessible alternatives either lack open stream
geometry or introduce disproportionate access and legal friction.

## Selected-source records

### NMD2023 Basskikt v2.1 — adopt

- **Authority/product:** Naturvårdsverket, Nationella marktäckedata 2023,
  Basskikt version 2.1. The current product description is [NMD2023
  Basskikt v2.1, edition 1.1 (2026-03-19)](https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/NMD2023_Produktbeskrivning_Basskikt_NMD2023_v2_1.pdf).
- **Role:** primary land-cover/habitat backbone for habitat context,
  ecological-network context, riparian context, and restoration-feasibility
  constraints.
- **Coverage and time:** the v2.x product is produced progressively; the
  documentation exposes a current extent metadata layer named
  `NV_NMD2023_version_baskartering`. The reference imagery includes 2023
  Sentinel-2 inputs, with other inputs dated in the product metadata. Do not
  infer complete Skåne coverage from the national download alone: coverage
  remains an ingestion-time acceptance check.
- **Raster and resolution:** 10 m raster; 54 thematic classes across four
  hierarchical levels; minimum mapping unit documented as 0.01 ha.
- **CRS and format:** EPSG:3006; unsigned 16-bit GeoTIFF with PackBits
  compression in the v2.1 national archive. The archive also contains
  metadata/FileGDB material.
- **Access:** [official download page](https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/ladda-ner-nationella-marktackedata/)
  and [v2.x download directory](https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/).
  The verified delivery is a national ZIP rather than a regional/tile
  service; local processing must therefore plan for a large archive.
- **License:** CC0 according to the product description.
- **Update behavior:** v2.0 was dated 2025-05-09 and v2.1 2026-03-19 in the
  product change history. Version and retrieval date must be recorded.
- **Relevant classes:** broad `Skogsmark`, `Öppen våtmark`, `Åkermark`,
  `Öppen fastmark`, `Anlagd och bebyggd mark`, and `Vatten` groups are
  sufficient for the MVP's first-pass masks and contextual summaries. The
  documented inland-water class is class 61. Class semantics and wetland
  caveats must be preserved during ingestion.
- **Quality/reproducibility caveats:** narrow streams can be missed or
  generalized at 10 m; v2.x completeness is not assumed; the product
  description itself contains a minor filename/metadata reference to v2.0 in
  places, so the archive name, PDF version, and retrieval metadata should be
  recorded together.
- **Decision:** **ADOPT** for the backbone, with a hard check that the
  selected v2.1 extent fully covers the dissolved Skåne boundary before any
  analysis proceeds.

### Protected areas — adopt two WFS layers, one physical network

- **Authority/product:** Naturvårdsverket, [Naturvårdsregistret open-data
  description](https://geodata.naturvardsverket.se/nedladdning/naturvardsregistret/Naturvardsregistret_beskrivning_av_oppna_data.pdf).
- **`SkyddadeOmraden`:** [WFS endpoint](https://geodata.naturvardsverket.se/naturvardsregistret/wfs),
  feature type `Naturvardsregistret_WFS:SkyddadeOmraden`. Use polygon
  features filtered to national parks and nature reserves, with
  `BESLUTSSTATUS=Gällande` where the status field supports that filter.
  Useful fields include `NVRID`, `NAMN`, `SKYDDSTYP`, status and decision
  dates, `LAN`, `KOMMUN`, and area fields.
- **`N2000`:** [WFS endpoint](https://geodata.naturvardsverket.se/n2000/wfs),
  feature type `N2000_WFS:N2000`. Include `OMRADESTYP` values `SCI`, `SPA`,
  and `SPA/SCI` (Habitats Directive, Birds Directive, or both). Useful
  fields include area, county/municipality, designation dates, and update
  date.
- **Geometry/CRS:** both services expose polygonal/multipolygon geometry and
  support EPSG:3006; the WFS capabilities advertise machine-readable output
  formats including GeoJSON/GeoPackage.
- **License/access/update:** the open-data description states CC0 and national
  coverage. Access is anonymous WFS. No fixed public refresh cadence was
  identified; feature status, effective dates, update fields, and retrieval
  timestamp must be retained.
- **Methodological caveat:** a site can be both Natura 2000 and a nature
  reserve/national park. The later protected-area geometry must be unioned or
  otherwise de-duplicated before physical-area measures; overlapping legal
  designations must not automatically count as twice the protected habitat.
- **Decision:** **ADOPT** these two layers as the smallest defensible
  protected-area scope for the protected-area reinforcement component.

### SCB DeSO 2025 — adopt for the boundary mask

- **Authority/product:** Statistics Sweden (SCB), [Demographic Statistical
  Areas DeSO 2025](https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/demografiska-statistikomraden-deso/).
- **Role:** reproducible study-area boundary source only; not an analytical
  environmental indicator.
- **Access:** anonymous WFS capabilities at
  `https://geodata.scb.se/geoserver/stat/wfs?service=wfs&version=1.1.0&request=GetCapabilities`;
  layer `stat:DeSO_2025`. Select `lanskod='12'` and dissolve the returned
  polygons. There is no direct county polygon layer in the inspected SCB WFS.
- **Geometry/CRS/version:** polygon geometry, default/native EPSG:3006;
  DeSO 2025 follows county and municipal boundaries as of 2025-01-01 and
  carries `lanskod`, `version`, and `referensdatum` attributes.
- **License/update:** SCB open data is CC0. The product is a dated boundary
  version; record the version/reference date rather than silently mixing it
  with another administrative vintage.
- **Decision:** **ADOPT** because it is anonymous, scriptable, geometrically
  adequate, and legally simple. Lantmäteriet's direct county product remains
  an optional alternative if its authorization requirement becomes acceptable.

## Optional or deferred sources

| Source | Potential role | Decision and reason |
| --- | --- | --- |
| [Jordbruksverket Ängs- och betesmarksinventeringen/TUVA](https://jordbruksverket.se/e-tjanster-databaser-och-appar/ovriga-e-tjanster-och-databaser/oppna-data/oppna-data/2026-01-02-angs--och-betesinventeringen) | Surveyed semi-natural meadow/pasture habitat, nature qualities, restoration field, and riparian field | **OPTIONAL / DEFER.** Strongly recommended as the first enrichment if the MVP needs a more explicit semi-natural-habitat anchor than NMD. The open WFS is scriptable and exposes useful fields, but exact product-level reuse terms and field semantics should be rechecked before ingestion. |
| [Lantmäteriet Hydrografi Nedladdning](https://geotorget.lantmateriet.se/geodataprodukter/hydrografi-nedladdning) | Detailed water bodies, watercourses, and networks | **REJECT FOR MVP.** Product access requires Geotorget authorization/account flow and legal approval; GML/API delivery and redistribution terms add friction. |
| [SMHI hydrografi/SVAR](https://www.smhi.se/data/sjoar-och-vattendrag/hydrografi) | Hydrological context and catchments | **REJECT AS A SEPARATE MVP HYDRO SOURCE.** Open WFS products cover catchments, but SMHI states that lake/stream geometries based on Lantmäteriet are view-only and not open WFS downloads. |
| [Lantmäteriet Markhöjdmodell Nedladdning](https://geotorget.lantmateriet.se/geodataprodukter/markhojdmodell-nedladdning-api) | Slope sub-indicator for feasibility | **OPTIONAL / DEFER.** A 1 m COG/STAC product is technically strong but requires authorization and substantial processing; incremental value at 500 m is not yet enough to justify it. |
| [Lantmäteriet Kommun, län och rike](https://www.lantmateriet.se/sv/geodata/vara-produkter/produktlista/kommun-lan-och-rike-nedladdning/) | Direct county polygon | **OPTIONAL / DEFER.** Authoritative and geometrically direct, but the inspected access path requires ordering/authorization; SCB is more reproducible for the MVP boundary. |
| NMD supplementary layers | Tree species, tillage, and other refinements | **REJECT FOR MVP unless a specific indicator needs one.** The basskikt already supports the five-component first pass; adding supplements would increase processing and interpretation burden without a demonstrated gap. |
| OpenStreetMap | Convenient stream lines | **REJECT FOR MVP.** It would weaken authority, consistency, licensing/provenance, and reproducibility relative to the official-source baseline. |

### Deferred-source access and reuse details

- **TUVA:** the open-data WFS entry point is
  `https://epub.sjv.se/inspire/opendata/wfs` (the agency also publishes an
  Inspire WFS at `https://epub.sjv.se/inspire/inspire/wfs`). The product page
  describes downloadable SWEREF 99 TM polygons and visits through 2023. The
  agency's general open-data terms support reuse, but the exact product-level
  license statement and field definitions should be captured before ingestion;
  this is why TUVA is optional rather than silently included.
- **Hydrografi Nedladdning:** the advertised machine interface is the
  Lantmäteriet ATOM API at `https://api.lantmateriet.se/hydrografi/atom/v1.1`.
  Access requires Geotorget authorization and the product has a separate
  [license document](https://www.lantmateriet.se/globalassets/geodata/geodataprodukter/licensvillkor-hydrografi-nedladdning.pdf);
  legal review and redistribution limits are material disadvantages.
- **SMHI/SVAR:** SMHI's [hydrografi page](https://www.smhi.se/data/sjoar-och-vattendrag/hydrografi)
  links the open WFS products, including catchment data, but explicitly
  distinguishes those from view-only Lantmäteriet-derived lake/stream
  geometry. SMHI's [reuse terms](https://www.smhi.se/data/om-smhis-data/villkor-for-anvandning)
  are CC BY 4.0 SE; this does not make the non-redistributable geometries an
  open vector source.
- **Markhöjdmodell:** the machine endpoint is the STAC root
  `https://api.lantmateriet.se/stac-hojd/v1`. The product is free and delivered
  as COG tiles with 1 m grid metadata, but access requires authorization/order
  flow. Its nominal detail is not sufficient reason to add it to this MVP.
- **Lantmäteriet county boundaries:** the product documentation describes
  current and annual county polygons and STAC-vektor/API delivery, but the
  inspected route requires authorization. No direct county layer was found in
  the anonymous SCB WFS, which is why SCB dissolution is the adopted route.

## Source-to-component mapping

This is a source-support map, not a scoring specification. It names possible
raw information only; normalization, thresholds, weights, and formulas remain
out of scope.

| Component | Selected source support | Possible raw information |
| --- | --- | --- |
| Habitat context | NMD2023; TUVA optional | Land-cover composition, open/forest/wetland context, and optional surveyed semi-natural habitat/nature-quality fields. |
| Ecological network context | NMD2023 plus adopted protected layers; TUVA optional | Habitat patch composition and spatial relationships to protected or surveyed habitat anchors. |
| Riparian opportunity | NMD2023 alone for MVP | Inland-water class, wetland classes, and wetland/forest context. Narrow stream geometry is a known weakness; no separate hydro layer is selected. |
| Protected-area reinforcement | `SkyddadeOmraden` + `N2000` | National parks, nature reserves, SCI, SPA, SPA/SCI, status and designation/update fields; later physical union to avoid overlap double-counting. |
| Land-restoration feasibility | NMD2023; terrain deferred | Agricultural/open/built/artificial/water/wetland/forest composition and exclusions. Slope is deferred. |

## Live verification record

The following lightweight checks were performed without downloading production
environmental data:

- NMD v2.x directory listing and the v2.1 product PDF were retrieved. The
  v2.1 ZIP advertised HTTP 200, byte ranges, and a 2,705,971,730-byte length;
  only archive metadata and small metadata material were inspected.
- Naturvårdsverket WFS `GetCapabilities` and `DescribeFeatureType` succeeded
  for both `SkyddadeOmraden` and `N2000`; EPSG:3006 and polygon geometry were
  confirmed. Small attribute/sample responses confirmed the protected and
  Natura type fields.
- SCB WFS `GetCapabilities` and `DescribeFeatureType` succeeded for
  `stat:DeSO_2025`; `lanskod`, version/reference-date fields, polygon geometry,
  and EPSG:3006 were confirmed.
- Lantmäteriet terrain STAC root metadata was reachable. No protected product
  data were requested.
- Jordbruksverket open-data WFS capabilities and schema were reachable; TUVA
  fields including `restaurering`, `fauna_kvaliteter`, `tradvarden`, and
  `vatten` were visible.
- SMHI's official hydrology documentation was checked for the distinction
  between open catchment WFS data and view-only Lantmäteriet-derived stream/
  lake geometries.

The exact geometry of the NMD v2.1 coverage layer was not fetched because it is
inside a large national archive. Complete Skåne coverage is therefore an
explicit unresolved acceptance check, not a claim made by this registry.
