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

## Why `JorisJonkers-dev/github-workflows` is exempt

Digest-pinning a reusable workflow in the **same** org would break the
`job_workflow_sha` self-checkout pattern used by deploy-artifact, leak-scan, and
deploy-validate: those workflows check out github-workflows at `github.job_workflow_sha`
to guarantee the action code matches the workflow that called it, which is exactly the
integrity contract semver tagging provides within an org-controlled repo.
