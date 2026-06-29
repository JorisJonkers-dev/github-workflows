#!/usr/bin/env bash
set -euo pipefail

annotation() {
  local kind="$1"
  local message="$2"
  printf '::%s::%s\n' "$kind" "$message"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

install_schema_cli() {
  local install_root="$1"
  local version="$2"
  local npmrc="${install_root}/.npmrc"

  rm -rf "$install_root"
  mkdir -p "$install_root"
  {
    printf '%s\n' '@jorisjonkers-dev:registry=https://npm.pkg.github.com'
    if [ -n "${NODE_AUTH_TOKEN:-}" ]; then
      printf '%s\n' "//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}"
    fi
  } > "$npmrc"

  echo "::group::Install @jorisjonkers-dev/deploy-config-schema@${version}" >&2
  (
    cd "$install_root"
    npm init -y >/dev/null
    npm install --userconfig "$npmrc" --no-audit --no-fund --save-exact "@jorisjonkers-dev/deploy-config-schema@${version}" >&2
  )
  echo "::endgroup::" >&2

  (
    cd "$install_root"
    node - <<'NODE'
const path = require('path')
const packageRoot = path.resolve('node_modules/@jorisjonkers-dev/deploy-config-schema')
const manifest = require(path.join(packageRoot, 'package.json'))
const bin = manifest.bin

let relativeBin = null
if (typeof bin === 'string') {
  relativeBin = bin
} else if (bin && typeof bin === 'object') {
  relativeBin = bin['deploy-config-schema'] || Object.values(bin)[0]
}

if (!relativeBin) {
  console.error('Package @jorisjonkers-dev/deploy-config-schema does not declare a CLI bin.')
  process.exit(1)
}

console.log(path.join(packageRoot, relativeBin))
NODE
  )
}

parse_images() {
  local raw="$1"
  raw="${raw//,/$'\n'}"

  IMAGE_ARGS=()

  local line
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line//$'\r'/}"
    line="$(trim "$line")"
    if [ -z "$line" ] || [[ "$line" == \#* ]]; then
      continue
    fi
    IMAGE_ARGS+=( --images "$line" )
  done <<< "$raw"
}

main() {
  local deploy_dir="${DEPLOY_DIR:-deploy}"
  local images="${IMAGES:-}"
  local package_version="${PACKAGE_VERSION:-^0.6.0}"
  local version="${VERSION:-}"
  local bundle_name="${BUNDLE_NAME:-}"
  local output_directory="${OUTPUT_DIRECTORY:-}"
  local working_directory="${WORKING_DIRECTORY:-.}"

  package_version="$(trim "$package_version")"
  if [ -z "$package_version" ]; then
    annotation error 'package-version is required.'
    exit 1
  fi
  if [ ! -d "$working_directory" ]; then
    annotation error "working-directory does not exist: $working_directory"
    exit 1
  fi

  if [ -z "$(trim "$version")" ]; then
    version="${GITHUB_REF_NAME:-}"
    version="${version#v}"
  fi
  if [ -z "$(trim "$version")" ]; then
    version="0.0.0-sha-${GITHUB_SHA:-unknown}"
  fi

  if [ -z "$(trim "$bundle_name")" ]; then
    bundle_name="${GITHUB_REPOSITORY##*/}"
  fi
  if [ -z "$(trim "$bundle_name")" ]; then
    annotation error 'bundle-name is required when GITHUB_REPOSITORY is unavailable.'
    exit 1
  fi

  if [ -z "$(trim "$output_directory")" ]; then
    output_directory="${RUNNER_TEMP:-/tmp}/deploy-bundle"
  fi

  local install_root
  install_root="${RUNNER_TEMP:-/tmp}/deploy-config-schema-cli"
  local cli_bin
  cli_bin="$(install_schema_cli "$install_root" "$package_version")"

  cd "$working_directory"

  if [ ! -d "$deploy_dir" ]; then
    annotation error "deploy-dir does not exist: $deploy_dir"
    exit 1
  fi

  mapfile -t deploy_files < <(find "$deploy_dir" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
  if [ "${#deploy_files[@]}" -eq 0 ]; then
    annotation error "deploy-dir contains no YAML files: $deploy_dir"
    exit 1
  fi

  local -a IMAGE_ARGS=()
  parse_images "$images"

  mkdir -p "$output_directory"
  local bundle_path="${output_directory%/}/${bundle_name}-${version}.tar"

  echo '::group::Validate deploy directory'
  "$cli_bin" validate deployment-v2 "${deploy_files[@]}"
  echo '::endgroup::'

  echo '::group::Pack deploy bundle'
  "$cli_bin" bundle pack \
    --deploy-dir "$deploy_dir" \
    --repo "${GITHUB_REPOSITORY:-$bundle_name}" \
    --git-sha "${GITHUB_SHA:-unknown}" \
    --version "$version" \
    --out "$bundle_path" \
    "${IMAGE_ARGS[@]}"
  echo '::endgroup::'

  if [ ! -s "$bundle_path" ]; then
    annotation error "Bundle pack did not write a non-empty bundle: $bundle_path"
    exit 1
  fi

  {
    printf 'bundle-path=%s\n' "$bundle_path"
    printf 'bundle-name=%s\n' "$bundle_name"
    printf 'bundle-version=%s\n' "$version"
  } >> "$GITHUB_OUTPUT"
}

main "$@"
