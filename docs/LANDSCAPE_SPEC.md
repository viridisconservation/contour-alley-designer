# Landscape-general designer (v0.6): specification and invariants

Status: research software. Terrain, soils, land cover and climate normals are public data; every process rate, price, species value and shelter effect is a labeled assumption.

## Purpose

One designer for every working landscape in the partnership:

- **Hill cropland:** contour alley cropping. Rows are rain fences.
- **Plains cropland:** shelterbelt alley cropping. Rows run across the dominant wind and double as snow fences.
- **Hay and pasture:** silvopasture rows, on contour or across the wind.
- **Native grassland:** sparse savanna-style restoration under strict integrity rules.
- **Prairie strips:** where they help, on cropland.

The tree crops follow J. R. Smith's *Tree Crops* (1929): nuts and fruit for people, and feed crops (acorns, honey-locust pods, mulberries) that livestock harvest. For each field the designer searches the admissible design space and tests every finalist under dry, central and wet climate and with the windbreak effects switched off. It then reports whether the design meets the long-term success criteria, and how the field will show early whether it is on track.

## Invariants

| ID | Invariant | Where checked |
|---|---|---|
| LG1 | With every v0.6 feature off, kernel outputs equal the frozen v0.5 snapshot exactly. | `test_v050_golden` |
| LG2 | Water closes with snow: rain = Δ(soil water + snowpack) + ET + drainage + surface outflow + subsurface outflow + snow loss. Shelter can only reduce snow loss, never create water. | kernel record; `test_snow_balance_and_trap` |
| LG3 | When monthly normals are given, they replace the synthetic climate. Dry and wet draws scale precipitation, PET and temperature, and use the same random seed so designs are compared on the same weather (common random numbers). | `test_monthly_normals_drive_weather` |
| LG4 | Matrix types are annual crop, hay, pasture and rangeland. Rangeland is never fertilized. Sward-legume N is bounded by demand and suppressed by mineral N. Grazing returns of C and N stay inside the field ledger. | `validate`; `Matrix` tests |
| LG5 | Prairie strips are disjoint from tree rows and sit mid-alley, following the row geometry. They are never fertilized or harvested, and infiltrate like rows. | `Strips` tests |
| LG6 | Establishment mortality in years 1–3 rises with tree water stress. Dead stems become residue, so C and N are conserved. Survival is recorded each year, and replanting is costed. Default mortality is zero. | `Establishment` test |
| LG7 | The tree-crop portfolio uses per-species bearing ramps, drought response, prices and harvest costs. Tree-crop shares plus the fixer share never exceed the row. Species totals sum to the tree product. Every mix has at least two species, and a permanent nut/feed mix (trees ≥ 8 m) is offered wherever one is eligible, so early-bearing shrubs cannot crowd out Smith's permanent layer by default. The food/feed substitution credit is reported separately and never as the headline. | `Portfolio` tests |
| LG8 | Hills get contour rows. Plains get rows perpendicular to the dominant summer or winter wind, plus a parallel-to-wind control. Rolling ground searches both. No contour layout is attempted on a plain. | `test_plain_uses_windbreaks` |
| LG9 | Classification is deterministic and uses documented thresholds: smoothed grade at least 5% is hill, 2–5% is rolling, below 2% is plain; P/PET classes; snow months; hardiness proxy. | `test_classifier_thresholds` |
| LG10 | Every candidate passes the integrity rules before it is simulated. Finalists are ranked by their worst case across 3 climate draws × 2 effect modes. Candidates share all inputs with conventional practice A except their own design choices. | `optimize.py`; designer verification |
| LG11 | The success verdict is "confirmed (model)" only if S1–S6 all hold. Monitoring predictions are envelopes across every draw and effect mode, and the observation check is deterministic. | `success`; `test_observation_status` |
| LG12 | Tree crops must pass a hardiness proxy, an aridity floor and the regional invasive gate. Listed species need a written override; a species listed in any region and unassessed in this one is excluded as a precaution. Non-fixers never fill the fixer role. Native grassland admits native species only. | `test_invasive_and_role_gate`, `test_native_grassland_integrity` |
| LG13 | Coppice crops (native willows, red-osier dogwood, pole hazel) share the row with tree crops and fixers, and all shares sum to at most 1. They are cut only on rotation years in the dormant month. The cut is sold as biomass or fodder (product C leaves the field) or charred on the farm by the same pyrolysis ledger as the fixer prunings. Fodder can count toward the reported (non-headline) substitution credit. | `Coppice` tests |
| LG14 | Native strips come as a grass or a forb-rich pollinator mix, on cropland and on hay/pasture but never on native grassland. A pollinator understory can replace the sown legume cover in the rows. Pollination raises yields of insect-pollinated crops only, in proportion to each species' dependence and decaying with distance to habitat. It is an assumed effect, zero by default and in every control run. Each design reports pollinator habitat area and the share of the field within foraging range (150 m). Co-benefit tie rule: among carbon-positive finalists within 2% of the best worst-case score, the one with the most pollinator habitat is chosen, and the report prices the habitat as the worst-case score given up. | `Pollinators` tests |
| NG1 | On native grassland: tree cover is capped at 5%; there is no fertilizer, no sown cover and no tillage establishment; ecological review (remnant prairie, grassland birds) is required. A violating design is rejected, not penalized. | `check_design`; `test_native_grassland_integrity` |

