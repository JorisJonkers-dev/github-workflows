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

Builds CRaC training Docker targets, runs them with Postgres, Valkey, and
RabbitMQ sidecars, validates that a checkpoint was produced, and uploads one
checkpoint artifact per service.

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
            "port": 8080
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
