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

| Field | Class | Chosen design | Worst-case net carbon, 40 yr* | Under Verra VMD0054** | Verdict |
|---|---|---|---|---|---|
| Hall County, NE cropland | plain, subhumid | shelterbelt rows across the summer wind, 84 m alleys, 6% trees (chestnut, chokecherry, saskatoon), native pollinator understory | +26 tCO₂e | +6 | confirmed (model) |
| Flathead County, MT hay | rolling, semi-arid, snow | contour silvopasture, 36 m alleys, 12% trees (chokecherry, saskatoon, persimmon) | +52 tCO₂e | −112 | conditional: payback year 26 |
| Flathead County, MT grassland | hill, semi-arid, snow | native savanna hedges on contour, 4% cover | +30 tCO₂e | −24 | confirmed (model) |
| Franklin County, VT cropland | rolling, humid | contour alleys, 20 m, 20% trees (chestnut, chokecherry, saskatoon), native pollinator understory | +137 tCO₂e | +93 | confirmed (model) |

\*Reversal-adjusted and leakage-net vs conventional, over the worst of 3 climate draws × 2 effect modes, with the headline per-tonne leakage charge. "Confirmed (model)" means every success criterion held in every simulated draw. It is not a field guarantee.
\*\*Same worst case with leakage computed by Verra VMD0054 v1.1 defaults (one-time conversion of 75% × 40% of the displaced output at local yield, new land assumed forest at 90 tC/ha). Cross-commodity netting of the tree crops is not yet computed.

Parameters are grounded in a literature review ([docs/research/AGROFORESTRY_PARAMETER_GROUNDING.md](docs/research/AGROFORESTRY_PARAMETER_GROUNDING.md)). Woody carbon lands at about 0.6 (Nebraska) to 1.0 (Vermont) tC per ha of tree row per year, matching USDA COMET-Planner windbreak rates. Since v0.6.3, rows also return leaf and fine-root litter and decompose slower than tilled ground, so row soil gains about 0.2–0.25 tC per ha of row per year (conservative against the ~0.45 of Cardinael et al. 2018).

The two leakage rules disagree in an instructive way. The per-tonne charge hits productive cropland hardest. VMD0054 converts lost output to hectares at the local yield, so on low-yield forage land a small loss maps to a large area of assumed forest clearing: both Montana fields turn negative under it, while both cropland fields stay positive. Each report gives both, plus an indicative NRCS EQIP payment from the published state schedule.

At the assumed commodity prices, coppice crops lost to fruit and nut rows on every field. A native pollinator understory in the rows was nearly free (about $100 over 40 years on the Nebraska field in the worst case), and the designer chose it on both cropland fields. Separate strips cost crop area.

## Use it from an AI agent (MCP)

