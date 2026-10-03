#!/usr/bin/env bash
# Compose the estate with the base project file and with the head one, and
# write the Project's render diff as Markdown.
#
# Exit 0 when head composes, whether or not the render changed. Exit 1 when
# head does not compose: the report then carries the diagnostics instead of a
# diff, because that is what the pull request would do to the estate.
set -euo pipefail

: "${PROJECT_FILE:?}" "${ESTATE_INPUTS:?}" "${BASE_DIRECTORY:?}" "${REPORT:?}" "${REPOSITORY:?}" "${HEAD_SHA:?}" "${BASE_SHA:?}"
HEAD_DIRECTORY="${HEAD_DIRECTORY:-.}"
TOOLKIT_DIRECTORY="${TOOLKIT_DIRECTORY:-.}"
DEPLOY_KIT_COMMAND="${DEPLOY_KIT_COMMAND:-npx --no-install deploy-kit}"
# A comment holds 65536 characters; the diff gets most of them.
MAX_DIFF_BYTES="${MAX_DIFF_BYTES:-55000}"
[[ "$MAX_DIFF_BYTES" =~ ^[0-9]+$ ]] || {
  echo "render-diff: MAX_DIFF_BYTES is not a number" >&2
  exit 1
}

# fenced <language> <file>: the file inside a code fence it cannot close. What
# is shown comes from a pull request's own files, so the fence is one backtick
# longer than the longest run of backticks in it: nothing in the file can end
# the block and continue as Markdown in a comment this action posts.
fenced() {
  local language="$1" file="$2" longest fence
  longest="$({ grep -o '`\{3,\}' "$file" || true; } | awk '{ if (length($0) > n) n = length($0) } END { print n + 0 }')"
  fence="$(printf '%*s' "$((longest > 2 ? longest + 1 : 3))" '' | tr ' ' '`')"
  printf '%s%s\n' "$fence" "$language"
  cat "$file"
  # A file that does not end in a newline must not swallow the closing fence.
  [ -z "$(tail -c 1 "$file")" ] || echo
  printf '%s\n' "$fence"
}

