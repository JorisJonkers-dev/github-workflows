# Implementation Plan: Round 2 Reusable Workflows

## Technical Approach

This repository already publishes reusable GitHub Actions workflows and composite actions. The round-2 candidates fit that surface, so the implementation adds reusable `workflow_call` files under `.github/workflows/` and keeps executable logic small.

The Flyway migration guard is implemented as `scripts/check_migrations.py` using only the Python standard library. A script is preferable to inline workflow shell because it is testable with fixtures, can expose configurable regex inputs, and can be covered by an 80% coverage gate.

CRaC training remains a reusable workflow template. It builds and runs consumer-provided training targets with configurable sidecars and checkpoint validation. The workflow owns the repeatable CI mechanics; consumers own their service matrix and training profile.

Production smoke/canary remains a reusable workflow template. It runs caller-provided commands for the real user journey, diagnostics, cleanup, and notification. This avoids extracting personal-stack endpoint paths, credentials, and mutation semantics.

## Files

- `.github/workflows/migration-guard.yml`: reusable Flyway migration guard.
- `.github/workflows/crac-train.yml`: reusable CRaC checkpoint training workflow.
- `.github/workflows/production-canary.yml`: reusable canary command runner.
- `scripts/check_migrations.py`: configurable migration validation CLI.
- `tests/test_check_migrations.py`: fixture-backed migration guard tests.
- `.github/workflows/ci.yml`: adds Python tests with `coverage --fail-under=80`.
- `README.md`: usage documentation for the new workflows.

## Requirement Mapping

- FR-001, FR-002, FR-003, FR-004: `scripts/check_migrations.py`.
- FR-005: `.github/workflows/migration-guard.yml`.
- FR-006, FR-007, FR-008: `.github/workflows/crac-train.yml`.
- FR-009, FR-010: `.github/workflows/production-canary.yml`.
- SC-001, SC-002, SC-003, SC-004: `tests/test_check_migrations.py`.
- SC-005, SC-006, SC-007: CRaC workflow inputs and matrix design.
- SC-008, SC-009, SC-010: canary workflow command and secret inputs.

## Deviations

The assignment mentions a branch named `impl/initial`, but this repo-specific task also requires `feat/round2`. The implementation uses `feat/round2` to match the assigned repository branch.

CRaC sidecars are always present in this initial reusable workflow because the extracted personal-stack pattern depends on Postgres, Valkey, and RabbitMQ. Optional sidecar topology is a future enhancement if another consumer needs a smaller set.

## Verification

- Run `python3 -m unittest discover -s tests`.
- Run `python3 -m coverage run -m unittest discover -s tests && python3 -m coverage report --fail-under=80`.
- Run the existing CI YAML/actionlint validation locally where tool availability permits.
