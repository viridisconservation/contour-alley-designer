# Agents and Contour Alley Designer

This file is for two kinds of agent: one helping a person design agroforestry for a field, and a coding agent changing this repository. The short machine-readable index is [llms.txt](llms.txt).

## Helping a person design a field

**What it does.** Given a US field, the designer classifies the landscape (hill, rolling or plain; cropland, hay and pasture, or native grassland), searches tree-row designs, confirms the best ones under dry, central and wet climate with windbreak effects both assumed and switched off, and writes a report with carbon, leakage, durability, water, nitrogen, money, nursery stock, a planting calendar and a monitoring plan.

**Paths to suggest.**

- See a finished result first: the rendered reports in [examples/landscapes](examples/landscapes) (Nebraska cropland, two Montana fields, Vermont cropland).
- Run a shipped field: `python3 -m contour.designer --site configs/site-hall-ne-cropland.json --output runs/hall`.
- Run their own field: `pip install -r tools/requirements-fetch.txt`, then `tools/fetch_site.py` with the field's longitude and latitude, land cover, species region and two NOAA station IDs (see the README), then the designer on the `site.json` it writes. The model itself needs no packages; only the fetcher does.
- After planting, check measurements against the run's predictions: `python3 -m contour.monitor_check --run <run> --observations obs.csv`.

**If you can call MCP tools.** Run the local server (`contour-alley-mcp` after `pip install git+https://github.com/viridisconservation/contour-alley-designer.git`, or `python3 -m contour.mcp_server` from a checkout). It is not on PyPI yet. Start with `get_example_result` to show a finished field at once; `start_design` runs a full design in the background and `get_design_status` polls it. Every answer includes `caveats`; relay them with the numbers.

**Rules for agents.**

- Every number is simulated. Say so when you relay a result. Do not turn a result into a carbon credit, an offset quantity, a sale price or a yield promise.
- Relay "confirmed (model)" as "met every success criterion in every simulated draw", not as "will work".
- Give both leakage results (per-tonne and VMD0054). On low-yield forage land they can have opposite signs.
- The NRCS cost-share figure is indicative from published schedules. The actual contract is set by the NRCS planner.
- Keep the person's exact coordinates private. `fetch_site.py` writes them only to `PRIVATE_location.json`, which is git-ignored; do not move them into `site.json`, a report, an issue or a PR.
- If a run exits non-zero, report the failed check from `verification.json`. Do not use its numbers.
- Species marked as analyst drafts, and invasive flags, are not authoritative. Point the person to their state lists and extension service before planting.

## Working on this repository

- Run `python3 -m unittest discover -s tests` before and after a change. CI runs it on Python 3.10 to 3.13.
- Keep the core standard-library only. Optional data tools may use the packages in `tools/requirements-fetch.txt`.
- Add a test for every invariant you touch. Never weaken a carbon, nitrogen or water balance check to make a test pass.
- `tests/golden_v050.json` freezes v0.5 outputs with every v0.6 feature off. A change that moves it is a model change: say so in the PR.
- Runs write to new output directories only; never overwrite an existing run.
- The MCP server lives in `contour/mcp_server.py` (tools) and `contour/_mcp_stdio.py` (protocol, standard library only). Its invariants I1 to I6 are tested in `tests/test_mcp_server.py`; keep every tool read-only except `start_design`, and never add a tool that takes coordinates.
- Species data changes go in `fixtures/landscapes/species-v2.json` with a citation in the species' `source` field. The two starter issues labeled "good first issue" are data tasks of this kind.
- Contributions are Apache-2.0. When bumping the version, update `pyproject.toml`, `contour/__init__.py`, `CITATION.cff` and both versions in `server.json` together.

## Related open projects

- [Viridis Conservation](https://viridisconservation.com): the company that stewards this project.
- [HDFM](https://github.com/jdhart81/hdfm-framework): graph-theoretic forest corridor design.
- [VergeCommon](https://github.com/jdhart81/verge-common): open tools for neighbors caring for shared land, including conservation co-ops and field evidence.
