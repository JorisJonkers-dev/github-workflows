# Path buckets

A bucketed pipeline runs only the suites a pull request's diff can affect. One
file in the consuming repository says which paths reach which suites, the
`path-buckets.yml` reusable workflow decides per pull request, and each job
gates on the result.

## The buckets file

`.github/buckets.yml`:

```yaml
buckets:
  go: ['**/*.go', 'go.mod', 'go.sum']
  web: ['web/**', '!web/README.md']
  ignore: ['docs/**', '*.md']
groups:
  app: [go, web]
fixtures:
  cmd/app/main.go: [go]
  go.mod: [go]
  go.sum: [go]
  web/src/main.ts: [web]
  web/README.md: [ignore]
  docs/adr/0001.md: [ignore]
  README.md: [ignore]
  .github/workflows/ci.yml: []
```

- **A bucket claims a path** when one of its patterns takes it and none of its
  `!` patterns drops it. `**`, `*` and `?` work as in a shell glob, with `**`
  crossing directories.
- **A path no bucket claims runs everything.** Leave build files, the workflow
  itself and shared tooling out of every bucket, so a change to them runs the
  whole pipeline.
- **`ignore` is required** and gates nothing: it claims what no suite reads, so
  a docs-only pull request runs no suite.
- **Groups** are derived gates, on when any member bucket is.
- **Fixtures** are the self-test: each path must land in exactly the buckets
  listed, `[]` meaning none. Every pattern needs a fixture that would notice
  its removal, so the table cannot fall behind the patterns.

## The pipeline

```yaml
on:
  pull_request:          # no `paths:` filter: a filtered trigger reports no check,
  push:                  # and a check that never reports can never be required
    branches: [main]

jobs:
  changes:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/path-buckets.yml@vX.Y.Z
    with:
      workflows: .github/workflows/ci.yml

  test:
    needs: changes
    if: fromJSON(needs.changes.outputs.run).go
    # ...

  pipeline-complete:
    name: Pipeline Complete
    if: always()
    needs: [changes, test]
    # accept `skipped` for gated jobs, never for `changes`
```

A pull request runs the buckets its diff reaches. A push, a merge queue entry or
a dispatch runs every bucket. The self-test runs before every decision and also
fails when a workflow gates on a name the file does not declare, which would
otherwise read as false and skip that job on every run.
