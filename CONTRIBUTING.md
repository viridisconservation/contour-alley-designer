# Contributing

Issues and pull requests are welcome. These contributions help most:

- **Field measurements:** soil moisture, runoff, yield by distance from rows, tree growth and survival, soil carbon.
- **Windbreak shelter data:** wind speed by distance from the barrier, porosity, and row height.
- **Species information:** verified invasive and native status, and nodulation, for the species table.

Rules for code changes:
- Keep the standard-library-only core.
- Add a test for every invariant you touch.
- Never weaken a balance check.
- Run `python3 -m unittest discover -s tests` before opening a PR.
- Results must go to new output directories, and existing runs are never overwritten.
