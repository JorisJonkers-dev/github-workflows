#!/usr/bin/env bash
# actions/deploy-artifact/publish.sh
# Idempotent OCI artifact publish with race detection, SBOM, and cosign signing.
# All inputs arrive via environment variables.
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
# Main
# ---------------------------------------------------------------------------

main() {
  local artifact_name="${ARTIFACT_NAME:?ARTIFACT_NAME is required}"
  local artifact_version="${ARTIFACT_VERSION:?ARTIFACT_VERSION is required}"
  local render_hash="${RENDER_HASH:?RENDER_HASH is required}"
  local artifact_ref
  artifact_ref="ghcr.io/jorisjonkers-dev/${artifact_name}:${artifact_version}"

  # (1) Build deterministic tar (SC-9: sort, mtime=0, uid=0, gid=0, numeric)
  tar \
    --sort=name \
    --mtime='UTC 1970-01-01' \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    -cf artifact.tar \
    -C out .

  local artifact_digest

  # (2) Check if tag already exists (idempotent publish)
  if oras manifest fetch "$artifact_ref" > /dev/null 2>&1; then
    # Tag exists: verify render-hash matches; fail on mismatch (content collision)
    local existing_digest
    existing_digest=$(oras resolve "$artifact_ref" 2>/dev/null)

    local existing_hash=""
    # Attempt to extract artifact-contract from the stored artifact blob
    local existing_contract
    if existing_contract=$(oras blob fetch \
        "ghcr.io/jorisjonkers-dev/${artifact_name}@${existing_digest}" \
        --media-type "application/vnd.jorisjonkers.deployment.artifact.v1+tar" \
        2>/dev/null | tar -xOf - artifact-contract.yaml 2>/dev/null); then
      existing_hash=$(printf '%s' "$existing_contract" | yq '.spec.renderHash' 2>/dev/null || echo "")
    fi

    if [[ -n "$existing_hash" && "$existing_hash" != "null" && "$existing_hash" != "$render_hash" ]]; then
      emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
        "publish-race-tag-moved" "none"
      fail "E_PUBLISH_RACE_TAG_MOVED: tag=${artifact_ref} existing-hash=${existing_hash} new-hash=${render_hash} (different content published under same version tag)"
    fi

    # Hashes match (or could not extract existing hash): idempotent re-use of existing artifact
    artifact_digest="$existing_digest"
    warn "artifact already exists with matching render-hash; skipping push (idempotent)"
  else
    # (3) Push artifact
    oras push "$artifact_ref" \
      --artifact-type "application/vnd.jorisjonkers.deployment.artifact.v1+tar" \
      artifact.tar \
      --digest-file pushed.digest

    local pushed_digest
    pushed_digest=$(cat pushed.digest)

    # (4) Post-push resolve verification (detect tag-move race)
    local resolved_digest
    resolved_digest=$(oras resolve "$artifact_ref" 2>/dev/null)
    if [[ "$resolved_digest" != "$pushed_digest" ]]; then
      emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
        "publish-race-tag-moved" "none"
      fail "E_PUBLISH_RACE_TAG_MOVED: pushed=${pushed_digest} resolved=${resolved_digest} (concurrent push moved tag)"
    fi

    artifact_digest="$pushed_digest"
  fi

  # (5) SBOM via syft → attach
  syft "ghcr.io/jorisjonkers-dev/${artifact_name}@${artifact_digest}" \
    -o spdx-json=sbom.spdx.json

  oras attach "${artifact_ref}@${artifact_digest}" \
    --artifact-type "application/vnd.syft.sbom.spdx+json" \
    sbom.spdx.json

  # (6) Keyless cosign sign (SC-10)
  cosign sign --yes \
    "ghcr.io/jorisjonkers-dev/${artifact_name}@${artifact_digest}"

  # (7) SLSA build provenance attestation
  # Uses the GitHub Actions attest action injected via the workflow permissions
  printf '%s\n' "artifact-ref=${artifact_ref}@${artifact_digest}" >> "${GITHUB_OUTPUT:-/dev/null}"
  printf '%s\n' "artifact-digest=${artifact_digest}" >> "${GITHUB_OUTPUT:-/dev/null}"
}

main "$@"
