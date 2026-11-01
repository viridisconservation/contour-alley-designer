# Model and invariants

The model, in brief:
- **Grid:** each active 5 m cell carries water, soil organic C, above/below-ground woody C, surface residue, living cover, biochar (on-site and imported, tracked separately), mineral N, organic N, plant N, residue N, cover N and biochar N.
- **Order within each month:** storm runoff is routed in elevation order (D8). Then each cell runs water, decomposition, crops, trees and fixation, cover, coppice and biochar, and N losses. Lateral residue and interflow are delivered at the end of the month.
- **Spec codes:** CA = contour layout and hydrology, NF = nitrogen fixation and fertilizer, CC = cover crop, BC = biochar, S3 = decomposition against A.

Tests live in `tests/test_contour.py`.

## Invariant → test map

| Spec | Implementation | Test |
|---|---|---|
| CA1 geometry, area closure, machinery multiple | `layout.py` | `Layout.*` |
| CA2 rain fence, water balance with run-on and interflow | `kernel.py` routing | `Balances.*` |
| CA3 zero-effect defaults | `params.py` effects | `Effects.test_zero_effect_default_CA3` |
| CA6 matched designs | `layout.layout` tree count, shared parameters | `Layout.test_equal_tree_area...`, `verification.json` |
| NF1 fixation suppressed by mineral N | kernel tree block | `Nitrogen.test_high_mineral_N...` |
| NF4 species gate | `species.py` | `Species.*` |
| NF5 demand-driven fertilizer | kernel | `Nitrogen.test_fertilizer...` |
| CC1 Ndfa partition | kernel cover block | `Nitrogen.test_only_atmospheric_share...` |
| BC2 pyrolysis mass balance | kernel coppice | `Biochar.test_pyrolysis...` |
| BC4 imported biochar excluded | kernel and params | `Biochar.test_imported...` |
| S3 decomposition reconciles | `accounting.py` | `Accounting.*` |
| C, N and water closure per cell and per field | kernel | `Balances.test_water_carbon_nitrogen_close...` |

## Design choices to review

1. **Pyrolysis carbon.** Under stock-change accounting the carbon lost in pyrolysis is already captured, because woody C drops and biochar C rises by less. So the loss is a ledger flux and is **not** also added as an emission, which avoids double counting. Only process energy is an emission.
2. **NF2 path.** Fixer N reaches alley crops only through litter fall, chop-and-drop prunings and mow-and-blow clippings delivered to adjacent alley cells, followed by decomposition. There is no root-to-root transfer.
3. **Interflow N.** Water moves downslope underground, but N in drainage is treated as leached. There is no lateral N in subsurface flow yet.
4. **Simplifications.**
   - One mean row height per month.
   - Isotropic competition, with no sun-angle shading yet.
   - Monthly storm partition rather than event hydrology.
   - No snowmelt.
5. **True-contour alleys vary in width.** C_T is reported for comparison. C (parallel) is the machinery-feasible design.


## Parameters

Every parameter is exported with its status in each run (`parameters_*.json`):
- **Default:** analyst assumption, not calibrated.
- **IPCC 2019 Refinement Tier 1 factors and AR6 GWP100:** labeled as such; verify before use.
- **Interaction effects** (shelter on ET and yield, row competition): zero by default. The experiment also runs a labeled "assumed-effects" mode with illustrative values from the config.

## Baseline soil carbon

With measured initial SOC, set `soil.init_equilibrium: true`. This derives the decay rate that holds conventional cropping at steady state (RothC-style initialization), so scenario differences are not swamped by an artificial baseline drift.
