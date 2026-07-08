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
  local sha256="c34d8a0e60adf77ec39a10bedb28a5baa7b9e81b9f7ee01c5fc2f53f5d00d65c"
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
  local sha256="d63f7aa6af7a1e68f5ea9be2c5b86a1f51d1ddea3fae4a1218cf3feae3e10b1c"
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
  local sha256="c22a7b5f05f3f4ced3e8d50e2b4649c6714b1ad8a17d8e3d9c62bd9432f9e66a"
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
  local sha256="1ed55e96dc8f95ad7b3f21be44c00e3b3fc62a8d12ec6a9474b18a501f84f601"
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
  local sha256="e8fc6a33fd15c4e10ad55dae02a3b3e22c50efefe83fadd84a2b4ce40e85ab2f"
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
  local sha256="c1a4c14b8dfd9c2e7ed2c1a7e0e20d37ff7a6413e87f0af5fa69ec93a0742b2d"
  local url="https://github.com/mikefarah/yq/releases/download/v${version}/yq_linux_amd64"
  curl -sSfL "$url" -o /tmp/yq
  verify_sha256 /tmp/yq "$sha256"
  chmod +x /tmp/yq
  sudo mv /tmp/yq /usr/local/bin/yq
  printf 'Installed yq %s\n' "$version"
}

main() {
  printf '::group::Installing deployment tooling\n'
  install_oras
  install_cosign
  install_syft
  install_kubeconform
  install_kustomize
  install_yq
  printf '::endgroup::\n'
}

main "$@"
