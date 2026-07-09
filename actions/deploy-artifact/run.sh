#!/usr/bin/env bash
# actions/deploy-artifact/run.sh
# Render deployment fragments for a service and emit the artifact contract.
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

# find_cluster_context <root>: locate cluster-context-public.yml inside a
# pulled context package tree. The published artifact carries it under
# context/public/, but the layout is discovered rather than hardcoded so a
# layout change fails loud in one place. Prefers a context/public/ match,
# falls back to the first match anywhere in the tree. Prints the path;
# returns 1 if no match exists.
find_cluster_context() {
  local root="$1"
  local matches
  matches=$(find "$root" -type f -name 'cluster-context-public.yml' 2>/dev/null | sort || true)
  if [[ -z "$matches" ]]; then
    return 1
  fi
  local preferred
  preferred=$(printf '%s\n' "$matches" | grep '/context/public/' | head -1 || true)
  if [[ -n "$preferred" ]]; then
    printf '%s' "$preferred"
  else
    printf '%s' "$(printf '%s\n' "$matches" | head -1)"
  fi
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

reject_secret_kind() {
  local dir="$1"
  local found_files
  found_files=$(grep -rl 'kind: Secret' "$dir" 2>/dev/null | tr '\n' ' ' || true)
  if [[ -n "$found_files" ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "forbidden-kind-secret" "none"
    fail "E_FORBIDDEN_KIND: kind=Secret found in rendered manifests: $found_files"
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

  # (1) Require digest-pinned context ref — fail early
  require_digest_ref "$context_ref"

  # (2) Install exact schema package (pinned version)
  local install_root="${RUNNER_TEMP:-/tmp}/deploy-artifact-schema-cli"
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
      "@jorisjonkers-dev/deploy-config-schema@${schema_version}" >&2
  )
  export PATH="${install_root}/node_modules/.bin:${PATH}"

  # Verify installed version matches exactly
  local installed
  installed=$(node -e "const d=require('fs').readFileSync(process.argv[1],'utf8');console.log(JSON.parse(d).version)" "${install_root}/node_modules/@jorisjonkers-dev/deploy-config-schema/package.json" 2>/dev/null || echo "")
  if [[ "$installed" != "$schema_version" ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "schema-version-mismatch" "none"
    fail "E_SCHEMA_VERSION_MISMATCH: installed=${installed} declared=${schema_version}"
  fi

  # (3) npm audit signatures — provenance verification
  # NOTE: npm audit signatures only works for packages from npmjs.com.
  # Packages installed from GHPR (npm.pkg.github.com) will produce
  # "found no dependencies to audit that were installed from a supported registry"
  # which is expected and not a failure.
  local provenance_verified=true
  local npm_audit_result
  npm_audit_result=$(npm audit signatures \
    --userconfig "$npmrc" \
    --scope @jorisjonkers-dev 2>&1) || {
    if echo "$npm_audit_result" | grep -q "found no dependencies to audit that were installed from a supported registry"; then
      warn "npm audit signatures skipped: package is from a private registry not supported by npm audit signatures"
      provenance_verified=false
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
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "image-lock-missing" "none"
    fail "E_IMAGE_LOCK_MISSING: expected at ${image_lock_path} (was image-lock-artifact set?)"
  fi

  # (5) Pull context package via oras (once; reused across all envs and subcommands)
  mkdir -p context-pkg
  if ! oras pull "$context_ref" --output context-pkg/ 2>&1; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "context-pull-failed" "none"
    fail "E_CONTEXT_PULL_FAILED: oras pull ${context_ref} failed"
  fi

  # (6) Locate cluster-context-public.yml in the pulled tree (usually under
  # context/public/) and validate it; fail loud if it is absent.
  # NOTE: `deploy-config-schema validate` must NOT be called on this file —
  # it has kind=ClusterContext (no deploy-config schema applies; the CLI falls
  # back to the deploy-config schema and produces spurious E_SCHEMA errors).
  # The context is validated by homelab-inventory before being published via
  # scripts/validate-contexts.mjs which uses the correct validateClusterContext
  # programmatic API. Presence-check only here.
  local context_file
  if ! context_file=$(find_cluster_context context-pkg); then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "context-file-missing" "none"
    fail "E_CONTEXT_FILE_MISSING: cluster-context-public.yml not found in pulled context package ${context_ref}; pulled files: $(find context-pkg -type f 2>/dev/null | tr '\n' ' ')"
  fi

  # (7) Process environments: CRLF strip, trim, validate name, dedupe
  local envs_raw
  envs_raw="$(printf '%s' "$environments" | sed 's/\r$//')"
  IFS=',' read -ra raw_envs <<< "$envs_raw"
  local -a envs=()
  declare -A seen_envs=()
  for env in "${raw_envs[@]}"; do
    env="$(printf '%s' "$env" | xargs 2>/dev/null || printf '%s' "$env")"
    [[ -z "$env" ]] && continue
    if [[ ! "$env" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
      emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
        "invalid-env-name" "none"
      fail "E_INVALID_ENV_NAME: '${env}'"
    fi
    if [[ "${seen_envs[$env]+set}" ]]; then
      warn "duplicate env '${env}' skipped"
      continue
    fi
    seen_envs["$env"]=1
    envs+=("$env")
  done
  if [[ ${#envs[@]} -eq 0 ]]; then
    emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
      "no-valid-environments" "none"
    fail "E_NO_VALID_ENVIRONMENTS"
  fi

  # (8) Render 5 fragments per env + emit kustomization health.
  # CLI: render <fragment-id> <deploy-dir> --env <env> --images <lock>
  #      --context <ref@sha256:..> --context-path <file> [--output <path>]
  # The context package was pulled once in step (5) and the context file
  # discovered in step (6); pass via --context <digest-ref> --context-path.
  for env in "${envs[@]}"; do
    mkdir -p "out/manifests/${env}" "out/metadata/${env}"
    for fragment in \
      kubernetes-workload-fragment \
      traefik-route-fragment \
      gatus-endpoint-fragment \
      edge-catalog-fragment \
      image-metadata-fragment
    do
      deploy-config-schema render "$fragment" "$deploy_dir" \
        --env "$env" \
        --context "$context_ref" \
        --context-path "$context_file" \
        --images "$image_lock_path" \
        --output "out/manifests/${env}"
    done

    deploy-config-schema artifact emit-kustomization-health \
      --deployment "$deploy_dir/deployment.yml" \
      --env "$env" \
      --image-digests "$image_lock_path" \
      --out "out/metadata/${env}/kustomization-health.yml"
  done

  # (9) Kubeconform validation
  if command -v kubeconform >/dev/null 2>&1; then
    kubeconform \
      -schema-location default \
      -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
      -strict \
      out/manifests/ || {
      emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
        "kubeconform-failed" "none"
      fail "E_KUBECONFORM_FAILED"
    }
  fi

  # (10) Kustomize build dry-run
  if command -v kustomize >/dev/null 2>&1; then
    for env in "${envs[@]}"; do
      kustomize build "out/manifests/${env}" --dry-run 2>&1 > /dev/null || {
        emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
          "kustomize-build-failed" "none"
        fail "E_KUSTOMIZE_BUILD_FAILED: env=${env}"
      }
    done
  fi

  # (11) Reject kind: Secret in rendered output
  reject_secret_kind "out/manifests/"

  # (12) Validate raw manifests (only when raw-manifests enabled in deployment.yml)
  local has_raw_manifests_dir="${deploy_dir}/raw-manifests"
  if [[ -d "$has_raw_manifests_dir" ]]; then
    deploy-config-schema artifact validate-raw-manifests \
      --deployment "$deploy_dir/deployment.yml" \
      --root "$deploy_dir/raw-manifests" \
      --output-root "out/raw-manifests" \
      --forbidden-kinds Secret,ClusterRole,ClusterRoleBinding,CustomResourceDefinition,Namespace \
      --out out/raw-manifests-guard.json || {
      emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
        "raw-manifests-violations" "none"
      fail "E_RAW_MANIFESTS_VIOLATIONS"
    }
  fi

  # (13) Emit artifact contract (includes SC-9 render hash).
  # CLI: artifact emit-contract
  #   --artifact-name <name>
  #   --environments <e1,e2>
  #   --images <images.lock.json>
  #   --context-ref <ref@sha256:..>
  #   --deployment <deployment.yml>
  #   --context <cluster-context.yml>
  #   --out <path>
  #   [--provenance-verified true|false]
  #   [--output-root <dir>]
  local envs_joined
  envs_joined="$(printf '%s,' "${envs[@]}" | sed 's/,$//')"
  deploy-config-schema artifact emit-contract \
    --artifact-name "$artifact_name" \
    --environments "$envs_joined" \
    --images "$image_lock_path" \
    --context-ref "$context_ref" \
    --deployment "$deploy_dir/deployment.yml" \
    --context "$context_file" \
    --provenance-verified "$provenance_verified" \
    --output-root out \
    --out out/artifact-contract.yaml

  # (14) Export render-hash to GITHUB_OUTPUT (correction #8: also declared in outputs block)
  local render_hash
  render_hash=$(yq '.spec.renderHash' out/artifact-contract.yaml)
  if [[ -z "$render_hash" || "$render_hash" == "null" ]]; then
    fail "E_RENDER_HASH_MISSING: yq could not extract .spec.renderHash from out/artifact-contract.yaml"
  fi
  printf 'render-hash=%s\n' "$render_hash" >> "${GITHUB_OUTPUT:-/dev/null}"
}

# Allow sourcing for unit tests of the helpers (find_cluster_context, ...);
# execute main only when invoked directly.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
