#!/usr/bin/env bash
# actions/deploy-artifact/run.sh
# Render deployment fragments for a service and emit the artifact contract.
# All inputs arrive via environment variables set by action.yml.
#
# The render loop, the forbidden-kind guard and the contract emission used to be
# spelled out here in bash, duplicating what actions/deploy-preview did and what
# every service repository carried as platform/render-local.sh. They now come
# from tools/deploy-check, so the artifact published on a tag and the preview
# shown on a pull request are produced by the same code. What remains here is
# what is specific to publishing: provenance verification, the image-lock guard,
# pulling the context, and exporting the render hash.
set -euo pipefail

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
  local redacted=false
  shift 5
  for flag in "$@"; do
    if [[ "$flag" == "--redacted" ]]; then
      redacted=true
    fi
  done

  cat > gate-summary.json <<EOF
{
  "gate": "${gate}",
  "check_name": "${check_name}",
  "status": "${status}",
  "reason": "${reason}",
  "flaky_candidates": [],
  "actor_decision": "${actor_decision}",
  "redacted": ${redacted}
}
EOF
}

require_digest_ref() {
  local ref="$1"
  if [[ "$ref" != *"@sha256:"* ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "context-ref-not-pinned" "none"
    fail "E_CONTEXT_REF_NOT_PINNED: context-ref must contain @sha256: digest: $ref"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  local deploy_dir="${DEPLOY_DIR:-deploy}"
  local artifact_name="${ARTIFACT_NAME:?ARTIFACT_NAME is required}"
  local schema_version="${SCHEMA_VERSION:?SCHEMA_VERSION is required}"
  local image_lock_path="${IMAGE_LOCK_PATH:-deploy/images.lock.json}"
  local context_ref="${CONTEXT_REF:?CONTEXT_REF is required}"
  local environments="${ENVIRONMENTS:-production}"
  local apply_bundle="${APPLY_BUNDLE:-false}"
  local gw_root="${GW_ROOT:?GW_ROOT is required}"
  local tool_dir="${gw_root}/tools/deploy-check"

  # (1) Require digest-pinned context ref — fail early
  require_digest_ref "$context_ref"

  # (2) Install the checker's dependencies from its committed lockfile. The tool
  # runs from this checkout rather than the registry, so a publish failure
  # cannot break publishing and the code matches the pinned workflow SHA.
  ( cd "$tool_dir" && npm ci --no-audit --no-fund >&2 ) \
    || fail "E_DEPLOY_CHECK_DEPS_FAILED: npm ci failed in ${tool_dir}"

  # (3) Provenance verification. deploy-check installs the pinned toolkit
  # itself, so audit the same version here and record the outcome in the
  # contract rather than inferring it.
  local install_root="${RUNNER_TEMP:-/tmp}/deploy-artifact-schema-cli"
  local npmrc="${install_root}/.npmrc"
  rm -rf "$install_root"; mkdir -p "$install_root"
  {
    printf '%s\n' '@jorisjonkers-dev:registry=https://npm.pkg.github.com'
    if [[ -n "${NODE_AUTH_TOKEN:-}" ]]; then
      printf '%s\n' "//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}"
    fi
  } > "$npmrc"
  ( cd "$install_root" && npm init -y >/dev/null && npm install --userconfig "$npmrc" \
      --no-audit --no-fund --save-exact "@jorisjonkers-dev/deploy-config-schema@${schema_version}" >&2 ) \
    || fail "E_TOOLKIT_INSTALL_FAILED: @jorisjonkers-dev/deploy-config-schema@${schema_version}"
  local installed
  installed=$(node -e "console.log(require('${install_root}/node_modules/@jorisjonkers-dev/deploy-config-schema/package.json').version)" 2>/dev/null || echo "")
  if [[ "$installed" != "$schema_version" ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" "schema-version-mismatch" "none"
    fail "E_SCHEMA_VERSION_MISMATCH: installed=${installed} declared=${schema_version}"
  fi

  # (3) npm audit signatures — provenance verification
  # NOTE: npm audit signatures only works for packages from npmjs.com.
  # Packages installed from GHPR (npm.pkg.github.com) will produce either:
  #   "found no dependencies to audit that were installed from a supported registry"
  # or (when --scope filters to zero matching packages):
  #   "found no installed dependencies to audit"
  # Both are expected skip-conditions, not failures.
  local provenance_verified=true
  local npm_audit_result
  npm_audit_result=$(npm audit signatures \
    --userconfig "$npmrc" \
    --scope @jorisjonkers-dev 2>&1) || {
    if echo "$npm_audit_result" | grep -qE "found no dependencies to audit that were installed from a supported registry|found no installed dependencies to audit"; then
      warn "npm audit signatures skipped: package is from a private registry not supported by npm audit signatures"
      provenance_verified=not_applicable
    else
      provenance_verified=false
      emit_gate_summary "npm-signatures" "npm Signatures" "fail" \
        "npm-audit-signatures-failed" "none"
      # Upload npm-signatures gate-summary before exit so finalize can reference it
      # (folded into deploy-artifact artifact — the finalize job uploads it)
      fail "E_NPM_AUDIT_SIGNATURES_FAILED: npm audit signatures returned non-zero: ${npm_audit_result}"
    fi
  }

  # (4) Guard: image lock must exist
  if [[ ! -f "$image_lock_path" ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" "image-lock-missing" "none"
    fail "E_IMAGE_LOCK_MISSING: expected at ${image_lock_path} (was image-lock-artifact set?)"
  fi

  # (5) Pull the context package once. Service repos never hold the cluster
  # context; it is fetched by digest.
  local context_root="context-pkg"
  rm -rf "$context_root"; mkdir -p "$context_root"
  if ! oras pull "$context_ref" --output "$context_root" 2>&1; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" "context-pull-failed" "none"
    fail "E_CONTEXT_PULL_FAILED: oras pull ${context_ref} failed"
  fi

  # (6) Render, guard and emit the contract. deploy-check locates
  # cluster-context-public.yml inside the pulled package, validates the
  # environment list, refuses a raw-manifests directory it cannot guard, and
  # rejects kind: Secret in the rendered output.
  local check_exit=0
  node "${tool_dir}/bin/deploy-check.js" preview \
    --deploy-dir "$deploy_dir" \
    --bin "${install_root}/node_modules/.bin/deploy-config-schema" \
    --context-ref "$context_ref" \
    --context-dir "$context_root" \
    --images "$image_lock_path" \
    --environments "$environments" \
    --artifact-name "$artifact_name" \
    --apply-bundle "$apply_bundle" \
    --provenance-verified "$provenance_verified" \
    --out out \
    || check_exit=$?

  if [[ "$check_exit" -ne 0 ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" "deploy-check-failed" "none"
    fail "E_DEPLOY_CHECK_FAILED: deploy-check exited ${check_exit}"
  fi

  # (7) Export render-hash to GITHUB_OUTPUT
  local render_hash=""
  if [[ -f out/render-hash.txt ]]; then
    render_hash=$(tr -d '[:space:]' < out/render-hash.txt)
  fi
  if [[ -z "$render_hash" ]]; then
    fail "E_RENDER_HASH_MISSING: deploy-check wrote no render hash to out/render-hash.txt"
  fi
  printf 'render-hash=%s\n' "$render_hash" >> "${GITHUB_OUTPUT:-/dev/null}"
}

# Allow sourcing for unit tests of the helpers; execute main only when invoked
# directly.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
