# Methodology

Landscape Restoration Prioritizer is a regional screening and decision-support
tool for Skåne län, Sweden. It identifies candidate analysis units for further
investigation. It does not recommend a parcel, estimate restoration success, or
measure legal, economic, ownership, or implementation feasibility.

## Study area and analysis units

The study boundary is built from SCB DeSO 2025 polygons with `lanskod=12`,
dissolved in EPSG:3006. It is an administrative/statistical extent and may
include territorial water. It is not itself the terrestrial candidate mask.

The canonical analytical CRS is SWEREF 99 TM (EPSG:3006), the metre-based
Swedish projected CRS used for areas, nominal distances, and the hexagonal grid.
The NMD2023 Basskikt v2.1 raster is a 10 m EPSG:3006 categorical raster.

Analysis units are complete regular 500 m flat-to-flat pointy-top hexagons. Their
theoretical area is 216,506.3509 m² (21.6506351 ha), and their side length is
`500 / sqrt(3) = 288.6751346 m`. Hexagons are planning units, not parcels; their
boundaries have no ecological or legal meaning.

The grid is anchored at EPSG:3006 origin `(0, 0)`. For integer coordinates
`(col, row)`, the center is:

```text
x = (col + row / 2) * 500
y = row * 1.5 * 288.6751346
```

The stable identifier is `h_<col>_<row>`. The grid is generated from the
processed raster extent and retains cells containing at least one terrestrial
NMD pixel. Pixel centers are assigned to cells; `all_touched` rasterization is
not used. Cells are not clipped to the coast, study boundary, raster footprint,
or candidate pixels.

## NMD semantic roles

NMD classes are land-cover observations. The following factual groups are
mutually exclusive:

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

The analytical roles are derived from those groups and may overlap:

- `primary_candidate`: arable class 3 only.
- `habitat_context_proxy`: established forest on firm ground, established
  forest on wetland, open wetland, and open vegetated land.
- `wetland_context`: established forest on wetland, transitional forest on
  wetland, and open wetland.
- `inland_water_context`: class 61 only; sea is excluded.
- `artificial_constraint`: classes 51–53.
- `transitional_forest`: classes 118 and 128 as factual supporting fields.
- `terrestrial_land`: every valid group except no-data, inland water, and sea.

These roles are proxies for mapped context. They do not establish naturalness,
habitat quality, biodiversity, ownership, cost, or restoration feasibility.

## Candidate population

The candidate population consists of complete grid cells satisfying both
inclusive conditions:

```text
candidate_area_m2 >= 50,000
candidate_fraction_of_terrestrial >= 0.25
```

Candidate pixels are exactly NMD class 3 arable land. The rule is equivalent to
at least 5 ha of arable land and at least 25% arable land among the unit's
terrestrial NMD pixels. It defines the population to rank; it is not a
suitability threshold or a claim that a retained cell is restorable.

## Neighborhood conventions

The six directly adjacent cells are the first ring. The local neighborhood is
all 18 cells with hex distance 1 or 2. The focal cell is excluded from
surrounding indicators. Surrounding fractions aggregate pixel counts before
division, rather than averaging cell fractions.

For Habitat Context, Riparian Opportunity, and protected-terrestrial support,
missing or water-only cells contribute no denominator. For Ecological Network
Context, a missing first-ring position contributes habitat fraction zero and is
counted as missing. These conventions preserve the distinction between missing
terrestrial support and zero observed context.

Candidates near the dissolved study boundary retain a diagnostic
`boundary_edge_flag`. They remain in the population and are not penalized or
imputed with cross-county data.

## Model components

### Habitat Context

Habitat Context measures the amount of surrounding mapped habitat-context proxy.
For each candidate, the sole scored input is the pixel-weighted fraction of
habitat-context proxy among terrestrial pixels in the 18 surrounding cells:

```text
habitat_context_local_fraction =
    surrounding_habitat_context_pixels /
    surrounding_terrestrial_pixels
```

The first-ring fraction is retained as a supporting diagnostic. The scored
input is converted to a population-relative 0–100 score using ascending average
ranks among valid candidates:

```text
habitat_context_score = 100 * (average_rank - 1) / (N - 1)
```

The lowest value is 0, the highest is 100, and tied values receive the same
average-rank score.

### Ecological Network Context

Ecological Network Context measures opposing-side arrangement of the same
habitat-context proxy in the six first-ring cells. The three grid axes are:

| Axis | Opposite offsets |
| --- | --- |
| `axis_a` | `(-1, 0)` and `(1, 0)` |
| `axis_b` | `(0, -1)` and `(0, 1)` |
| `axis_c` | `(-1, 1)` and `(1, -1)` |

