# Action pinning policy

All third-party GitHub Actions are referenced by SHA digest (not tag), enforced by
renovate.json `pinDigests: true` globally. The weekly "github-actions pinned digests"
PR updates these automatically every Monday before 9 am.

`JorisJonkers-dev/github-workflows` is our own reusable workflow repo; it uses semver
tags (e.g. `@v1.2.3`) and is **not** digest-pinned — the semver tag IS the integrity
signal because the repo is org-controlled. Renovate updates it when a new semver tag
is published, without digest-pinning.

`cosign`, `syft`, `oras`, `kubeconform`, `kustomize`, and `yq` are installed in
`actions/deploy-artifact/install-tooling.sh` by SHA-pinned release download URLs
(verified with `sha256sum`). These are updated by a separate `pinned-tooling` group in
renovate.json when one is added.

## Adding a new third-party action

1. Find the exact SHA for the version you want:
   ```
   gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq '.object.sha'
   ```
2. Reference it as `uses: owner/repo@<SHA> # vX.Y.Z` — the comment keeps the version
   human-readable while the SHA pins the runtime.
3. Renovate will open a weekly digest-bump PR when a newer version is published.

## How `JorisJonkers-dev/github-workflows` pins itself

Reusable workflows here check out this repository to run an action or script from
it. That checkout must land on the same version as the workflow file doing the
checking out, or a consumer pinning a tag gets that tag's workflow driving some
other version's code.

It used to use `ref: ${{ github.job_workflow_sha }}`, on the assumption that the
context names the reusable workflow's own commit. **It does not.** Echoing the
contexts inside a reusable workflow in a real run gives:

```
job_workflow_sha=[]
job_workflow_ref=[]
workflow_ref=[<owner>/<repo>/.github/workflows/<caller>.yml@refs/pull/97/merge]
```

Both `job_workflow_*` values are empty, and `workflow_ref` describes the
*caller*. With an empty `ref`, `actions/checkout` falls back to the default
branch, so every consumer ran `main` regardless of the tag it pinned — visible
in the run log as:

```
git fetch --no-tags --prune --depth=1 origin +refs/heads/main
git checkout --progress --force -B main refs/remotes/origin/main
```

Nothing failed; a tag pin simply had no effect on the action code.

Since no context carries the value, the ref is a literal:

```yaml
ref: v1.2.3 # x-release-please-version
```

release-please rewrites it in the release PR via `extra-files`, so the workflow
file shipped inside a tag references that same tag. `tests/test_actions_checkout_pinned.py`
enforces both halves: no workflow may use `job_workflow_sha`, and every literal
must be listed for bumping — an unmaintained literal is a stale pin waiting to
happen.

One consequence worth knowing: a tag released *before* this change still
contains the empty-ref pattern, so consumers pinned to an older tag keep running
`main` until they move to a tag that includes it.
