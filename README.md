# Contour Alley Designer

An open, reproducible field-scale model for designing **agroforestry on every working landscape**:
- **Hills:** contour alley cropping, where tree rows act as rain fences.
- **Plains:** shelterbelt alley cropping, with rows across the dominant wind that double as snow fences.
- **Hay and pasture:** silvopasture.
- **Native grassland:** sparse savanna restoration under strict integrity rules.

Every design can add a legume or native pollinator understory, nitrogen-fixing shrubs, coppice crops (native willows, red-osier dogwood, pole hazel), on-farm biochar, and native grass or pollinator strips. The tree crops follow J. R. Smith's *Tree Crops* (1929): nuts and fruit for people, and feed crops for livestock.

For each field the designer classifies the landscape, searches the admissible designs and confirms the finalists under dry, central and wet climate, with windbreak effects both assumed and switched off. It then reports whether the design meets long-term success criteria and writes a monitoring plan that tests that claim in the field.

From [Viridis Conservation](https://viridisconservation.com). Apache-2.0.

> **Status: research software (v0.6.0-dev).** Terrain, soils and land cover can be real; process rates, prices and shelter effects are labeled assumptions. Nothing here is a calibrated forecast for a real farm or a verified carbon removal.

## Why

Nature-based carbon removal in farm landscapes hinges on three questions:
- how much production the trees displace (**leakage**)
- whether the gain would have happened anyway (**additionality**)
- how long the carbon stays (**durability**)

Tree-row geometry, land marginality and management decide all three. This tool makes those trade-offs explicit and testable on real terrain.

## What it does

- **Lays out matched designs.** Every design plants the same tree area with identical inputs, so geometry is the only difference:
  - conventional crop
  - straight rows, at any bearing
  - parallel-contour rows (constant alley width, machinery-feasible)
  - true-contour rows
  - a land-sparing tree block
- **Simulates 40 years monthly on 5 m cells.** It covers:
  - storm runoff routed downslope and caught by rows (rain fences), plus subsurface interflow
  - windbreak shelter and row competition that scale with growing tree height and barrier porosity (zero by default; enabled in a labeled sensitivity run)
  - a legume cover in the rows, where only the atmospheric share of its nitrogen counts as new N
  - nitrogen-fixing trees, suppressed by soil mineral N and costing carbon
  - coppiced prunings to chop-and-drop mulch or on-farm biochar
  - demand-driven fertilizer with manufacturing and IPCC Tier 1 N₂O emissions
  - SOC turnover with coupled N release
- **Accounts against conventional cropping.** It reports gross and net carbon, commodity leakage, reversal-adjusted carbon by pool, a component decomposition, NPV, fertilizer avoided and runoff change. Imported biochar is excluded from removals by default.
- **Checks itself.** Carbon, nitrogen and water balances close per cell and per field. Every run writes `verification.json` and exits non-zero if any invariant fails.

Standard-library Python 3.10+, with no dependencies for the model; the optional public-data fetcher uses numpy, pillow and pyproj.

## Quick start

```sh
python3 -m unittest discover -s tests
python3 -m contour.experiment --config configs/contour-synthetic-v1.json --output runs/gentle
open runs/gentle/report.html
```

Configs included:
- `configs/contour-synthetic-v1.json`: gentle synthetic hillslope (3.2 ha, about 1.5 minutes).
- `configs/contour-synthetic-spur-v1.json`: spur-and-hollow synthetic hillslope.
- `configs/contour-franklin-vt-pilot-v1.json`: a real 9.9 ha cultivated field in Franklin County, Vermont. It uses USGS 3DEP lidar terrain, NLCD 2021 land cover and USDA SSURGO soils, with coordinates anonymized. It runs in about 10 minutes. A rendered report is in `examples/franklin-vt-pilot/`.

## Design any landscape (v0.6)

```sh
python3 -m contour.designer --site configs/site-hall-ne-cropland.json --output runs/hall
python3 -m contour.monitor_check --run runs/hall --observations my_observations.csv
```

The pipeline runs field → classify → screen → robust confirmation → success test → implementation plan and monitoring. It writes a report, nursery stock counts and a planting calendar, stratified sampling points, and yearly prediction envelopes (`predictions.csv`). Field observations are checked against those envelopes as on_track, watch or off_track. The specification and invariants are in [docs/LANDSCAPE_SPEC.md](docs/LANDSCAPE_SPEC.md).

Four real fields are included, each about 10–13 ha. They use USGS 3DEP lidar, NLCD 2021, USDA SSURGO, NOAA 1991–2020 normals and NOAA hourly wind, with locations anonymized to county. Rendered reports are in `examples/landscapes/`.

| Field | Class | Chosen design | Worst-case net carbon, 40 yr* | Verdict |
|---|---|---|---|---|
| Hall County, NE cropland | plain, subhumid | shelterbelt rows across the NW winter wind, 84 m alleys, 6% trees (chestnut, chokecherry, saskatoon) | −5 tCO₂e | conditional: carbon ≈ leakage charge |
| Flathead County, MT hay | rolling, semi-arid, snow | contour silvopasture, 36 m alleys, 12% trees | +24 tCO₂e | conditional: payback year 27 |
| Flathead County, MT grassland | hill, semi-arid, snow | native savanna hedges on contour, 4% cover | +15 tCO₂e | confirmed (model) |
| Franklin County, VT cropland | rolling, humid | contour alleys, 20 m, 20% trees (chestnut, chokecherry, saskatoon), native pollinator understory | +9 tCO₂e | confirmed (model) |

\*Reversal-adjusted and leakage-net vs conventional, over the worst of 3 climate draws × 2 effect modes. "Confirmed (model)" means every success criterion held in every simulated draw. It is not a field guarantee.

Parameters are grounded in a literature review ([docs/research/AGROFORESTRY_PARAMETER_GROUNDING.md](docs/research/AGROFORESTRY_PARAMETER_GROUNDING.md)). Woody carbon lands at about 0.7 (Nebraska) to 1.0 (Vermont) tC per ha of tree row per year, matching USDA COMET-Planner windbreak rates. On cropland, the charge for displaced crop (leakage) cancels most of the gross gain. Nebraska's result swings from +71 to −121 tCO₂e across the defensible leakage range (0.2–2.4 tCO₂e per t grain), so leakage, not tree growth, decides the climate case there.

At the assumed commodity prices, coppice crops lost to fruit and nut rows on every field. A native pollinator understory in the rows was nearly free: on the Nebraska field it cost about $460 over 40 years in the worst case. Separate strips cost crop area.

## Use your own field

Any US field, from public data, in two commands:

```sh
pip install -r tools/requirements-fetch.txt          # numpy, pillow, pyproj (the model itself needs none)
python3 tools/fetch_site.py --name my-field --lon -98.507 --lat 40.899 --cover cropland \
    --region central_plains --normals-station USW00014935 --wind-station USW00014935 --out sites/my-field
python3 -m contour.designer --site sites/my-field/site.json --output runs/my-field
```

`fetch_site.py` pulls USGS 3DEP terrain, NLCD 2021 land cover, USDA SSURGO soils and NOAA 1991–2020 normals and hourly wind. It anonymizes the field and writes a designer-ready `site.json`. The exact location goes only to `PRIVATE_location.json`, which is git-ignored. Given the Franklin County coordinates, it rebuilt the shipped Vermont fixture to within one cell, about 1% on soil carbon and 0.1% on PET.

On Google Earth Engine, `earth_engine/export_field_dem.js` exports terrain, NLCD and the AlphaEarth satellite embedding. Its dataset IDs were checked against the catalog on 2026-11-02, but the script has not yet been executed. `tools/geotiff_to_field.py` converts its exports into a field for the designer.

## Pilot results (Franklin County field, 40 years; simulated)

- **Water.** Any tree-row layout cut runoff leaving the field by about 60%. Contour rows kept the most water in the alleys in July (about 20 mm vs 9 mm under conventional cropping).
- **Leakage.** Crop output displaced by the trees cancelled about two-thirds of the gross carbon gain.
- **Durability.** Durability risk removed a further ~70% of net carbon. Turning prunings into biochar was the main lever for carbon that lasts.
- **Geometry.** Row geometry moved net carbon by only about 10 tCO₂e. On this field, leakage and durability decide the climate case, not layout.

See [docs/MODEL.md](docs/MODEL.md) for the model, invariants, deviations and limits.

## Limits

- monthly bucket hydrology with a degree-day snowpack; no event hydraulics, groundwater or irrigation
- illustrative shelter and competition curves
- one mean row height
- Tier 1 N₂O factors applied to modeled flows
- species tables: yields and prices for chestnut, elderberry, saskatoon, hazelnut, black walnut and shrub willow are taken from extension budgets (sources in `fixtures/landscapes/species-v2.json`); other values and the invasive flags (ecological concern, not state regulation) remain drafts
- no calibration against field measurements yet

Contributions of field data, calibration datasets and windbreak measurements are especially welcome.

## Citation

See `CITATION.cff`.