The package includes a local [Model Context Protocol](https://modelcontextprotocol.io) server, so Claude, Cursor or any MCP client can design and check fields directly. It uses the standard library only and runs on your machine.

It is not on PyPI yet. Install it from GitHub, which bundles the four shipped fields:

```sh
pip install git+https://github.com/viridisconservation/contour-alley-designer.git
contour-alley-mcp                       # stdio server; from a checkout: python3 -m contour.mcp_server
```

Client config (Claude Desktop, Cursor and others):

```json
{"mcpServers": {"contour-alley": {"command": "contour-alley-mcp"}}}
```

Tools:
- `describe_project`, `list_sites`: what the model does and which real fields ship with it.
- `get_example_result`: the finished result for a shipped field, at once.
- `start_design`, `get_design_status`: run a full design in the background (several minutes) and poll it.
- `check_observations`: compare field measurements with a run's predictions; writes nothing.

Every answer carries the source and the rules for relaying results, reports both leakage rules, and treats a run that failed verification as no result. No tool takes coordinates. Runs go to `~/.contour-alley/runs` (set `CONTOUR_ALLEY_RUNS` to change it).

<!-- mcp-name: io.github.viridisconservation/contour-alley-designer -->

## Use your own field

Any US field, from public data, in two commands:

```sh
pip install -r tools/requirements-fetch.txt          # numpy, pillow, pyproj (the model itself needs none)
python3 tools/fetch_site.py --name my-field --lon -98.507 --lat 40.899 --cover cropland \
    --region central_plains --normals-station USW00014935 --wind-station USW00014935 --out sites/my-field
python3 -m contour.designer --site sites/my-field/site.json --output runs/my-field
```

`fetch_site.py` pulls USGS 3DEP terrain, NLCD 2021 land cover, USDA SSURGO soils and NOAA 1991–2020 normals and hourly wind. It anonymizes the field and writes a designer-ready `site.json`. The exact location goes only to `PRIVATE_location.json`, which is git-ignored. Given the Franklin County coordinates, it rebuilt the shipped Vermont fixture to within one cell, about 1% on soil carbon and 0.1% on PET.

On Google Earth Engine, `earth_engine/export_field.py` (Python API, direct download) and `earth_engine/export_field_dem.js` (Code Editor) export terrain, NLCD and the AlphaEarth satellite embedding. On 2026-11-03 the Python export rebuilt all four shipped fields on the same 5 m grid: field cells within 0.6%, the same hill/rolling/plain class, and relative-elevation correlation 0.986-0.999 against the USGS/MRLC web-service fixtures (mean grade 1-10% lower, from 10 m source smoothing). `tools/geotiff_to_field.py` converts the exports into a field for the designer.

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

For AI agents and assistants: [llms.txt](llms.txt) is a short index, and [AGENTS.md](AGENTS.md) has the rules for relaying results and for changing the code.

## Try it from your agent

There is no hosted service; a coding agent runs the model locally (an MCP client can use the local server above instead). It needs Python 3.10+ and no packages:

```sh
git clone https://github.com/viridisconservation/contour-alley-designer.git
cd contour-alley-designer
python3 -m unittest discover -s tests
python3 -m contour.designer --site configs/site-hall-ne-cropland.json --output runs/hall
```

The tests take about 10 seconds. The designer run on the bundled Hall County, Nebraska field took about 17 minutes on one core and ends with:

```
[Hall County NE cropland (plain)] best X23 windbreak alley 84.0 m, food_value: confirmed (model) []
```

It writes `runs/hall/report.md` and `report.html`, `verification.json` (every balance check), `predictions.csv`, `monitoring_plan.json`, `leakage_vmd0054.json` and the other files the report draws on. Use a new `--output` directory for each run. The other bundled fields are `configs/site-flathead-mt-hay.json`, `configs/site-flathead-mt-grassland.json` and `configs/site-franklin-vt-cropland.json`, and finished reports for all four are in `examples/landscapes/`.

Prompts to paste into a coding agent (for example Claude Code) opened in this repository:

1. `Read AGENTS.md, then run python3 -m contour.designer --site configs/site-hall-ne-cropland.json --output runs/hall. Tell me the chosen design, its worst-case net carbon under both leakage rules, and whether every check in verification.json passed. Say that every number is simulated.`
2. `Using the reports in examples/landscapes, explain why the two Montana fields turn negative under Verra VMD0054 leakage while the two cropland fields stay positive. Give both leakage results for each field, and do not describe any number as a carbon credit or offset.`
3. `List the tree and shrub species the Hall County run chose, and for each say whether its values in fixtures/landscapes/species-v2.json are sourced or marked as an analyst draft, and whether it carries an invasive flag. Tell me what I should check with my state lists and extension service before planting.`

Limits to keep when relaying a result: this is research software. Results are modeled, not calibrated against field measurements. Terrain, soils, land cover and climate normals can be real, but process rates, prices and shelter effects are assumptions. Most species values are analyst drafts, and invasive flags record ecological concern, not state regulation. "Confirmed (model)" means every success criterion held in every simulated draw, not that the design will work in the field. If a run exits non-zero, treat it as no result.

## Citation

See `CITATION.cff`.
