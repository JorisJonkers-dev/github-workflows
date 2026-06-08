# Feature Specification: Round 2 Reusable Workflows

## User Stories & Testing

### User Story 1 - Guard Flyway migrations (Priority: P1)

A repository with Flyway SQL migrations can call a reusable workflow that detects edits to already-committed migrations and rejects newly added migrations whose version is not greater than the base branch's highest version for the same service.

**Success Criteria**

- SC-001: A modified, deleted, or renamed existing migration fails unless the configured override is active.
- SC-002: A new migration with a version lower than or equal to the base branch maximum for that service fails.
- SC-003: Duplicate migration versions within a service fail, including split migration directories.
- SC-004: A repository with no matching migrations succeeds.

### User Story 2 - Train CRaC checkpoints (Priority: P2)

A JVM service repository can call a reusable workflow that builds one or more Dockerfile training targets, runs each image with Postgres, Valkey, and RabbitMQ sidecars, validates the CRaC checkpoint directory, and uploads checkpoint artifacts.

**Success Criteria**

- SC-005: Consumers provide the service matrix, Dockerfile target, checkpoint path, sidecar images, and artifact retention through inputs.
- SC-006: The workflow accepts the expected CRaC exit-code set and fails on unexpected non-zero exits.
- SC-007: The workflow does not contain personal-stack service names, database names, hostnames, or domains.

### User Story 3 - Run production canaries (Priority: P3)

A repository can call a reusable production canary workflow that checks out the caller repository, optionally waits after a main-branch push, runs a caller-owned smoke command, runs diagnostics and cleanup on failure or completion, and optionally posts to a webhook.

**Success Criteria**

- SC-008: Consumers provide the canary command and any service-specific authentication, URLs, assertions, cleanup, and mutation behavior.
- SC-009: The workflow supports scheduled/manual/push callers without embedding schedule details in the shared workflow.
- SC-010: Notification text and webhook secret are supplied by the consumer.

## Functional Requirements

- FR-001: The migration guard shall accept a base ref, migration path regex, service scope regex, and override flag.
- FR-002: The migration guard shall report GitHub Actions error annotations for immutable migration and ordering violations.
- FR-003: The migration guard shall support Flyway-style versions such as `V1__x.sql` and `V1_2__x.sql`.
- FR-004: The migration guard shall group versions by a configurable service scope.
- FR-005: The migration guard workflow shall be callable by other repositories and shall resolve the matching `github-workflows` ref before running the guard implementation.
- FR-006: The CRaC workflow shall accept a JSON matrix with per-service Docker context, Dockerfile, image tag, database credentials, and application port.
- FR-007: The CRaC workflow shall run with privileged Docker and host networking because CRIU checkpointing requires host capabilities.
- FR-008: The CRaC workflow shall upload one checkpoint artifact per matrix service.
- FR-009: The canary workflow shall run a caller-provided command and optional cleanup, diagnostics, and notification commands.
- FR-010: The canary workflow shall avoid storing production URLs, usernames, secrets, or endpoint paths in this repository.

## Constraints

- The shared workflows must not require personal-stack service names, domains, queue names, or database names.
- The migration guard is the only implementation code in this change and must keep at least 80% test coverage.
- CRaC and production canary workflows are templates; service-specific behavior stays in consumers.

## Out of Scope

- Rewriting full monorepo CI.
- Owning production canary business logic.
- Publishing an artifact package.
- Supporting non-GitHub CI systems.
