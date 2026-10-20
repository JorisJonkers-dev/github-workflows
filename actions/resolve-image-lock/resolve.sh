#!/usr/bin/env bash
# Write a release's images lock (deploy-kit spec/v1/20-resolved-deployment.md#the-images-lock)
# from the images it pushed, read back from the registry.
#
# IMAGE_LIST holds one entry per image, separated by spaces or newlines:
#   alias                      ghcr.io/<owner>/<alias>:<TAG>
#   alias=repository           <repository>:<TAG>
#   alias=repository:tag       that reference
#
# Each entry records repository, digest, the uid and gid the image runs as, and
# version: the image's org.opencontainers.image.version label, else the tag it
# was named by when that tag is a version. TAG, the release, names our Project
# and never the software, so it is not taken for one. An image with neither is
# refused, and so is the whole lock.
set -euo pipefail

: "${LOCK_NAME:?}" "${IMAGE_LIST:?}" "${TAG:?}" "${OWNER:?}" "${OUT:?}"

owner="$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')"
failed=0
refuse() {
  echo "::error::$*"
  failed=1
}

lock="$(mktemp)"
{
  echo 'apiVersion: lock.jorisjonkers.dev/v1'
  echo 'kind: ImagesLock'
  echo 'schemaVersion: 1.0.0'
  echo "name: ${LOCK_NAME}"
  echo 'images:'
} >"$lock"

for entry in $IMAGE_LIST; do
  alias="${entry%%=*}"
  named_tag=""
  if [ "$alias" = "$entry" ]; then
    repository="ghcr.io/${owner}/${alias}"
    tag="$TAG"
  else
    reference="${entry#*=}"
    repository="$reference"
    tag="$TAG"
    case "${reference##*/}" in
      *:*)
        repository="${reference%:*}"
        tag="${reference##*:}"
        named_tag="$tag"
        ;;
    esac
  fi
  ref="${repository}:${tag}"

  digest="$(docker buildx imagetools inspect "$ref" --format '{{json .Manifest}}' | jq -r '.digest')" || digest=""
  case "$digest" in
    sha256:*) ;;
    *) refuse "no digest for ${ref}: it was not pushed, or this token cannot read it"; continue ;;
  esac

  config="$(docker buildx imagetools inspect "${repository}@${digest}" --format '{{ json .Image }}' \
    | jq -c 'if has("linux/amd64") then .["linux/amd64"] else . end | .config')"
  user="$(jq -r '.User // ""' <<<"$config")"
  case "$user" in
    *[!0-9:]* | "" | *: | :*) refuse "${alias} (${ref}) runs as '${user}', not a numeric uid[:gid]"; continue ;;
  esac

  version="$(jq -r '.Labels["org.opencontainers.image.version"] // ""' <<<"$config")"
  if [ -z "$version" ] && [[ "$named_tag" =~ ^v?[0-9]+(\.[0-9]+)+([._+-][0-9A-Za-z._+-]+)?$ ]]; then
    version="$named_tag"
  fi
  if [ -z "$version" ]; then
    refuse "${alias} (${ref}) names no software version. Build it with the shared image build or set its org.opencontainers.image.version label, or push a versioned tag and name it as ${alias}=${repository}:<version>"
    continue
  fi

  {
    echo "  ${alias}:"
    echo "    repository: ${repository}"
    echo "    digest: \"${digest}\""
    echo "    uid: ${user%%:*}"
    echo "    gid: ${user##*:}"
    echo "    version: \"${version}\""
  } >>"$lock"
done

if [ "$failed" -ne 0 ]; then
  rm -f "$lock"
  exit 1
fi

mv "$lock" "$OUT"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  { echo '```yaml'; cat "$OUT"; echo '```'; } >>"$GITHUB_STEP_SUMMARY"
fi
cat "$OUT"
