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
| `repository-hygiene-guard.yml` | Block reintroduction of planning and scratch artifacts. |
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
