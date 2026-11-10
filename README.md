# github-workflows

Reusable GitHub Actions workflows and composite actions for JorisJonkers-dev
repositories.

## What It Is

`github-workflows` centralizes CI, release, image publishing, Intent Fragment
publishing, Project automation, and repository hygiene automation. Consumer
repositories should call released tags instead of branches.

## Composite Actions

| Action | Purpose |
| --- | --- |
| `actions/setup-java-gradle` | Install Java and configure Gradle package access/cache settings. |
| `actions/setup-node` | Install Node, configure npm/pnpm/yarn caching, and install dependencies. |
| `actions/prepare-ci-host` | Download Docker image artifacts and prepare optional dev DNS. |
| `actions/flux-render-validate` | Validate Flux render output for GitOps repositories. |
| `actions/compose-system-test-stack` | Run caller-owned Docker Compose system-test stacks. |
| `actions/api-client-publish` | Generate and publish TypeScript, Java, and Kotlin API clients. |
| `actions/path-buckets` | Decide which path buckets a pull request's diff reaches, after self-testing the caller's `.github/buckets.yml`. |
| `actions/resolve-image-lock` | Write a release's images lock (repository, digest, uid, gid and the software version from each image's `org.opencontainers.image.version` label, else its versioned tag) and upload it for `publish-fragment.yml`. Refuses an image with no version. |
| `actions/render-diff` | Compose the estate with a pull request's project file and comment the Project's render diff (deploy-kit). |

## Reusable Workflows

| Workflow | Purpose |
| --- | --- |
| `node-ci.yml` | Node install, zero-warning ESLint, TypeScript, Prettier, test, and build jobs. |
| `python-ci.yml` | Python Ruff, typecheck, and test workflow with overrideable commands. |
| `nix-ci.yml` | Nix formatter, Statix, Deadnix, flake check, and optional validation. |
| `jvm-ci.yml` | Gradle static lint/test workflow that fails Gradle warnings and deprecations. |
| `quarantine-check.yml` | Fails the build on a quarantined test whose quarantine has expired, names no issue, or lasts more than 14 days. The marker is `quarantine: <issue-url> until YYYY-MM-DD`, in the reason of whatever skips the test. |
| `pr-title-check.yml` | Fails a pull request whose title or commits are not Conventional Commits, or whose body or commits credit a coding agent (a Co-Authored-By trailer, a "generated with" banner, or a session link). |
| `mutation-report.yml` | Mutates the lines a pull request changes (gremlins for Go, Stryker for TypeScript and Vue) and keeps one comment listing the mutants that survived. Never fails the build. |
| `docker-image-ci.yml` | Build a Docker image without publishing. |
| `container-publish.yml` | Publish a GHCR image with release and sha tags, labelled `org.opencontainers.image.version` with the software inside it: the digest-pinned upstream base's tag, our release for our own software, or the `software-version` input. |
| `publish-api-clients.yml` | Generate and publish API clients from an OpenAPI spec. |
| `gitops-ci.yml` | Flux render validation and optional system tests. |
| `flux-render-validate.yml` | Reusable Flux render validation job. |
| `migration-guard.yml` | Block unsafe edits to existing Flyway-style migrations. |
| `crac-train.yml` | Build and run CRaC training images with optional sidecars. |
| `production-canary.yml` | Run caller-owned production smoke checks. |
| `publish-fragment.yml` | Validate, pack, sign and push a project file's Intent Fragment for a release, then start composition (deploy-kit). Runs deploy-kit at the version `tools/deploy-kit` pins; the caller pins none. |
| `path-buckets.yml` | The `changes` job of a bucketed pipeline: its `run` output gates each job on the buckets a diff reaches. |
| `repository-hygiene-guard.yml` | Block reintroduction of planning and scratch artifacts, and refuse pull requests that credit an AI agent. |
| `add-to-project.yml` | Add opened/reopened issues and pull requests to the org Project. |

## Examples

```yaml
jobs:
  node-ci:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/node-ci.yml@v0.7.3
    with:
      package-manager: pnpm
      test-command: pnpm test
      build-command: pnpm build
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
```

## Packages Proxy Token

Every reusable workflow that installs packages takes the proxy token as the
`PACKAGES_PROXY_TOKEN` secret. A caller adds exactly one of these lines to the
job that calls the workflow, under `uses:`:

```yaml
secrets: inherit
```

or

```yaml
secrets: { PACKAGES_PROXY_TOKEN: ${{ secrets.PACKAGES_PROXY_TOKEN }} }
```

Without the secret the job logs a warning and installs packages directly. A
caller that also sets `require-package-proxy: true` makes an empty token fail
the job instead. A pull request from a fork never receives secrets, so it only
warns. Composite actions take the same token as `packages-proxy-token`.

`container-publish.yml`, `docker-image-ci.yml` and `crac-train.yml` also pull
base images through the proxy when the secret is set: the build logs in to
`packages.jorisjonkers.dev` as the user `packages` and names the proxy a
BuildKit mirror of `docker.io`, `ghcr.io`, `gcr.io` and `quay.io`. BuildKit
falls back to the registry itself when the mirror cannot serve an image, so
the estate's own private `ghcr.io/jorisjonkers-dev/*` images still pull from
GHCR. Without the secret nothing changes: no login, no mirror.

`container-publish.yml` renamed its secret in this release from
`packages-proxy-token` (v0.44.0 callers) to `PACKAGES_PROXY_TOKEN`. The old name
still works as a deprecated alias: a caller that passes only `packages-proxy-token`
keeps working, and the new name takes precedence when both are passed.

## Agent Attribution Guard

