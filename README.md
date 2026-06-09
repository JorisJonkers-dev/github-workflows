# github-workflows

Reusable ExtraToast composite actions and workflows live here. Consumer
repositories should call them with immutable release tags, not branches.
Renovate keeps those pins current.

## Composite actions

### `prepare-ci-host`

Downloads Docker image artifacts, loads the image tarballs, and optionally runs
a repository-provided dev DNS setup script.

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ExtraToast/github-workflows/actions/prepare-ci-host@v0.2.0
    with:
      artifact-pattern: image-*
      artifact-path: /tmp/images
      image-name-pattern: image-*
      image-tar-pattern: '*.tar'
      configure-dev-dns: 'true'
      dev-dns-script-path: infra/scripts/setup-dev-dns.sh
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `artifact-pattern` | `image-*` | Artifact name pattern passed to `actions/download-artifact`. |
| `artifact-path` | `/tmp/images` | Directory where artifacts are downloaded. |
| `image-name-pattern` | `image-*` | Directory pattern below `artifact-path` that contains tarballs. |
| `image-tar-pattern` | `*.tar` | File pattern for Docker image tarballs. |
| `configure-dev-dns` | `true` | Runs the dev DNS script when set to `true`. |
| `dev-dns-script-path` | `infra/scripts/setup-dev-dns.sh` | Script path in the checked-out repository. |

### `setup-java-gradle`

Installs Java and configures Gradle dependency caching.

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ExtraToast/github-workflows/actions/setup-java-gradle@v0.2.0
    with:
      java-version: '21'
      java-distribution: temurin
      github-packages-token: ${{ secrets.GITHUB_TOKEN }}
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `java-version` | `21` | Java version installed by `actions/setup-java`. |
| `java-distribution` | `temurin` | Java distribution installed by `actions/setup-java`. |
| `gradle-cache-disabled` | `false` | Disables Gradle caching when set to `true`. |
| `gradle-cache-read-only` | `false` | Restores Gradle cache entries without saving updates when set to `true`. |
| `github-packages-actor` | `github.actor` | GitHub Packages actor exported as `GITHUB_ACTOR` when `github-packages-token` is set. |
| `github-packages-token` | empty | GitHub Packages token exported as `GITHUB_TOKEN` for Gradle package resolution. |

### `setup-node`

Installs Node, configures package-manager caching, and installs dependencies.
The package manager may be `pnpm` or Yarn Berry.

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ExtraToast/github-workflows/actions/setup-node@v0.2.0
    with:
      node-version: '24'
      package-manager: pnpm
      cache-dependency-path: pnpm-lock.yaml
      github-packages-token: ${{ secrets.GITHUB_TOKEN }}
```

Yarn example:

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: ExtraToast/github-workflows/actions/setup-node@v0.2.0
    with:
      node-version: '24'
      package-manager: yarn
      cache-dependency-path: yarn.lock
      github-packages-token: ${{ secrets.GITHUB_TOKEN }}
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `node-version` | `24` | Node.js version installed by `actions/setup-node`. |
| `package-manager` | `pnpm` | Package manager to configure. Supported values: `pnpm`, `yarn`. |
| `cache-dependency-path` | empty | Lockfile path or paths used for dependency caching. |
| `working-directory` | `.` | Directory where dependencies are installed. |
| `install-command` | empty | Overrides the install command. Empty uses `pnpm install --frozen-lockfile` or `yarn install --immutable`. |
| `github-packages-token` | empty | GitHub Packages token used to write project `.npmrc` auth and export `NODE_AUTH_TOKEN`. |
| `npm-scope` | `@extratoast` | npm scope resolved from GitHub Packages when `github-packages-token` is set. |
| `github-packages-registry` | `https://npm.pkg.github.com` | npm registry URL used for the configured GitHub Packages scope. |

### `compose-system-test-stack`

Runs a caller-owned Docker Compose CI/system-test stack, waits for service
health or routes, runs optional migration checks, dumps diagnostics on failure,
and tears the stack down by default. Compose files, service names, route URLs,
migration assertions, and hook commands stay in the consumer repository.

