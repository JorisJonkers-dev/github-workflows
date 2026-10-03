#!/usr/bin/env bash
# Validate a project file and pack its Intent Fragment for one release.
#
# Every decision is the deploy-kit command's: this script checks the release
# version's shape, hands the command its arguments, and writes the per-file
# manifest a consumer verifies the pulled package against.
#
# The toolkit is the one the calling repository's lockfile pins. `npm ci` in
# TOOLKIT_DIRECTORY installed it, and `npx --no-install` can run nothing else.
set -euo pipefail

: "${PROJECT_FILE:?}" "${VERSION:?}" "${SOURCE_SHA:?}" "${REPOSITORY:?}" "${OUT:?}"
VALIDATE_WITH="${VALIDATE_WITH:-}"
TOOLKIT_DIRECTORY="${TOOLKIT_DIRECTORY:-.}"
DEPLOY_KIT_COMMAND="${DEPLOY_KIT_COMMAND:-npx --no-install deploy-kit}"

fail() {
  echo "publish-fragment: $*" >&2
  exit 1
}

workspace="$PWD"
absolute() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$workspace" "$1" ;;
  esac
}

deploy_kit() {
  # shellcheck disable=SC2086 # a command and its fixed options, split on purpose
  (cd "$TOOLKIT_DIRECTORY" && $DEPLOY_KIT_COMMAND "$@")
}

# A release tag is vX.Y.Z; the fragment carries X.Y.Z.
version="${VERSION#v}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version '${VERSION}' is not a release, vX.Y.Z"
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "source-sha '${SOURCE_SHA}' is not a commit"
[ -f "$PROJECT_FILE" ] || fail "no project file at ${PROJECT_FILE}"

# The project file and what is read with it: env files, Assets. A directory is
# read as every file below it.
read_together=("$(absolute "$PROJECT_FILE")")
for path in $VALIDATE_WITH; do
  [ -e "$path" ] || fail "validate-with names ${path}, which does not exist"
  read_together+=("$(absolute "$path")")
done

echo "::group::Validate"
deploy_kit validate "${read_together[@]}"
echo "::endgroup::"

echo "::group::Pack"
out="$(absolute "$OUT")"
rm -rf "$out"
deploy_kit publish "$(absolute "$PROJECT_FILE")" \
  --repository "$REPOSITORY" \
  --source-sha "$SOURCE_SHA" \
  --version "$version" \
  --out "$out"
[ -f "$out/fragment.yml" ] || fail "the command packed no fragment.yml"

# Per-file digests, so a consumer can verify the package it pulled.
manifest="$(mktemp)"
(cd "$out" && find . -type f | LC_ALL=C sort | xargs sha256sum) >"$manifest"
mv "$manifest" "$out/MANIFEST.sha256"
echo "::endgroup::"

project="$(yq '.spec.project' "$out/fragment.yml")"
inputs_sha="$(yq '.spec.inputsSha' "$out/fragment.yml")"
[[ "$project" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || fail "the fragment names project '${project}', which is not a name"

echo "packed ${project} ${version} (${inputs_sha})"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "project=${project}"
    echo "version=${version}"
    echo "inputs-sha=${inputs_sha}"
  } >>"$GITHUB_OUTPUT"
fi
