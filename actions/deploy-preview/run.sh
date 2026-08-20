#!/usr/bin/env bash
# actions/deploy-preview/run.sh
#
# Renders a service repository's deployment, scores it, and reports the result
# on the pull request.
#
# The rendering and the scorecard used to live here in bash, and a near-copy of
# both lived in every service repository as platform/render-local.sh. The two
# drifted: this copy read deployment.yml with yq and consulted the artifact
# contract, while the per-repo copy re-derived the same answers by grepping raw
# text, so a local run and a CI run could disagree about the same repository.
# Both are now tools/deploy-check, which is unit tested and published as
# @jorisjonkers-dev/deploy-check for local use. What remains here is the part
# that is genuinely specific to running inside Actions: pulling the context
# package, posting the sticky comment, and emitting the gate summary.
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

fail() {
  printf '::error::%s\n' "$*" >&2
  exit 1
}

warn() {
  printf '::warning::%s\n' "$*" >&2
}

emit_gate_summary() {
  local gate="$1"
  local check_name="$2"
  local status="$3"
  local reason="$4"
  local actor_decision="$5"

  cat > gate-summary.json <<EOF
{
  "gate": "${gate}",
  "check_name": "${check_name}",
  "status": "${status}",
  "reason": "${reason}",
  "flaky_candidates": [],
  "actor_decision": "${actor_decision}",
  "redacted": false
}
EOF
}

post_sticky_pr_comment() {
  local marker="<!-- deploy-preview-marker -->"
  local summary_file="$1"
  local body
  body=$(cat "$summary_file")

  if [[ -z "${GITHUB_TOKEN:-}" || -z "${PR_NUMBER:-}" || -z "${GITHUB_REPOSITORY:-}" ]]; then
    warn "Skipping PR comment: GITHUB_TOKEN, PR_NUMBER, or GITHUB_REPOSITORY not set"
    return 0
  fi

  local api_base="https://api.github.com/repos/${GITHUB_REPOSITORY}"

  # Find existing bot comment with the marker
  local existing_comment_id=""
  local comments_response
  comments_response=$(curl -sSf \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    "${api_base}/issues/${PR_NUMBER}/comments?per_page=100" 2>/dev/null || echo "[]")

  existing_comment_id=$(printf '%s' "$comments_response" \
    | jq -r --arg marker "$marker" \
      '.[] | select(.body | contains($marker)) | .id | tostring' \
    | head -1)

  local payload
  payload=$(jq -n --arg body "$body" '{"body": $body}')

  if [[ -n "$existing_comment_id" ]]; then
    # Update existing comment
    curl -sSf \
      -X PATCH \
      -H "Authorization: Bearer ${GITHUB_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "Content-Type: application/json" \
      -d "$payload" \
      "${api_base}/issues/comments/${existing_comment_id}" \
      >/dev/null
    printf 'Updated Deploy Preview PR comment (id=%s)\n' "$existing_comment_id"
  else
    # Create new comment
    curl -sSf \
      -X POST \
      -H "Authorization: Bearer ${GITHUB_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "Content-Type: application/json" \
      -d "$payload" \
      "${api_base}/issues/${PR_NUMBER}/comments" \
      >/dev/null
    printf 'Created Deploy Preview PR comment\n'
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  local deploy_dir="${DEPLOY_DIR:-deploy}"
  local schema_version="${SCHEMA_VERSION:?SCHEMA_VERSION is required}"
  local image_lock_path="${IMAGE_LOCK_PATH:-deploy/images.lock.json}"
  local context_ref="${CONTEXT_REF:?CONTEXT_REF is required}"
  local environments="${ENVIRONMENTS:-production}"
  local comment="${COMMENT:-true}"
  local gw_root="${GW_ROOT:?GW_ROOT is required}"
  local tool_dir="${gw_root}/tools/deploy-check"

  if [[ "$context_ref" != *"@sha256:"* ]]; then
    emit_gate_summary "deploy-validate" "Deploy Validate" "fail" \
      "context-ref-not-pinned" "none"
    fail "E_CONTEXT_REF_NOT_PINNED: context-ref must be digest-pinned: ${context_ref}"
  fi

  # Install the checker's dependencies from its committed lockfile. CI runs the
  # tool from this checkout rather than from the published package, so a publish
  # failure cannot break the gate and the code always matches the pinned
  # workflow SHA.
  ( cd "$tool_dir" && npm ci --no-audit --no-fund >&2 ) \
    || fail "E_DEPLOY_CHECK_DEPS_FAILED: npm ci failed in ${tool_dir}"

  # Pull the context package once. Service repos never hold the cluster
  # context; it is fetched by digest.
  local context_root="${RUNNER_TEMP:-/tmp}/deploy-preview-context"
  rm -rf "$context_root"
  mkdir -p "$context_root"
  if ! oras pull "$context_ref" --output "$context_root" 2>&1; then
    emit_gate_summary "deploy-validate" "Deploy Validate" "fail" \
      "context-pull-failed" "none"
    fail "E_CONTEXT_PULL_FAILED: oras pull ${context_ref} failed; rendering impossible"
  fi

  local check_exit=0
  node "${tool_dir}/bin/deploy-check.js" preview \
    --deploy-dir "$deploy_dir" \
    --schema-version "$schema_version" \
    --context-ref "$context_ref" \
    --context-dir "$context_root" \
    --images "$image_lock_path" \
    --environments "$environments" \
    --out out \
    --markdown-out deploy-preview-summary.md \
    || check_exit=$?

  # The scorecard is authoritative for the gate. A nonzero exit also covers
  # render and contract failures, which the summary already describes.
  local overall="fail"
  if [[ -f out/scorecard-detail.json ]]; then
    overall=$(jq -r '.overall // "fail"' out/scorecard-detail.json 2>/dev/null || echo fail)
  fi
  emit_gate_summary "deploy-validate" "Deploy Validate" "$overall" "scorecard-evaluated" "none"

  if [[ "$comment" == "true" && -f deploy-preview-summary.md ]]; then
    post_sticky_pr_comment deploy-preview-summary.md
  fi

  exit "$check_exit"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
