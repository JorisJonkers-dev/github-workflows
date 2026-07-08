#!/usr/bin/env bash
# actions/deploy-preview/run.sh
# Validate deployment fragments and render the Deploy Preview PR comment.
# All inputs arrive via environment variables set by action.yml.
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

# ---------------------------------------------------------------------------
# Schema CLI install
# ---------------------------------------------------------------------------

install_schema_cli() {
  local install_root="${RUNNER_TEMP:-/tmp}/deploy-preview-schema-cli"
  local npmrc="${install_root}/.npmrc"
  rm -rf "$install_root"
  mkdir -p "$install_root"
  {
    printf '%s\n' '@jorisjonkers-dev:registry=https://npm.pkg.github.com'
    if [[ -n "${NODE_AUTH_TOKEN:-}" ]]; then
      printf '%s\n' "//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}"
    fi
  } > "$npmrc"

  (
    cd "$install_root"
    npm init -y >/dev/null
    npm install \
      --userconfig "$npmrc" \
      --no-audit \
      --no-fund \
      --save-exact \
      "@jorisjonkers-dev/deploy-config-schema@${SCHEMA_VERSION}" >&2
  )
  printf '%s/node_modules/.bin' "$install_root"
}

# ---------------------------------------------------------------------------
# Scorecard computation (SC-11)
# ---------------------------------------------------------------------------

# count_lines <output>: count non-empty lines in a string without || echo 0 pitfall.
# grep -c already returns 0 when no matches; the || true prevents set -e from
# firing on exit code 1 (no match), and the result is a single clean integer.
count_lines() {
  printf '%s' "$1" | grep -c '^.' || true
}

