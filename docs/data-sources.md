# Data-source decision registry

This is the authoritative source registry for the MVP research baseline. It
records source decisions, not ingestion code. No production environmental
datasets are stored in this repository.

## Minimal MVP source stack

| Role | Authority and exact product | MVP decision | Key access / reproducibility note |
| --- | --- | --- | --- |
| Study boundary | [SCB DeSO 2025](https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/demografiska-statistikomraden-deso/), WFS layer `stat:DeSO_2025` | **ADOPT** | Anonymous WFS; select `lanskod=12` and dissolve. Native/default CRS EPSG:3006; SCB open data is CC0. |
| Land-cover backbone | [Naturvårdsverket NMD2023 Basskikt v2.1](https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/ladda-ner-nationella-marktackedata/) | **ADOPT — coverage gate passed** | Public national ZIP, 2,705,971,730 bytes at retrieval; 10 m GeoTIFF, EPSG:3006, CC0. Skåne terrestrial coverage was verified from the live archive and metadata layer. |
| Protected areas | [Naturvårdsregistret `SkyddadeOmraden`](https://geodata.naturvardsverket.se/naturvardsregistret/wfs) plus [Natura 2000 `N2000`](https://geodata.naturvardsverket.se/n2000/wfs) | **ADOPT** | Anonymous WFS, polygon geometry, EPSG:3006. Filter the minimum defensible categories below; retain retrieval/update metadata. |

The stack is intentionally three source families. NMD supplies broad habitat,
land-cover, wetland, and inland-water context. A separate hydrographic line
source is deferred because the accessible alternatives either lack open stream
geometry or introduce disproportionate access and legal friction.

## Selected-source records

### NMD2023 Basskikt v2.1 — adopt; coverage gate passed

- **Authority/product:** Naturvårdsverket, Nationella marktäckedata 2023,
  Basskikt version 2.1. The current product description is [NMD2023
  Basskikt v2.1, edition 1.1 (2026-03-19)](https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/NMD2023_Produktbeskrivning_Basskikt_NMD2023_v2_1.pdf).
- **Role:** primary land-cover/habitat backbone for habitat context,
  ecological-network context, riparian context, and restoration-feasibility
  constraints.
- **Coverage and time:** the v2.x product is produced progressively; the
  documentation exposes a current extent metadata layer named
  `NV_NMD2023_version_baskartering`. The reference imagery includes 2023
  Sentinel-2 inputs, with other inputs dated in the product metadata. Step 4
  explicitly checked the generated Skåne extent against the delivered raster
  and this metadata layer.
- **Raster and resolution:** 10 m raster; 54 thematic classes across four
  hierarchical levels; minimum mapping unit documented as 0.01 ha.
- **CRS and format:** EPSG:3006; unsigned 16-bit GeoTIFF with PackBits
  compression in the v2.1 national archive. The archive also contains
  metadata/FileGDB material.
- **Access:** [official download page](https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/ladda-ner-nationella-marktackedata/)
  and [v2.x download directory](https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/).
  The live v2.1 delivery used by Step 4 is
  `https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/NMD2023_basskikt_v2_1.zip`.
  It is a national ZIP rather than a regional/tile service. At retrieval it
  exposed `Content-Length: 2705971730`, `Last-Modified: Mon, 23 Mar 2026
  14:10:23 GMT`, and byte ranges (`206`, `bytes 0-0/2705971730`).
- **Observed delivery members:** the ZIP central directory contained 189
  members. The required raster is
  `NMD2023_basskikt_v2_1/NMD2023bas_v2_1.tif` (1,194,356,226 compressed /
  10,852,673,468 uncompressed bytes, ZIP Deflate; the TIFF itself is PackBits
  compressed). The preferred metadata member is
  `NMD2023_basskikt_v2_1/NMD2023_metadata_v2_0.gdb/NMD2023_metadata_v2_0.gpkg`
  (663,605,064 compressed / 2,701,787,136 uncompressed bytes, ZIP Deflate).
  The supplied code/name table is the small adjacent `.tif.vat.dbf` member.
  Duplicate FileGDB metadata members are not extracted.
- **Integrity:** no publisher checksum file or checksum field was found in the
  official v2.x directory or adjacent official search results. The local
  cached ZIP is 2,705,971,730 bytes with locally computed SHA-256
  `8326b6731a2a21d9181bc126ef20f3fc2ea7eb74ca287f122e3165e2b009c9bc`;
  the download was CRC-checked before use.
- **Implemented workflow:** `python -m restoration_prioritizer.nmd` streams
  to an atomic `.part` file, resumes an interrupted download when the server
  supports byte ranges, reuses a valid cache, extracts only the raster and
  GeoPackage, and removes the expanded national interim files after a
  successful run. The ignored processed output is a tiled DEFLATE GeoTIFF at
  `data/processed/nmd/nmd2023_v2_1_skane.tif`; factual run metadata is in the
  adjacent ignored provenance JSON.
- **Coverage acceptance:** the exact metadata layer used was
  `NV_NMD2023_version_baskartering`, a MultiPolygon in EPSG:3006 with fields
  `OBJECTID`, `Version`, and geometry. Its observed values were `Endast v0.x`
  and `v2.0 och v0.x`; Skåne intersects only the latter current-v2.x extent.
  Using the supplied raster legend, code 0 is no-data and code 62 is
  `Hav`/sea. The Skåne subset contained 11349.354 km² of terrestrial valid
  NMD data, 5753.0955 km² of sea, and 3 no-data pixels; 0 terrestrial valid
  pixels were outside current v2.x metadata coverage. NMD v2.1 is therefore
  accepted as the Skåne MVP land-cover source. The mixed `v2.0 och v0.x`
  metadata value and the bundled `v2_0` metadata filename are retained as
  observed delivery discrepancies rather than silently renamed.
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
  features where `SKYDDSTYP` is exactly `Nationalpark` or `Naturreservat`
  and `BESLUTSSTATUS` is exactly `Gällande`. The source `NVRID` and `NAMN`
  fields are retained as normalized `source_id` and `name`; the source
  designation and status are retained as `designation` and `source_status`.
- **`N2000`:** [WFS endpoint](https://geodata.naturvardsverket.se/n2000/wfs),
  feature type `N2000_WFS:N2000`. Include exactly `OMRADESTYP` values `SCI`,
  `SPA`, and `SPA/SCI` (Habitats Directive, Birds Directive, or both). The
  source `OMRADESKOD` and `OMRADESNAMN` fields are retained as normalized
  `source_id` and `name`. The source exposes designation dates and update
  dates, but no separate legal/current-status field; no status filter is
  invented.
- **Live service contract (verified 2026-09-09):** both services report WFS
  version `2.0.0`, target-layer default CRS `urn:ogc:def:crs:EPSG::3006`,
  and `DescribeFeatureType` geometry `gml:MultiSurfacePropertyType`. The
  target layers are present. The services expose GML/XML
  (`text/xml; subtype=gml/3.2`) rather than `application/json`; GeoJSON
  probes were rejected. WFS 2.0.0 BBOX requests use the server's EPSG:3006
  northing/easting axis order.
- **Access/filtering:** access is anonymous and uses Python standard-library
  HTTP GETs. Server-side BBOX filtering is used over the buffered study-area
  bounds. Live impossible-value CQL/FES attribute-filter probes were ignored
  by both services, so approved designation/status filters are applied and
  validated locally. A deterministic 2×2 BBOX tile split avoids unreliable
  paged responses, with 1 m overlap and authoritative-ID deduplication.
- **License/update:** the open-data description states CC0 and national
  coverage. No fixed public refresh cadence was identified; the service
  response timestamp and local UTC retrieval timestamp are recorded.
- **Local context:** features intersecting the SCB-derived Skåne study
  geometry buffered by exactly 5,000 m are retained. The buffer is source
  context for later proximity analysis only; it does not expand the candidate
  study area and is not a future score threshold.
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
  for both `SkyddadeOmraden` and `N2000`; WFS 2.0.0, EPSG:3006, polygon
  geometry, and the exact source fields were confirmed. Bounded samples
  confirmed national values `Nationalpark`, `Naturreservat`, and status
  `Gällande`, plus Natura values `SCI`, `SPA`, and `SPA/SCI`. The live
  services rejected GeoJSON and ignored CQL/FES attribute filters; GML/XML
  was therefore preserved and all semantic filters are local.
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

## Implemented study-area ingestion

The Step 3 source-specific command is documented in the README and can be run
with `python -m restoration_prioritizer.study_area`. It sends a WFS 2.0.0
`GetFeature` request to the SCB endpoint with `typeNames=stat:DeSO_2025`,
`srsName=EPSG:3006`, `outputFormat=application/json`, and the server-side
`CQL_FILTER=lanskod='12'`. The live service returned 819 matching features at
implementation time. The exact response is retained under ignored raw data;
the processed GeoPackage contains one dissolved feature and a concise
provenance manifest.

The dissolved DeSO geometry is deliberately retained as the administrative /
statistical study extent. It may include territorial water and is not the
terrestrial candidate-analysis mask; later NMD-based candidate-land logic will
handle marine, inland-water, built, and other unsuitable areas.

The live NMD v2.1 archive and its coverage layer have now been fetched and
checked by the Step 4 command. Complete terrestrial Skåne coverage passed the
acceptance gate; marine pixels and the three code-0 no-data pixels were not
treated as terrestrial coverage failures.