```yaml
steps:
  - uses: actions/checkout@v6
  - uses: ExtraToast/github-workflows/actions/compose-system-test-stack@v0.4.0
    with:
      compose-files: |-
        docker-compose.yml
        docker-compose.ci.yml
      services: api frontend db
      wait-strategy: routes
      wait-routes-file: .github/system-test-routes.txt
      diagnostics-command: .github/scripts/dump-compose-diagnostics.sh
      migration-check-command: .github/scripts/verify-migrations.sh
```

Route wait files contain blank lines, comments, or either `name url` or `url`
entries. Generate the file in the caller repository when URLs depend on
environment-specific hostnames or ports.

The action tears the stack down before the next workflow step unless
`down-on-complete` is set to `false`. For tests that run in later steps, leave
the stack up and provide separate caller-owned diagnostics and cleanup.

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `compose-files` | `docker-compose.yml`, `docker-compose.ci.yml` | Newline-separated compose files owned by the caller repository. |
| `services` | empty | Space-separated services to pass to `docker compose up`; empty starts the selected stack. |
| `project-name` | empty | Optional Docker Compose project name. |
| `working-directory` | `.` | Directory containing compose files and hook scripts. |
| `wait-strategy` | `compose-wait` | Wait mode: `compose-wait`, `services`, `command`, `routes`, or `none`. |
| `wait-command` | empty | Caller-owned command for `wait-strategy=command`. |
| `wait-routes-file` | empty | Caller-owned route list for `wait-strategy=routes`. |
| `wait-timeout-seconds` | `300` | Timeout per route when `wait-strategy=routes`. |
| `wait-interval-seconds` | `2` | Poll interval per route when `wait-strategy=routes`. |
| `migration-check-command` | empty | Optional caller-owned migration verification command. |
| `diagnostics-command` | empty | Optional caller-owned diagnostics command for failures. Built-in `ps`, logs, and Docker disk diagnostics run first. |
| `cleanup-command` | empty | Optional caller-owned cleanup command run before `docker compose down`. |
| `up-args` | `--no-build --wait --timeout 300 -d` | Extra `docker compose up` arguments. |
| `down-on-complete` | `true` | Whether to run `docker compose down --remove-orphans` after the stack step completes. |

## Reusable workflows

### `migration-guard.yml`

Checks Flyway-style SQL migrations for two failure modes: existing migrations
changed after being committed, and newly added migrations whose version is not
greater than the base branch maximum for the same service.

```yaml
jobs:
  migration-guard:
    uses: ExtraToast/github-workflows/.github/workflows/migration-guard.yml@v0.3.0
    with:
      migration-regex: 'services/[^/]+/src/main/resources/db/migration(-pg)?/V[0-9][^/]*\.sql$'
      scope-regex: '(services/[^/]+)/.*'
      override-label: allow-migration-change
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `base-ref` | inferred PR base or default branch | Git ref used as the immutable migration baseline. |
| `migration-regex` | `services/.../db/migration(-pg)?/V*.sql` | Regex matching migration files. |
| `scope-regex` | `(services/[^/]+)/.*` | Regex whose first capture group defines the version namespace. |
| `override-label` | `allow-migration-change` | PR label that downgrades existing migration changes to warnings. |

### `crac-train.yml`

Builds CRaC training Docker targets, runs them with per-service optional
Postgres, Valkey, and RabbitMQ sidecars, validates that a checkpoint was
produced, and uploads one checkpoint artifact per service. Matrix rows that do
not declare `sidecars` keep the original round-2 behavior and start all three
sidecars.

```yaml
jobs:
  crac-train:
    uses: ExtraToast/github-workflows/.github/workflows/crac-train.yml@v0.3.0
    with:
      service-matrix: >-
        [
          {
            "service": "example-api",
            "context": ".",
            "dockerfile": "services/example-api/Dockerfile",
            "db_name": "example_db",
            "db_user": "example_user",
            "db_password": "example_password",
            "port": 8080,
            "sidecars": ["postgres", "valkey"]
          },
          {
            "service": "worker",
            "context": ".",
            "dockerfile": "services/worker/Dockerfile",
            "sidecars": "none"
          }
        ]
      docker-target: train
      checkpoint-path: /opt/crac/checkpoint
      expected-exit-codes: '0 137'
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `service-matrix` | required | JSON matrix include list for services to train. |
| `docker-target` | `train` | Dockerfile target used for training images. |
| `checkpoint-path` | `/opt/crac/checkpoint` | Container path where CRaC writes checkpoint files. |
| `expected-exit-codes` | `0 137` | Space-separated accepted container exit codes. |
| `artifact-retention-days` | `7` | Checkpoint artifact retention period. |
| `postgres-image` | `postgres:17-alpine` | Postgres sidecar image. |
| `valkey-image` | `valkey/valkey:7-alpine` | Valkey sidecar image. |
| `rabbitmq-image` | `rabbitmq:3-management-alpine` | RabbitMQ sidecar image. |
| `extra-docker-run-args` | empty | Extra arguments appended before the training image tag. |