`repository-hygiene-guard.yml` also runs `scripts/attribution_guard.py` as its
`Agent Attribution Guard` job on every `pull_request` event. No commit or pull
request credits an AI agent, model or provider. It checks the pull request's
author, title and body, and every commit between the base branch's tip and the
head:

- each commit author and `Co-Authored-By:` trailer must be the owner (an owner
  name with an owner email), reviewed automation, a GitHub App bot
  (`[<id>+]<name>[bot]@users.noreply.github.com`, under its own name) whose name
  carries no AI name, or a person whose name and address name no AI. An AI
  provider's address is never allowed. Setting `non_owner_humans.mode` to
  `refuse` drops the last case, so only the owner, automation and bots pass;
- no line may credit an AI (`Generated by Mistral Vibe.`, `Reviewed by Kimi
  K2.7`), open with a `generated with/by` banner that names no allowed generator
  (Renovate, Release Please, Dependabot), carry a trailer whose key names an AI
  (`Claude-Session:`) or whose `-by` value is one, the robot emoji outside
  Renovate's checkbox lines, an AI provider's email address, or a Claude Code
  link.

The identities, automation, AI names and provider domains live in
`data/attribution-identities.json`. The override label does not skip this job.
The guard reads the title and body when the pull request event fires, so add
`edited` to the caller's `pull_request` types to re-run it when either changes.

## Package Proxy Guard

`repository-hygiene-guard.yml` runs `scripts/package_proxy_guard.py` over the
caller's checkout. It reports six rules:

- `uncovered-install`: an installer (npm, pnpm or yarn install or ci, pip
  install, uv sync or pip, cargo build, fetch or install, go mod download, tidy,
  build, test or install, gradle, `./gradlew`, mvn or `./mvnw`) that runs in a
  job before any step sets up the proxy. A covering step is
  `setup-package-proxy`, `setup-java-gradle` or `setup-node` given its token, or
  a run step that writes the proxy netrc or sets the proxy index variables. Only
  step order counts; the guard does not evaluate `if:` conditions, so a covering
  step that is conditionally skipped still counts.
- `reusable-without-token`: a job that calls a reusable workflow must pass the
  proxy token. It passes only when it calls a `JorisJonkers-dev/github-workflows`
  workflow with `secrets: inherit` or `PACKAGES_PROXY_TOKEN`. A shared workflow
  called with only `packages-token` is reported, as is any other reusable call.
- `dockerfile-netrc`: a `RUN` that installs go, pip or uv packages without the
  `--mount=type=secret,id=packages-netrc` mount, or npm, pnpm or yarn packages
  without the `--mount=type=secret,id=packages-npmrc` mount. It also reports a
  `RUN` whose stage does not declare the build arg its index needs (`GOPROXY`,
  `PIP_INDEX_URL`, `UV_DEFAULT_INDEX`). A `FROM` starts a new stage, so an `ARG`
  does not carry across stages. Cargo and Gradle or Maven `RUN`s need no mount.
- `npmrc-registry`: a committed `.npmrc` whose `registry=` is not
  `https://packages.jorisjonkers.dev/npm/`.
- `gradle-repository`: a Gradle build file that names a third-party Maven
  repository URL. `mavenCentral()`, `gradlePluginPortal()` and `google()` are
  allowed.
- `allowlist`: an entry in the allowlist has no reason.

Out of scope: OS packages (`apt`, `apk`). Installers are matched by text on
`run:` lines, so an installer inside a script file is not seen.

The input `package-proxy-guard` selects the mode. `report` (the default) prints
the findings and passes. `enforce` fails the job. A repository migrates in
report mode first, then opts in to `enforce`. If the pinned release of this
repository has no guard script yet, the guard step is skipped with a notice.

Exceptions go in `.github/package-proxy-allow.txt`, one per line: a path glob,
then a reason. An entry without a reason is itself a finding.

## Zero-Warning Gates

`node-ci.yml` always runs ESLint through the selected package manager with
`--max-warnings=0`, runs TypeScript with `tsc --noEmit` unless
`typecheck-command` is set, and runs `prettier --check` before optional
caller-owned lint, test, and build commands.

`python-ci.yml` defaults to `ruff check .`, `python -m mypy .`, and
`python -m unittest discover`. Pyright consumers can set `typecheck-command` to
their Pyright invocation.

`nix-ci.yml` checks formatting with Alejandra by default, auto-switches to
`nixpkgs-fmt --check` when the flake or treefmt config references it, then runs
`statix check`, `deadnix --fail`, and `nix flake check`.

`jvm-ci.yml` appends `--warning-mode=fail` to both lint and test Gradle
invocations so Gradle warnings and deprecations fail CI. The default
`lint-gradle-args` is now `detektMain detektTest ktlintCheck`; consumers without
those tasks must pass their own static-only lint command. The default
`test-gradle-args` remains `test`.

```yaml
jobs:
  crac-train:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/crac-train.yml@v0.7.3
    with:
      service-matrix: >-
        [
          {
            "service": "example-api",
            "dockerfile": "services/example-api/Dockerfile",
            "sidecars": ["postgres", "valkey"]
          },
          {
            "service": "worker",
            "dockerfile": "services/worker/Dockerfile",
            "sidecars": "none"
          }
        ]
```

## Local Use

```bash
actionlint .github/workflows/*.yml
python3 -m unittest discover -s tests
```

## Links

- [Organization profile](https://github.com/JorisJonkers-dev)
- [Security policy](https://github.com/JorisJonkers-dev/.github/security/policy)
- [Changelog](./CHANGELOG.md)
- [License](./LICENSE)

Copyright (c) Joris Jonkers. Source available for viewing only; use, copying,
modification, redistribution, deployment, or reuse is not licensed. See
[LICENSE](./LICENSE).
