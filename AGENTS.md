# Agents

Guidance for any coding agent working in this repository.

The repository's agent contract is [CLAUDE.md](CLAUDE.md). The estate-wide conventions (labels, traps) are in the [estate contract](https://github.com/JorisJonkers-dev/workspace/blob/main/CLAUDE.md); read both before non-trivial changes.

This repository holds the estate's reusable GitHub Actions workflows (`.github/workflows`) and composite actions (`actions`), with helper scripts in `scripts` and tests in `tests`. Consumers pin released tags. See `VERSIONING.md`.

## Build and test

- `python3 -m unittest discover -s tests` runs the Python tests (the CI migrations job runs this under coverage).
- CI lints the workflows with `actionlint .github/workflows/*.yml`.