fail() {
  echo "render-diff: $*" >&2
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

estate="$(absolute "$ESTATE_INPUTS")"
[ -d "$estate/platform" ] || fail "${ESTATE_INPUTS}/platform is missing"
[ -d "$estate/fragments" ] || fail "${ESTATE_INPUTS}/fragments is missing"
[ -f "$estate/cluster-state.yml" ] || fail "${ESTATE_INPUTS}/cluster-state.yml is missing"
[ -f "$(absolute "$HEAD_DIRECTORY")/$PROJECT_FILE" ] || fail "no project file at ${HEAD_DIRECTORY}/${PROJECT_FILE}"

# The integrity of the toolkit that composes, read from the lockfile that pins
# it, as composition itself records it.
integrity="${SCHEMA_PACKAGE_INTEGRITY:-}"
if [ -z "$integrity" ]; then
  integrity="$(jq -r '.packages["node_modules/@jorisjonkers-dev/deploy-kit"].integrity // ""' "$TOOLKIT_DIRECTORY/package-lock.json")"
fi
[ -n "$integrity" ] || fail "${TOOLKIT_DIRECTORY}/package-lock.json pins no @jorisjonkers-dev/deploy-kit"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# compose <side> <checkout> <sha>: the estate with this side's project file in
# place of the Project's published fragment. A side with no project file, a
# Project the base branch does not have yet, composes the estate as pulled.
compose() {
  local side="$1" checkout sha="$3" fragment project
  checkout="$(absolute "$2")"
  mkdir -p "$work/$side"
  cp -R "$estate/fragments" "$work/$side/fragments"

  if [ -f "$checkout/$PROJECT_FILE" ]; then
    fragment="$work/$side/packed"
    deploy_kit publish "$checkout/$PROJECT_FILE" \
      --repository "$REPOSITORY" --source-sha "$sha" --version 0.0.0 --out "$fragment" \
      >"$work/$side/publish.log" 2>&1 || return 1
    project="$(yq '.spec.project' "$fragment/fragment.yml")"
    # The name comes from a pull request's file and is about to be a path, a
    # pattern and a comment's marker, so it is held to a name's shape first.
    [[ "$project" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || {
      echo "the project file names project '${project}', which is not a name" >"$work/$side/publish.log"
      return 1
    }
    rm -rf "$work/$side/fragments/$project"
    mv "$fragment" "$work/$side/fragments/$project"
    # A fragment is read beside the ref its pull resolved. This one was never
    # pushed, so it is named by the commit it was packed from.
    echo "ghcr.io/$(printf '%s' "${REPOSITORY%%/*}" | tr '[:upper:]' '[:lower:]')/intent-${project}@sha256:$(printf '%s' "$sha" | sha256sum | cut -d' ' -f1)" \
      >"$work/$side/fragments/$project/ref"
    echo "$project" >"$work/$side/project"
  fi

  local options=(--platform "$estate/platform" --fragments "$work/$side/fragments"
    --cluster-state "$estate/cluster-state.yml" --schema-package-integrity "$integrity"
    --out "$work/$side/composed")
  [ -d "$estate/held" ] && options+=(--held "$estate/held")
  [ -f "$estate/pins.json" ] && options+=(--pins "$estate/pins.json")
  if [ -f "$estate/previous/lock.json" ] && [ -f "$estate/previous/COMMIT" ]; then
    options+=(--lock "$estate/previous/lock.json" --lock-commit "$(cat "$estate/previous/COMMIT")")
  fi
  deploy_kit compose "${options[@]}" >"$work/$side/compose.log" 2>&1
}

head_status=0
compose head "$HEAD_DIRECTORY" "$HEAD_SHA" || head_status=$?
project="$(cat "$work/head/project" 2>/dev/null || true)"
[ -n "$project" ] || {
  cat "$work/head/publish.log" >&2 || true
  fail "the head project file could not be packed"
}
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "project=${project}" >>"$GITHUB_OUTPUT"; fi

marker="<!-- render-diff:${project} -->"
{
  echo "$marker"
  echo "### Render diff: \`${project}\`"
  echo
} >"$REPORT"

# Isolation composes a refused Project at its last fragment and still exits 0,
# so a refusal is read from what composition reports, not only its exit status.
isolated=""
if [ "$head_status" -eq 0 ] && [ -f "$work/head/composed/lock.json" ]; then
  isolated="$(jq -r --arg p "$project" '.spec.isolated[$p] // "" | if type == "object" or type == "array" then tojson else . end' "$work/head/composed/lock.json")"
fi

if [ "$head_status" -ne 0 ] || [ -n "$isolated" ]; then
  {
    echo "**This change does not compose.** Published as it is, composition would refuse \`${project}\` and keep it at its last composed release."
    echo
    {
      if [ -n "$isolated" ]; then echo "$isolated"; fi
      head -c "$MAX_DIFF_BYTES" "$work/head/compose.log"
    } >"$work/refusal.txt"
    fenced "" "$work/refusal.txt"
  } >>"$REPORT"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$REPORT" >>"$GITHUB_STEP_SUMMARY"; fi
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "changed=true" >>"$GITHUB_OUTPUT"; fi
  cat "$REPORT"
  exit 1
fi

# The base side only has to give something to diff against. If the base does
# not compose, the whole head render is shown as new.
compose base "$BASE_DIRECTORY" "$BASE_SHA" || rm -rf "$work/base/composed"
mkdir -p "$work/base/composed/artifacts/$project" "$work/head/composed/artifacts/$project"

changed=false
(cd "$work" && diff -ruN "base/composed/artifacts/$project" "head/composed/artifacts/$project" >"$work/render.diff") || changed=true
# Paths as a reviewer reads them, and no timestamps: the same change is the same comment.
sed -i.bak -E \
  -e "s#base/composed/artifacts/${project}/#a/#g" \
  -e "s#head/composed/artifacts/${project}/#b/#g" \
  -e 's#^(---|\+\+\+) ([^[:space:]]+)[[:space:]].*$#\1 \2#' \
  "$work/render.diff"

if [ "$changed" = false ]; then
  echo "No change to the rendered objects. The fragment still changes, so composition records a new release without moving the pin." >>"$REPORT"
else
  files="$(grep -c '^diff -ruN ' "$work/render.diff" || true)"
  {
    echo "${files} rendered file(s) change. This is what Flux would apply once the release is published and composed."
    echo
    head -c "$MAX_DIFF_BYTES" "$work/render.diff" >"$work/shown.diff"
    if [ "$(wc -c <"$work/render.diff")" -gt "$MAX_DIFF_BYTES" ]; then
      printf '\n... diff cut at %s bytes; run the composition locally for the rest\n' "$MAX_DIFF_BYTES" >>"$work/shown.diff"
    fi
    fenced diff "$work/shown.diff"
  } >>"$REPORT"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$REPORT" >>"$GITHUB_STEP_SUMMARY"; fi
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "changed=${changed}" >>"$GITHUB_OUTPUT"; fi
cat "$REPORT"