compute_scorecard() {
  local deployment_yml="$1"
  local contract_yaml="$2"

  # schema_pinned: schema-version field present and non-empty in deployment
  local schema_pinned="pass"
  if ! yq '.spec.schemaVersion // ""' "$deployment_yml" 2>/dev/null | grep -q '^[0-9]'; then
    schema_pinned="fail"
  fi

  # context_pinned: contract contextRef contains @sha256:
  local context_pinned="fail"
  if yq '.spec.contextRef' "$contract_yaml" 2>/dev/null | grep -q '@sha256:'; then
    context_pinned="pass"
  fi

  # no_latest_images: none of the imageDigests values contain :latest
  local no_latest_images="pass"
  if yq '.spec.imageDigests | to_entries[].value' "$contract_yaml" 2>/dev/null | grep -q ':latest'; then
    no_latest_images="fail"
  fi

  # health_declared: every workload has health.path
  local health_declared="pass"
  if yq '.spec.workloads[].health.path // ""' "$deployment_yml" 2>/dev/null | grep -q '^$'; then
    health_declared="fail"
  fi

  # route_owner_authmode_declared: not_applicable if no routes[]
  local route_owner_authmode_declared="not_applicable"
  local has_routes=0
  has_routes=$(yq '.spec.workloads[].routes // [] | length' "$deployment_yml" 2>/dev/null \
    | awk '{s+=$1} END {print s+0}') || has_routes=0
  if [[ "${has_routes:-0}" -gt 0 ]]; then
    route_owner_authmode_declared="pass"
    if yq '.spec.workloads[].routes[].owner // ""' "$deployment_yml" 2>/dev/null | grep -q '^$'; then
      route_owner_authmode_declared="fail"
    fi
    if [[ "$route_owner_authmode_declared" == "pass" ]]; then
      if yq '.spec.workloads[].routes[].authMode // ""' "$deployment_yml" 2>/dev/null | grep -q '^$'; then
        local authmode_out
        authmode_out=$(yq '.spec.workloads[].routeDefaults.authMode // ""' "$deployment_yml" \
          2>/dev/null || true)
        local has_defaults=0
        has_defaults=$(count_lines "$authmode_out")
        # count_lines counts all lines; re-filter to non-empty alpha values
        has_defaults=$(printf '%s' "$authmode_out" | grep -c '^[a-z]' || true)
        if [[ "$has_defaults" -eq 0 ]]; then
          route_owner_authmode_declared="fail"
        fi
      fi
    fi
  fi

  # rollback_retention_acknowledged
  local rollback_retention_acknowledged="fail"
  local ack_out
  ack_out=$(yq '.spec.workloads[].rollbackTargetRetention.acknowledged' "$deployment_yml" \
    2>/dev/null || true)
  local ack=0
  ack=$(printf '%s' "$ack_out" | grep -c 'true' || true)
  local days=0
  days=$(yq '.spec.workloads[].rollbackTargetRetention.minimumDays' "$deployment_yml" \
    2>/dev/null | sort -n | tail -1 || true)
  days="${days:-0}"
  if [[ "$ack" -gt 0 && "${days}" -ge 90 ]]; then
    rollback_retention_acknowledged="pass"
  fi

  # no_raw_secrets
  local no_raw_secrets="pass"
  if grep -r 'kind: Secret' out/manifests/ 2>/dev/null | grep -q .; then
    no_raw_secrets="fail"
  fi

  # stateful_policy_declared: not_applicable if no stateful workloads
  local stateful_policy_declared="not_applicable"
  local stateful_out
  stateful_out=$(yq '.spec.workloads[] | select(.stateful == true) | .name' "$deployment_yml" \
    2>/dev/null || true)
  local has_stateful=0
  has_stateful=$(printf '%s' "$stateful_out" | grep -c '.' || true)
  if [[ "$has_stateful" -gt 0 ]]; then
    stateful_policy_declared="pass"
    if yq '.spec.workloads[] | select(.stateful == true) | .migrationPolicy // ""' \
      "$deployment_yml" 2>/dev/null | grep -q '^$'; then
      stateful_policy_declared="fail"
    fi
  fi

  # raw_manifests_guarded: not_applicable if no rawManifests.enabled workloads
  local raw_manifests_guarded="not_applicable"
  local raw_out
  raw_out=$(yq '.spec.workloads[] | select(.rawManifests.enabled == true) | .name' \
    "$deployment_yml" 2>/dev/null || true)
  local has_raw=0
  has_raw=$(printf '%s' "$raw_out" | grep -c '.' || true)
  if [[ "$has_raw" -gt 0 ]]; then
    raw_manifests_guarded="pass"
    if [[ ! -f out/raw-manifests-guard.json ]]; then
      raw_manifests_guarded="fail"
    else
      local violations=0
      violations=$(jq '.violations | length' out/raw-manifests-guard.json 2>/dev/null || echo 1)
      if [[ "$violations" -gt 0 ]]; then
        raw_manifests_guarded="fail"
      fi
    fi
  fi

  # npm_signatures_verified: from contract provenance_verified
  local npm_signatures_verified="fail"
  if yq '.spec.provenance_verified' "$contract_yaml" 2>/dev/null | grep -q 'true'; then
    npm_signatures_verified="pass"
  fi

  printf '{
  "schema_pinned": "%s",
  "context_pinned": "%s",
  "no_latest_images": "%s",
  "health_declared": "%s",
  "route_owner_authmode_declared": "%s",
  "rollback_retention_acknowledged": "%s",
  "no_raw_secrets": "%s",
  "stateful_policy_declared": "%s",
  "raw_manifests_guarded": "%s",
  "npm_signatures_verified": "%s"
}' \
    "$schema_pinned" \
    "$context_pinned" \
    "$no_latest_images" \
    "$health_declared" \
    "$route_owner_authmode_declared" \
    "$rollback_retention_acknowledged" \
    "$no_raw_secrets" \
    "$stateful_policy_declared" \
    "$raw_manifests_guarded" \
    "$npm_signatures_verified"
}

# ---------------------------------------------------------------------------
# Render preview summary markdown
# ---------------------------------------------------------------------------

