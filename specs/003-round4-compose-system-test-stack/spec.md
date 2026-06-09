# Feature Specification: Round 4 Compose System-Test Stack Action

## User Stories & Testing

### User Story 1 - Run a caller-owned Compose stack (Priority: P1)

A repository maintainer can call the composite action with a list of compose
files, an optional Docker Compose project name, and an optional subset of
services to start for CI/system tests.

**Success Criteria**

- SC-001: The action no longer contains a design-first guard or placeholder
  acknowledgement input.
- SC-002: Newline-separated compose files become ordered `docker compose -f`
  arguments and are validated before startup.
- SC-003: `services` limits the `docker compose up` command to the requested
  service names; an empty value starts the selected compose stack.
- SC-004: `project-name` is optional and, when set, is passed as `-p`.
- SC-005: The action and fixtures do not copy reference-repo compose files,
  domains, hostnames, namespaces, queue names, IPs, image prefixes, or vendor
  URLs.

### User Story 2 - Wait for services or routes (Priority: P1)

A caller can choose whether stack readiness is handled by Docker Compose health
checks, a caller command, route polling, or no additional wait.

**Success Criteria**

- SC-006: Supported `wait-strategy` values are `compose-wait`, `services`,
  `command`, `routes`, and `none`.
- SC-007: `wait-strategy=command` requires and runs `wait-command`.
- SC-008: `wait-strategy=routes` requires `wait-routes-file` and polls each
  non-comment route until it succeeds or the per-route timeout expires.

### User Story 3 - Diagnose, migrate, and clean up (Priority: P2)

When stack startup, wait, or migration verification fails, the action dumps
generic diagnostics, optionally runs caller diagnostics, and then cleans up.

**Success Criteria**

- SC-009: Built-in failure diagnostics include compose service state, compose
  logs, Docker container state, and Docker disk usage.
- SC-010: `migration-check-command` runs after the configured wait strategy.
- SC-011: `cleanup-command` runs before `docker compose down`.
- SC-012: `down-on-complete=false` leaves teardown to the caller.

## Functional Requirements

- FR-001: The action shall start Docker Compose with caller-provided compose
  file paths and no app-specific defaults beyond generic file names.
- FR-002: The action shall pass selected services to `docker compose up` only
  when the `services` input is non-empty.
- FR-003: The action shall validate wait strategy, route wait timeout, route
  wait interval, and boolean cleanup inputs before startup.
- FR-004: The action shall support route wait files with blank lines, comments,
  `name url` entries, or single-URL entries.
- FR-005: The action shall run optional caller-owned wait, migration,
  diagnostics, and cleanup commands from `working-directory`.
- FR-006: The action shall run generic built-in diagnostics on failures after
  stack startup.
- FR-007: The action shall run `docker compose down --remove-orphans` on exit
  when `down-on-complete` is `true`.

## Constraints

- Do not modify `/workspace/personal-stack` or `/workspace/website`; they are
  read-only references.
- Do not copy consumer compose files, route lists, migration assertions, queue
  names, hostnames, IPs, domains, namespaces, Vault paths, or image prefixes.
- Keep the existing CI shape, including the terminal `Pipeline Complete` job and
  Python coverage gate.
- Avoid networked local verification; the external orchestrator runs full CI.

## Out of Scope

- Providing application-specific compose files, route inventories, bootstrap
  services, or migration SQL assertions.
- Running Docker or networked CI locally in this sandbox.