Matrix fields:

| Name | Default | Purpose |
| --- | --- | --- |
| `service` | required | Service name used for the local image tag and checkpoint artifact prefix. |
| `context` | `.` | Docker build context. |
| `dockerfile` | required | Dockerfile path in the caller repository. |
| `db_name` | `service` | Postgres database name when the Postgres sidecar is enabled. |
| `db_user` | `postgres` | Postgres username when the Postgres sidecar is enabled. |
| `db_password` | `postgres` | Postgres password when the Postgres sidecar is enabled. |
| `port` | empty | Optional `SERVER_PORT` passed to the training container. |
| `sidecars` | all three | String or list containing `postgres`, `valkey`, `rabbitmq`, or `none`. Missing keeps the backward-compatible full topology; `[]`, `none`, or `["none"]` starts no sidecars. |

### `production-canary.yml`

Runs a caller-owned production smoke command with optional post-push delay,
diagnostics, cleanup, and webhook notification. Service-specific URLs, auth,
assertions, and mutation behavior stay in the caller repository.

```yaml
jobs:
  production-canary:
    uses: ExtraToast/github-workflows/.github/workflows/production-canary.yml@v0.3.0
    with:
      enabled: ${{ vars.PROD_CANARY_ENABLED == 'true' }}
      post-push-delay-seconds: 180
      canary-command: ./scripts/prod-canary.sh
      diagnostics-command: ./scripts/prod-canary-diagnostics.sh
      cleanup-command: ./scripts/prod-canary-cleanup.sh
      notification-message: Production canary failed.
    secrets:
      webhook-url: ${{ secrets.PROD_CANARY_WEBHOOK_URL }}
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `enabled` | `true` | Allows callers to opt in through repository variables. |
| `canary-command` | required | Caller-owned smoke command. |
| `cleanup-command` | empty | Optional cleanup command run with `always()`. |
| `diagnostics-command` | empty | Optional diagnostics command run on failure. |
| `working-directory` | `.` | Directory where commands run. |
| `timeout-minutes` | `5` | Canary job timeout. |
| `post-push-delay-seconds` | `0` | Delay for push-triggered callers. |
| `notification-message` | `Production canary failed.` | Message prefix posted on failure. |

### `jvm-ci.yml`

Runs generic Gradle lint and test jobs for JVM repositories.

```yaml
jobs:
  jvm-ci:
    uses: ExtraToast/github-workflows/.github/workflows/jvm-ci.yml@v0.2.0
    with:
      java-version: '21'
      gradle-args: --no-daemon --stacktrace
      lint-gradle-args: detekt ktlintCheck
      test-gradle-args: test
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
```

Inputs:

| Name | Default | Purpose |
| --- | --- | --- |
| `java-version` | `21` | Java version installed for both jobs. |
| `java-distribution` | `temurin` | Java distribution installed for both jobs. |
| `working-directory` | `.` | Directory that contains the Gradle build. |
| `gradle-command` | `./gradlew` | Gradle command to run. |
| `gradle-args` | `--no-daemon` | Additional arguments appended to lint and test commands. |
| `lint-gradle-args` | `check` | Gradle tasks or arguments for lint checks. |
| `test-gradle-args` | `test` | Gradle tasks or arguments for tests. |
| `packages-token` | empty | GitHub Packages token passed to `setup-java-gradle` for Gradle package resolution. Prefer passing it as the `packages-token` workflow secret. |