render_preview_summary() {
  local scorecard_json="$1"
  local deployment_yml="${DEPLOY_DIR:-deploy}/deployment.yml"
  local contract_yaml="out/artifact-contract.yaml"

  # Extract metadata
  local artifact_name
  artifact_name=$(yq '.metadata.name // "unknown"' "$deployment_yml" 2>/dev/null || echo "unknown")
  local context_ref_display
  context_ref_display="${CONTEXT_REF:-unknown}"
  local envs_display="${ENVIRONMENTS:-production}"

  # Count workloads
  local workload_count=0
  workload_count=$(yq '.spec.workloads | length' "$deployment_yml" 2>/dev/null || echo 0)

  # Count routes
  local route_count=0
  route_count=$(yq '.spec.workloads[].routes | length' "$deployment_yml" 2>/dev/null \
    | awk '{s+=$1} END {print s+0}' || echo 0)

  # Count gatus endpoints (from rendered fragments if available)
  local gatus_count=0
  if ls out/manifests/*/gatus*.yaml 2>/dev/null | grep -q .; then
    gatus_count=$(grep -l 'gatus' out/manifests/*/gatus*.yaml 2>/dev/null | wc -l || echo 0)
  fi

  # Image refs from image lock
  local image_refs=""
  if [[ -f "${IMAGE_LOCK_PATH:-deploy/images.lock.json}" ]]; then
    image_refs=$(jq -r 'to_entries[] | "  - `\(.key)`: `\(.value)`"' \
      "${IMAGE_LOCK_PATH:-deploy/images.lock.json}" 2>/dev/null || echo "  _(none)_")
  fi

  # Render scorecard table
  local scorecard_rows=""
  while IFS="=" read -r key value; do
    local icon="✅"
    if [[ "$value" == "fail" ]]; then
      icon="❌"
    elif [[ "$value" == "not_applicable" ]]; then
      icon="➖"
    fi
    scorecard_rows+="| ${icon} | ${key} | ${value} |"$'\n'
  done < <(printf '%s' "$scorecard_json" | jq -r 'to_entries[] | "\(.key)=\(.value)"')

  # Check for escape hatch
  local escape_hatch_warning=""
  if printf '%s' "$scorecard_json" | jq -e '[.[] | select(. == "not_applicable")] | length > 0' \
    >/dev/null 2>&1; then
    escape_hatch_warning=$'\n> ⚠️ **Escape hatch in use**: one or more scorecard checks are `not_applicable`.\n'
  fi

  cat <<MARKDOWN
<!-- deploy-preview-marker -->
## Deploy Preview — ${artifact_name}

