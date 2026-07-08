#!/usr/bin/env bash
# actions/deploy-artifact/install-tooling.sh
# Install cosign, syft, oras, kubeconform, kustomize, yq at pinned versions.
# Versions and SHA-256 digests are pinned here and verified before install.
set -euo pipefail

fail() {
  printf '::error::%s\n' "$*" >&2
  exit 1
}

verify_sha256() {
  local file="$1"
  local expected="$2"
  local actual
  actual=$(sha256sum "$file" | awk '{print $1}')
  if [[ "$actual" != "$expected" ]]; then
    fail "SHA-256 mismatch for ${file}: expected=${expected} actual=${actual}"
  fi
}

install_cosign() {
  # cosign v2.4.3 linux/amd64
  local version="2.4.3"
  local sha256="caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708"
  local url="https://github.com/sigstore/cosign/releases/download/v${version}/cosign-linux-amd64"
  curl -sSfL "$url" -o /tmp/cosign
  verify_sha256 /tmp/cosign "$sha256"
  chmod +x /tmp/cosign
  sudo mv /tmp/cosign /usr/local/bin/cosign
  printf 'Installed cosign %s\n' "$version"
}

install_syft() {
  # syft v1.28.0 linux amd64
  local version="1.28.0"
  local sha256="3edee7fe1ceb1f78360e547f57048930d57f00c7ec3d0b8bdfb902805f048468"
  local url="https://github.com/anchore/syft/releases/download/v${version}/syft_${version}_linux_amd64.tar.gz"
  curl -sSfL "$url" -o /tmp/syft.tar.gz
  verify_sha256 /tmp/syft.tar.gz "$sha256"
  tar -xzf /tmp/syft.tar.gz -C /tmp syft
  chmod +x /tmp/syft
  sudo mv /tmp/syft /usr/local/bin/syft
  printf 'Installed syft %s\n' "$version"
}

install_oras() {
  # oras v1.2.3 linux amd64
  local version="1.2.3"
  local sha256="b4efc97a91f471f323f193ea4b4d63d8ff443ca3aab514151a30751330852827"
  local url="https://github.com/oras-project/oras/releases/download/v${version}/oras_${version}_linux_amd64.tar.gz"
  curl -sSfL "$url" -o /tmp/oras.tar.gz
  verify_sha256 /tmp/oras.tar.gz "$sha256"
  tar -xzf /tmp/oras.tar.gz -C /tmp oras
  chmod +x /tmp/oras
  sudo mv /tmp/oras /usr/local/bin/oras
  printf 'Installed oras %s\n' "$version"
}

install_kubeconform() {
  # kubeconform v0.7.0 linux amd64
  local version="0.7.0"
  local sha256="c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3"
  local url="https://github.com/yannh/kubeconform/releases/download/v${version}/kubeconform-linux-amd64.tar.gz"
  curl -sSfL "$url" -o /tmp/kubeconform.tar.gz
  verify_sha256 /tmp/kubeconform.tar.gz "$sha256"
  tar -xzf /tmp/kubeconform.tar.gz -C /tmp kubeconform
  chmod +x /tmp/kubeconform
  sudo mv /tmp/kubeconform /usr/local/bin/kubeconform
  printf 'Installed kubeconform %s\n' "$version"
}

install_kustomize() {
  # kustomize v5.6.0 linux amd64
  local version="5.6.0"
  local sha256="54e4031ddc4e7fc59e408da29e7c646e8e57b8088c51b84b3df0864f47b5148f"
  local url="https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${version}/kustomize_v${version}_linux_amd64.tar.gz"
  curl -sSfL "$url" -o /tmp/kustomize.tar.gz
  verify_sha256 /tmp/kustomize.tar.gz "$sha256"
  tar -xzf /tmp/kustomize.tar.gz -C /tmp kustomize
  chmod +x /tmp/kustomize
  sudo mv /tmp/kustomize /usr/local/bin/kustomize
  printf 'Installed kustomize %s\n' "$version"
}

install_yq() {
  # yq v4.45.3 linux amd64
  local version="4.45.3"
  local sha256="2c621387e61e7f6bd14e85077c4bce36bc99d198804721501a1f14c236f3a2a9"
  local url="https://github.com/mikefarah/yq/releases/download/v${version}/yq_linux_amd64"
  curl -sSfL "$url" -o /tmp/yq
  verify_sha256 /tmp/yq "$sha256"
  chmod +x /tmp/yq
  sudo mv /tmp/yq /usr/local/bin/yq
  printf 'Installed yq %s\n' "$version"
}

main() {
  local only=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --only)
        only="${2:?--only requires a comma-separated tool list}"
        shift 2
        ;;
      *)
        fail "unknown argument: $1 (usage: install-tooling.sh [--only oras,yq,...])"
        ;;
    esac
  done

  local -a tools=(oras cosign syft kubeconform kustomize yq)
  if [[ -n "$only" ]]; then
    IFS=',' read -ra tools <<< "$only"
  fi

  printf '::group::Installing deployment tooling\n'
  for tool in "${tools[@]}"; do
    case "$tool" in
      oras) install_oras ;;
      cosign) install_cosign ;;
      syft) install_syft ;;
      kubeconform) install_kubeconform ;;
      kustomize) install_kustomize ;;
      yq) install_yq ;;
      *) fail "unknown tool: ${tool} (valid: oras,cosign,syft,kubeconform,kustomize,yq)" ;;
    esac
  done
  printf '::endgroup::\n'
}

main "$@"
