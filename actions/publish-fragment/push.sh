#!/usr/bin/env bash
# Push a packed Intent Fragment, sign it keyless, and read it back.
#
# The fragment is pushed under its release version. `latest`, which composition
# resolves, moves only forward: publishing an older release again leaves it
# where it is, so a re-run can never put an earlier fragment in front of
# composition. Going back is a Rollback in the Estate repository, not a push.
set -euo pipefail

: "${FRAGMENT:?}" "${PROJECT:?}" "${VERSION:?}" "${SOURCE_SHA:?}" "${OWNER:?}" "${SIGNED_REPOSITORY:?}"
SIGNER_WORKFLOW="${SIGNER_WORKFLOW:-https://github.com/JorisJonkers-dev/github-workflows/.github/workflows/publish-fragment.yml@}"

fail() {
  echo "publish-fragment: $*" >&2
  exit 1
}

repository="ghcr.io/$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')/intent-${PROJECT}"
inputs_sha="$(yq '.spec.inputsSha' "$FRAGMENT/fragment.yml")"

# The release `latest` names now, if any. Absent on a Project's first publish.
current="$(oras manifest fetch "${repository}:latest" 2>/dev/null |
  jq -r '.annotations["org.opencontainers.image.version"] // ""' || true)"
tags="$VERSION"
if [ -z "$current" ] || [ "$(printf '%s\n%s\n' "$current" "$VERSION" | sort -V | tail -n 1)" = "$VERSION" ]; then
  tags="${VERSION},latest"
else
  echo "::notice::latest stays at ${current}: ${VERSION} is older, so it is pushed under its own tag only"
fi

# The digest comes from the push itself, never from a tag another run could move.
digest="$(cd "$FRAGMENT" && oras push "${repository}:${tags}" \
  --annotation "org.opencontainers.image.version=${VERSION}" \
  --annotation "org.opencontainers.image.revision=${SOURCE_SHA}" \
  --annotation "dev.jorisjonkers.inputs-sha=${inputs_sha}" \
  --format go-template='{{.digest}}' .)"
case "$digest" in
  sha256:*) ;;
  *) fail "the push returned no digest: '${digest}'" ;;
esac

# Keyless: the identity is this run's OIDC token, so no signing key exists to
# store, rotate or leak.
cosign sign --yes "${repository}@${digest}"

# A push and a signature that succeeded are not evidence a consumer can read
# and trust what was meant. Pull it back by digest, check every file, and
# verify the signature the way composition will: this workflow's identity, run
# for this repository.
check="$(mktemp -d)"
oras pull "${repository}@${digest}" --output "$check" >/dev/null
(cd "$check" && sha256sum -c MANIFEST.sha256 >/dev/null)
cmp "$check/fragment.yml" "$FRAGMENT/fragment.yml"
cosign verify "${repository}@${digest}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp "^${SIGNER_WORKFLOW//./\\.}" \
  --certificate-github-workflow-repository "$SIGNED_REPOSITORY" >/dev/null
rm -rf "$check"

echo "published ${repository}@${digest}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "ref=${repository}@${digest}"
    echo "digest=${digest}"
  } >>"$GITHUB_OUTPUT"
fi