## Long-term success criteria (model sense)

- **S1.** Reversal-adjusted, leakage-net removal is above zero at year 40 in every draw and effect mode.
- **S2.** Cumulative discounted cash vs A turns positive by year 25 (central climate, zero effects). When it fails, the report gives the upfront support (cost-share or pre-purchase) that would close the gap.
- **S3.** Establishment survival is at least 0.80 in every establishment year and draw, before replanting.
- **S4.** Surface outflow as a share of the water reaching the field is never more than 0.5 percentage points above A's (snow trapped by rows can add a little melt runoff while adding water overall).
- **S5.** There are no integrity violations, and the species pass the gate.
- **S6.** Water, carbon and nitrogen balances close in every run.

## Design search

Each candidate combines four choices:

- **Layout mode:** chosen by landscape class.
- **Alley width:**
  - contour: vertical interval / grade, for vertical intervals of 1, 2 and 3 m
  - windbreak: 6, 10 and 15 × mature height
  - native grassland: from the 2% and 4% tree-cover targets
  - every width is rounded to whole machinery passes, with at least 2 passes
- **Native habitat:** none, grass strips or pollinator strips (8% of the field), or a pollinator understory in the rows.
- **Tree-crop portfolio:** food_value, food_staged, nut_feed_permanent, feed_silvopasture, mixed_food_feed, coppice_and_fruit or coppice_biochar, built from the eligible species.

Tree fraction follows from geometry: row width / (alley + row).

The search runs in two stages:

1. **Screen** (15 years, central climate, assumed effects), staged:
   - (a) every admissible geometry (layout mode × alley width) with the first portfolio;
   - (b) the best geometry of each layout mode, plus the next best overall, under every portfolio;
   - (c) the two best of (b) with each admissible native habitat option added.
2. **Confirm.** The top three candidates, with at most two per layout mode, plus A run 40 years in 3 climate draws × 2 effect modes. They are ranked by their worst-case score.

Score = NPV vs A + carbon price × reversal-adjusted net removal. The default carbon price is $50/t, a weighting rather than a forecast.

## Known deviations and limits

- **Irrigation:** not modeled. Plains cropland results are dryland.
- **Hardiness:** zone = coldest monthly mean − 22 °C as an extreme-minimum proxy. Check against the USDA map before use.
- **Wind rose:** NOAA hourly normals give the modal direction per hour, which makes a coarse rose. Kalispell has no hourly normals, so Missoula serves as the Flathead proxy.
- **Snow:** a monthly degree-day model. Snow-trap gain is an assumed effect (γ = 0.6), and is zero in the control runs.
- **Rows:** one mean row height, set by the tallest species in the mix. There are no species-specific crowns, no root architecture and no light competition beyond the illustrative competition term.
- **Species values:** yields, prices, bearing ages and invasive statuses in `species-v2.json` are REVIEW REQUIRED.
- **Juglone:** walnut allelopathy is flagged but not modeled.
- **NLCD grassland:** NLCD "grassland/herbaceous" does not establish native status. That needs a field survey.
