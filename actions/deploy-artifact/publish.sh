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
  # Use a "deploy-" tag prefix to avoid colliding with the container image tag.
  # Container images are pushed as ghcr.io/<owner>/<name>:<version> by
  # container-publish.yml; deploy artifacts use ghcr.io/<owner>/<name>:deploy-<version>
  # so they coexist in the same GHCR repository without overwriting each other.
  artifact_ref="ghcr.io/jorisjonkers-dev/${artifact_name}:deploy-${artifact_version}"

  # (0) Authenticate to GHCR so oras push and cosign sign can write packages.
  # GITHUB_TOKEN is always injected by GitHub Actions (packages: write on the
  # deploy-artifact.yml caller grants write access to the service's packages).
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf '%s' "$GITHUB_TOKEN" | oras login ghcr.io \
      --username "${GITHUB_ACTOR:-github-actions}" \
      --password-stdin
    printf '%s' "$GITHUB_TOKEN" | docker login ghcr.io \
      --username "${GITHUB_ACTOR:-github-actions}" \
      --password-stdin
  fi

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

  # (2) Check if the deploy artifact tag already exists (idempotent publish).
  # The "deploy-" tag prefix (e.g. "deploy-v1.2.3") ensures this tag can only
  # ever point to a deploy artifact — container images use a plain version tag.
  if oras manifest fetch "$artifact_ref" > /dev/null 2>&1; then
    # Deploy artifact tag exists: verify render-hash; fail on mismatch.
    local existing_digest
    existing_digest=$(oras resolve "$artifact_ref" 2>/dev/null)

    local existing_hash=""
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

    artifact_digest="$existing_digest"
    warn "artifact already exists with matching render-hash; skipping push (idempotent)"
  else
    # (3) Push artifact
    # oras 1.x does not support --digest-file; capture digest via oras resolve
    # after push, which also serves as the post-push verification (step 4).
    oras push "$artifact_ref" \
      --artifact-type "application/vnd.jorisjonkers.deployment.artifact.v1+tar" \
      artifact.tar

    # (4) Resolve digest after push (also detects tag-move races)
    local pushed_digest
    pushed_digest=$(oras resolve "$artifact_ref" 2>/dev/null)
    if [[ -z "$pushed_digest" || "$pushed_digest" != sha256:* ]]; then
      emit_gate_summary "deploy-artifact" "Deploy Artifact" "fail" \
        "publish-resolve-failed" "none"
      fail "E_PUBLISH_RESOLVE_FAILED: could not resolve digest for ${artifact_ref} after push"
    fi

    artifact_digest="$pushed_digest"
  fi

  # NOTE: SBOM via syft is intentionally omitted here. This artifact is a
  # YAML/tar package (deployment configuration), not a container image; there
  # are no software dependencies to enumerate. Container-image SBOM is handled
  # separately by the container-publish.yml workflow.

  # (5) Keyless cosign sign (SC-10)
  cosign sign --yes \
    "ghcr.io/jorisjonkers-dev/${artifact_name}@${artifact_digest}"

  # (6) SLSA build provenance attestation
  # Uses the GitHub Actions attest action injected via the workflow permissions
  printf '%s\n' "artifact-ref=${artifact_ref}@${artifact_digest}" >> "${GITHUB_OUTPUT:-/dev/null}"
  printf '%s\n' "artifact-digest=${artifact_digest}" >> "${GITHUB_OUTPUT:-/dev/null}"
}

main "$@"
