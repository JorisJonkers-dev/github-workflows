# @jorisjonkers-dev/deploy-check

Renders a service repository's deployment and scores it against the SC-11
readiness checks. The `deploy-preview` action runs this exact code, so a local
run and a CI run cannot disagree about the same repository.

## Why this exists

The rendering and the scorecard were previously written twice: once in
`actions/deploy-preview/run.sh` and once in a per-repository
`platform/render-local.sh`, roughly 400 lines of bash copied into seven
repositories. The copies drifted. The CI version read `deployment.yml` with
`yq` and consulted the artifact contract; the local version re-derived the same
answers by grepping raw text. Separately, the local copy could not run at all —
it passed a directory where the toolkit expects a file, and called two
`artifact` subcommands that the toolkit does not publish.

Consolidating removes the copies and makes the logic testable: the scorecard is
a pure function, and the toolkit invocations are asserted against a stub that
records its arguments.

## Local use

```bash
npx @jorisjonkers-dev/deploy-check preview \
  --deploy-dir platform \
  --schema-version 0.20.0 \
  --context-ref ghcr.io/jorisjonkers-dev/cluster-deploy-context-public@sha256:<digest> \
  --context-dir ./context-package
```

Take `--schema-version` and `--context-ref` from the repository's
`deploy-preview.yml`, so a local check matches what CI will do. The toolkit
compares its own version against the context's `spec.schemaVersion` with strict
string equality, so the version is not a preference — an off-by-a-patch install
fails with `E_SCHEMA_VERSION_MISMATCH`.

Already have the context pulled by digest? Pass `--context-ref` together with
`--context-path <cluster-context-public.yml>` instead of `--context-dir`.

Exit status is 0 when every check passes or is `not_applicable`, and 1 when a
check fails or a render fails. Results are written to `out/`:
`scorecard.json`, `scorecard-detail.json` (with a reason per check),
`scorecard.md`, and `deploy-preview-summary.md`.

## The three statuses

| status | meaning |
|---|---|
| `pass` | the check ran and the condition holds |
| `fail` | the check ran and the condition does not hold |
| `not_applicable` | the check does not apply, or nothing was produced to inspect |

`not_applicable` is deliberately distinct from `pass`. Reporting `pass` for an
absent input is what let a preview that rendered nothing at all report itself
ready to deploy: the secret scan grepped an empty directory, found no `Secret`,
and called that a pass.

## Library use

```js
import { computeScorecard, renderPreviewSummary } from '@jorisjonkers-dev/deploy-check'
```

`computeScorecard(deployment, contract, evidence)` is pure — all filesystem
reads happen in the caller — so it can be exercised directly against fixtures.