For each axis, the matched strength is the minimum of the two opposing habitat
fractions:

```text
m_a = min(a1, a2)
m_b = min(b1, b2)
m_c = min(c1, c2)
total_neighbor_habitat = a1 + a2 + b1 + b2 + c1 + c2
```

The scored input is the bounded configuration ratio:

```text
opposing_balance_ratio =
    2 * (m_a + m_b + m_c) / total_neighbor_habitat
```

When the total is zero, the ratio is zero. The component score is direct
bounded scaling:

```text
ecological_network_score = 100 * opposing_balance_ratio
```

This is a structural proxy imposed by the hex grid, not a species movement,
corridor, resistance, or habitat-quality model.

### Riparian Opportunity

Riparian Opportunity uses NMD wetland context and inland water. The hydrologic
numerator and non-marine denominator are:

```text
hydrologic_context_pixels = wetland_context_pixels + inland_water_pixels
nonmarine_context_pixels = terrestrial_land_pixels + inland_water_pixels
```

Wetland is already part of terrestrial land. Inland water is added to the
denominator; sea and no-data are excluded. The focal candidate fraction is the
sole scored input. Adjacent, near, and local fractions remain supporting fields.

Zero focal fraction receives zero. Positive candidates are ranked among the
positive population using ascending average ranks:

```text
riparian_opportunity_score = 100 * positive_average_rank / N_positive
```

The smallest positive observation is therefore above zero. This score is
relative mapped hydrologic context, not water quality, flood risk, stream order,
hydrological connectivity, or restoration suitability. The display layer is
generalized and is not a comprehensive stream network.

### Protected-Area Reinforcement

The protected network is the physical union of selected national parks, nature
reserves, and Natura 2000 SCI, SPA, or SPA/SCI polygons. A 10 m NMD pixel provides
protected-terrestrial support when its center lies inside that union and its
class belongs to `terrestrial_land`. The legal source geometry itself is not
clipped for this calculation.

The scored input is `nearest_protected_hex_steps`, the minimum axial grid-step
distance from a candidate to a grid position containing protected-terrestrial
support. Its `steps * 500 m` value is nominal and is not an exact polygon-edge
or Euclidean distance.

For candidate `i`, distance-zero candidates receive 100. Positive distances are
ranked among the non-overlap population using an ascending average rank:

```text
protected_area_reinforcement_score =
    100 * (N_positive - positive_average_distance_rank_i + 1) /
          (N_positive + 1)
```

Protected focal, adjacent, and local fractions remain supporting diagnostics and
are not combined with distance.

### Restoration Land Availability

Restoration Land Availability measures the relative amount of mapped eligible
arable land in each candidate cell. Its sole scored input is:

```text
candidate_land_area_ha = candidate_area_m2 / 10,000
```

The score uses ascending average ranks across all eligible candidates:

```text
restoration_land_availability_score = 100 * (average_rank - 1) / (N - 1)
```

Artificial-context fractions are supporting diagnostics only. This component has
no ownership, cadastral, socioeconomic, legal, cost, soil, drainage, yield, or
landowner-consent data.

## Overall score and presets

The overall score is a direct weighted arithmetic mean of the five 0–100
component scores:

```text
preset_score = sum(component_score * component_weight)
```

The three named presets are:

| Preset | Habitat | Network | Riparian | Protection | Availability |
| --- | ---: | ---: | ---: | ---: | ---: |
| Balanced | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 |
| Connectivity First | 0.20 | 0.30 | 0.10 | 0.25 | 0.15 |
| Riparian Restoration | 0.20 | 0.10 | 0.35 | 0.15 | 0.20 |

These are transparent scenario weightings, not learned coefficients or
ecological probabilities. The weighted mean is compensatory: a strong component
can offset a weak component.

The frontend's Custom mode accepts non-negative slider values from 0 to 100 and
normalizes them at calculation time. It changes client-side ranking of the
delivered component scores; it does not recalculate the analytical model.

## Reproducibility and limitations

The Python pipeline validates source schemas, CRS, raster resolution, geometry,
finite numeric values, identifier reconciliation, and deterministic ordering.
Generated artifacts are written under ignored `data/processed/` paths. The
canonical commands are:

```bash
python -m restoration_prioritizer.prioritization_model
python -m restoration_prioritizer.web_delivery
```

The model is limited by the source data and regional scale. NMD classes are
structural land-cover proxies; narrow streams may be missed; source dates,
resolutions, and completeness differ; and cross-county context beyond the
Skåne NMD extent is unavailable. The outputs support investigation and
comparison, not a formal restoration decision.
