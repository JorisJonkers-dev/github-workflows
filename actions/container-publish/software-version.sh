#!/usr/bin/env bash
# Work out the version of the software inside an image, for its
# org.opencontainers.image.version label.
#
#   OVERRIDE set                       -> OVERRIDE
#   last stage FROM a digest-pinned    -> that base's tag, when it names a version;
#   upstream image                        otherwise unset, and the summary says so
#   anything else (our own software)   -> RELEASE, without its leading v
#
# Writes version=<value> (empty when unset) to GITHUB_OUTPUT.
set -euo pipefail

: "${DOCKERFILE:?}" "${RELEASE:?}"
OVERRIDE="${OVERRIDE:-}"

say() {
  echo "$*"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "$*" >>"$GITHUB_STEP_SUMMARY"; fi
}

emit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "version=$1" >>"$GITHUB_OUTPUT"; else echo "version=$1"; fi
}

if [ -n "$OVERRIDE" ]; then
  say "Software version ${OVERRIDE}, as the caller named it."
  emit "$OVERRIDE"
  exit 0
fi

[ -f "$DOCKERFILE" ] || { echo "::error::no Dockerfile at ${DOCKERFILE}" >&2; exit 1; }

# Every stage as "<alias> <image>", in order; an earlier stage's alias may be
# the image a later one builds from.
stages="$(awk '
  toupper($1) == "FROM" {
    i = 2
    while ($i ~ /^--/) i++
    image = $i
    alias = "-"
    if (toupper($(i + 1)) == "AS") alias = $(i + 2)
    print alias, image
  }' "$DOCKERFILE")"
[ -n "$stages" ] || { echo "::error::${DOCKERFILE} has no FROM line" >&2; exit 1; }

image="$(printf '%s\n' "$stages" | tail -n 1 | cut -d' ' -f2)"
# Follow a stage alias back to the image it starts from.
for _ in $(seq 1 20); do
  from="$(printf '%s\n' "$stages" | awk -v a="$image" '$1 == a { print $2 }' | tail -n 1)"
  [ -n "$from" ] || break
  image="$from"
done

case "$image" in
  *@sha256:*)
    name="${image%%@*}"
    tag=""
    # The tag is after the last ':' that follows the last '/', so a registry port is not one.
    case "${name##*/}" in *:*) tag="${name##*:}" ;; esac
    if [[ "$tag" =~ ^v?[0-9]+(\.[0-9]+)+([._+-][0-9A-Za-z._+-]+)?$ ]]; then
      say "Software version ${tag}, from the base image ${name}."
      emit "$tag"
    else
      say "The base image ${name} names no version in its tag, so org.opencontainers.image.version is left unset. Pass the software-version input to name it."
      emit ""
    fi
    ;;
  *)
    version="${RELEASE#v}"
    say "Software version ${version}, the release: the image builds our own software on ${image}."
    emit "$version"
    ;;
esac