**Environments:** ${envs_display}
**Context ref:** \`${context_ref_display}\`
**Workloads:** ${workload_count} | **Routes:** ${route_count} | **Gatus entries:** ${gatus_count}

### Image refs
${image_refs}

### SC-11 Readiness Scorecard

| | Check | Status |
|---|---|---|
${scorecard_rows}
${escape_hatch_warning}
---
_Updated by deploy-preview action on push to this PR._
MARKDOWN
}

# ---------------------------------------------------------------------------
# Sticky PR comment
# ---------------------------------------------------------------------------

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

  # Install schema CLI
  local bin_path
  bin_path=$(install_schema_cli)
  export PATH="${bin_path}:${PATH}"

  mkdir -p out/manifests/preview

  # (1) Render all 5 fragments per environment.
  # The CLI takes a single --env value; loop over environments.
  # In preview mode the context package is not pulled via oras; pass via
  # --context-ref (digest ref) + --context-path (local file in deploy-dir).
  # Render failures are captured and surfaced in the scorecard; the action
  # exits nonzero at the end if any fragment failed (rendering impossible).
  local IFS_save="$IFS"
  IFS=',' read -ra preview_envs <<< "${environments}"
  IFS="$IFS_save"

  # Track per-env render failures for scorecard
  declare -A render_failures=()

  for env in "${preview_envs[@]}"; do
    env="$(printf '%s' "$env" | xargs 2>/dev/null || printf '%s' "$env")"
    [[ -z "$env" ]] && continue
    mkdir -p "out/manifests/preview/${env}"
    for fragment in \
      kubernetes-workload-fragment \
      traefik-route-fragment \
      gatus-endpoint-fragment \
      edge-catalog-fragment \
      image-metadata-fragment
    do
      local render_stderr_file
      render_stderr_file=$(mktemp)
      local render_exit=0
      deploy-config-schema render "$fragment" "$deploy_dir" \
        --env "$env" \
        --context "$context_ref" \
        --context-path "${deploy_dir}/cluster-context-public.yml" \
        --images "$image_lock_path" \
        --output "out/manifests/preview/${env}" \
        2>"$render_stderr_file" || render_exit=$?

      if [[ "$render_exit" -ne 0 ]]; then
        local diag
        diag=$(cat "$render_stderr_file")
        warn "render ${fragment} env=${env} failed (exit=${render_exit}): ${diag}"
        render_failures["${env}/${fragment}"]="${diag}"
      fi
      rm -f "$render_stderr_file"
    done
  done

  # (2) Emit artifact contract (preview mode; provenance_verified=false).
  # The CLI requires --deployment, --context (cluster-context.yml path),
  # --context-ref (digest ref), --environments (comma-separated), --images,
  # --artifact-name, and --out.
  local emit_stderr_file
  emit_stderr_file=$(mktemp)
  local emit_exit=0
  deploy-config-schema artifact emit-contract \
    --artifact-name preview \
    --environments "$environments" \
    --images "$image_lock_path" \
    --context-ref "$context_ref" \
    --deployment "${deploy_dir}/deployment.yml" \
    --context "${deploy_dir}/cluster-context-public.yml" \
    --provenance-verified false \
    --out out/artifact-contract.yaml \
    2>"$emit_stderr_file" || emit_exit=$?

  local emit_diag=""
  if [[ "$emit_exit" -ne 0 ]]; then
    emit_diag=$(cat "$emit_stderr_file")
    warn "artifact emit-contract failed (exit=${emit_exit}): ${emit_diag}"
  fi
  rm -f "$emit_stderr_file"

  # (3) Compute SC-11 scorecard.
  # If emit-contract failed the contract file may not exist; scorecard handles missing files.
  local scorecard
  scorecard=$(compute_scorecard "${deploy_dir}/deployment.yml" out/artifact-contract.yaml)

  # If rendering was impossible (emit-contract failed), mark scorecard fields fail.
  if [[ "$emit_exit" -ne 0 ]]; then
    scorecard=$(printf '%s' "$scorecard" \
      | jq \
        --arg diag "${emit_diag}" \
        '.context_pinned = "fail" | .no_latest_images = "fail"')
  fi

  # If any render failed, mark no_raw_secrets fail with diagnostic
  if [[ "${#render_failures[@]}" -gt 0 ]]; then
    scorecard=$(printf '%s' "$scorecard" \
      | jq '.no_raw_secrets = "fail"')
    for key in "${!render_failures[@]}"; do
      warn "render failure [${key}]: ${render_failures[$key]}"
    done
  fi

  # (4) Build deploy-preview summary markdown
  render_preview_summary "$scorecard" > deploy-preview-summary.md

  # (5) Post sticky PR comment (if comment=true)
  if [[ "$comment" == "true" ]]; then
    post_sticky_pr_comment deploy-preview-summary.md
  fi

  # (6) Detect escape hatch: warn if any SC-11 field is not_applicable
  local has_not_applicable
  has_not_applicable=$(printf '%s' "$scorecard" \
    | jq '[.[] | select(. == "not_applicable")] | length > 0' 2>/dev/null || echo "false")
  if [[ "$has_not_applicable" == "true" ]]; then
    warn "Deploy Preview: escape hatch in use — one or more SC-11 checks are not_applicable"
  fi

  # (7) Emit SC-4 gate summary
  local overall
  overall=$(printf '%s' "$scorecard" \
    | jq -r '[.[] | select(. == "fail")] | length == 0 | if . then "pass" else "fail" end' \
    2>/dev/null || echo "fail")
  emit_gate_summary "deploy-validate" "Deploy Validate" "$overall" "scorecard-evaluated" "none"

  # Exit nonzero when rendering is impossible (emit-contract failed) — the PR
  # check should fail, not silently pass with a broken scorecard.
  if [[ "$emit_exit" -ne 0 ]]; then
    fail "E_EMIT_CONTRACT_FAILED: artifact emit-contract returned ${emit_exit}; rendering impossible"
  fi

  if [[ "$overall" != "pass" ]]; then
    exit 1
  fi
}

main "$@"
