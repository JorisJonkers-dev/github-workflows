# github-workflows

Reusable GitHub Actions workflows and composite actions for JorisJonkers-dev
repositories.

## What It Is

`github-workflows` centralizes CI, release, image publishing, deploy bundle,
Project automation, and repository hygiene automation. Consumer repositories
should call released tags instead of branches.

## Composite Actions

| Action | Purpose |
| --- | --- |
| `actions/setup-java-gradle` | Install Java and configure Gradle package access/cache settings. |
| `actions/setup-node` | Install Node, configure npm/pnpm/yarn caching, and install dependencies. |
| `actions/prepare-ci-host` | Download Docker image artifacts and prepare optional dev DNS. |
| `actions/platform-config-validate` | Install `@jorisjonkers-dev/deploy-config-schema` and validate platform YAML. |
| `actions/deploy-config-render-drift` | Render deploy-config adapter output and compare committed files. |
| `actions/flux-render-validate` | Validate Flux render output for GitOps repositories. |
| `actions/compose-system-test-stack` | Run caller-owned Docker Compose system-test stacks. |
| `actions/api-client-publish` | Generate and publish TypeScript, Java, and Kotlin API clients. |
| `actions/deploy-bundle` | Validate and pack a first-party `deploy/` directory as an OCI bundle. |
| `actions/deploy-sources-render` | Resolve deployment sources, compile Flux output, and emit image tags. |

## Reusable Workflows

| Workflow | Purpose |
| --- | --- |
| `node-ci.yml` | Node install, lint, typecheck, test, and build jobs. |
| `python-ci.yml` | Python lint/test workflow with overrideable commands. |
| `nix-ci.yml` | Nix installer plus flake check and optional validation. |
| `jvm-ci.yml` | Gradle lint/test workflow for JVM repositories. |
| `docker-image-ci.yml` | Build a Docker image without publishing. |
| `container-publish.yml` | Publish a GHCR image with release and sha tags. |
| `publish-api-clients.yml` | Generate and publish API clients from an OpenAPI spec. |
| `gitops-ci.yml` | Platform config, Flux render, drift, and optional system tests. |
| `platform-config-validate.yml` | Reusable platform YAML validation job. |
| `deploy-config-render-drift.yml` | Reusable deploy-config render-drift job. |
| `flux-render-validate.yml` | Reusable Flux render validation job. |
| `migration-guard.yml` | Block unsafe edits to existing Flyway-style migrations. |
| `crac-train.yml` | Build and run CRaC training images with optional sidecars. |
| `production-canary.yml` | Run caller-owned production smoke checks. |
| `deploy-bundle.yml` | Validate first-party deploy bundles and optionally publish them to GHCR. |
| `deploy-sources-render.yml` | Render deployment sources and expose image tags for downstream tests. |
| `repository-hygiene-guard.yml` | Block reintroduction of planning and scratch artifacts. |
| `add-to-project.yml` | Add opened/reopened issues and pull requests to the org Project. |

## Examples

```yaml
jobs:
  node-ci:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/node-ci.yml@v0.7.3
    with:
      package-manager: pnpm
      lint-command: pnpm lint
      test-command: pnpm test
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
```

```yaml
jobs:
  deploy-bundle:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/deploy-bundle.yml@v0.7.3
    with:
      deploy-dir: deploy
      version: ${{ needs.release.outputs.version }}
      publish: true
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
```

```yaml
jobs:
  deploy-sources-render:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/deploy-sources-render.yml@v0.7.3
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
```

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
