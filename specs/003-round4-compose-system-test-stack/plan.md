# Implementation Plan: Round 4 Compose System-Test Stack Action

## Technical Approach

The Round 3 skeleton becomes a working composite action by delegating the
behavior to `actions/compose-system-test-stack/run.sh`. Keeping orchestration in
a shell runner makes the action YAML small enough for actionlint while allowing
the Python harness to execute the real control flow with stubbed `docker` and
`curl` commands.

The action remains generic. It accepts compose files and hook commands from the
consumer repository, starts the requested service subset, waits via Compose
health checks, a caller command, route polling, or no extra wait, then optionally
runs caller-owned migration checks. Failure handling is centralized with a Bash
`EXIT` trap so diagnostics and cleanup run for startup, wait, and migration
failures.

## Files

- `actions/compose-system-test-stack/action.yml`: production composite action
  inputs and runner invocation.
- `actions/compose-system-test-stack/run.sh`: compose argument assembly,
  validation, startup, wait strategies, diagnostics, migration checks, and
  cleanup.
- `actions/compose-system-test-stack/fixtures/compose.stack.example.yml`:
  generic compose fixture with variable-based images.
- `actions/compose-system-test-stack/fixtures/routes.example.txt`: generic
  route-wait fixture with caller-provided base URL placeholders.
- `tests/test_crac_train_workflow.py`: Python harness coverage for the action
  surface and runner behavior with local stubs.
- `README.md`: working action documentation.

## Requirement Mapping

- FR-001, FR-002: compose file parsing, project name handling, and service
  subset assembly in `run.sh`.
- FR-003: validation in `run.sh`.
- FR-004: route file parser and route polling in `run.sh`.
- FR-005: caller wait, migration, diagnostics, and cleanup command execution.
- FR-006: built-in diagnostics trap.
- FR-007: exit cleanup path.
- SC-001..SC-012: Python harness tests plus README/spec traceability.

## Verification

- Run `python3 -m unittest discover -s tests`.
- Run `python3 -m coverage run --source=scripts.check_migrations -m unittest discover -s tests`.
- Run `python3 -m coverage report --fail-under=80`.
- Run `actionlint .github/workflows/*.yml` and, when supported by the local
  binary, lint composite action files too.

## Deviations

Docker Compose itself is not executed locally because the sandbox blocks Docker
sockets. The runner is verified through direct Bash execution with stubbed
commands, and the external orchestrator runs the full CI environment.
