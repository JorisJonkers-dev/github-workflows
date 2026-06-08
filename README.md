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
