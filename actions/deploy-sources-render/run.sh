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

normalize_bool() {
  local name="$1"
  local value
  value="$(printf '%s' "${2:-false}" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    true|1|yes|y|on)
      printf 'true'
      ;;
    false|0|no|n|off|'')
      printf 'false'
      ;;
    *)
      annotation error "Unsupported boolean for $name: ${2}. Use true or false."
      exit 1
      ;;
  esac
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

require_file() {
  local path="$1"
  local label="$2"
  if [ ! -f "$path" ]; then
    annotation error "$label does not exist: $path"
    exit 1
  fi
}

main() {
  local package_version="${PACKAGE_VERSION:-^0.6.0}"
  local environment="${ENVIRONMENT:-production}"
  local sources_path="${SOURCES_PATH:-deployment-sources.yml}"
  local lock_path="${LOCK_PATH:-deployment.lock.yml}"
  local node_contract_path="${NODE_CONTRACT_PATH:-inventory/node-contract.lock.yml}"
  local reachability_path="${REACHABILITY_PATH:-catalog/reachability.yml}"
  local output_path="${OUTPUT_PATH:-cluster/flux}"
  local working_directory="${WORKING_DIRECTORY:-.}"
  local update_lock
  local check
  local cutover_parity
  local current_tree="${CURRENT_TREE:-}"

  package_version="$(trim "$package_version")"
  if [ -z "$package_version" ]; then
    annotation error 'package-version is required.'
    exit 1
  fi
  if [ ! -d "$working_directory" ]; then
    annotation error "working-directory does not exist: $working_directory"
    exit 1
  fi

  update_lock="$(normalize_bool update-lock "${UPDATE_LOCK:-false}")"
  check="$(normalize_bool check "${CHECK:-true}")"
  cutover_parity="$(normalize_bool cutover-parity "${CUTOVER_PARITY:-false}")"

  local install_root
  install_root="${RUNNER_TEMP:-/tmp}/deploy-config-schema-cli"
  local cli_bin
  cli_bin="$(install_schema_cli "$install_root" "$package_version")"

  cd "$working_directory"

  require_file "$sources_path" 'sources-path'
  require_file "$lock_path" 'lock-path'
  require_file "$node_contract_path" 'node-contract-path'
  require_file "$reachability_path" 'reachability-path'

  echo '::group::Resolve deployment sources'
  "$cli_bin" resolve-sources --sources "$sources_path" --lock "$lock_path" --check
  echo '::endgroup::'

  if [ "$update_lock" = 'true' ]; then
    echo '::group::Update deployment lock'
    "$cli_bin" lock --sources "$sources_path" --lock "$lock_path" --update
    echo '::endgroup::'
  fi

  local -a compile_args=(
    compile
    --env "$environment"
    --sources "$sources_path"
    --lock "$lock_path"
    --node-contract "$node_contract_path"
    --reachability "$reachability_path"
    --out "$output_path"
  )
  if [ "$check" = 'true' ]; then
    compile_args+=( --check )
  fi

  echo '::group::Compile deployment sources'
  "$cli_bin" "${compile_args[@]}"
  echo '::endgroup::'

  local image_tags
  image_tags="$("$cli_bin" lock images --lock "$lock_path" --format image-tags)"
  {
    printf 'image-tags<<EOF\n'
    printf '%s\n' "$image_tags"
    printf 'EOF\n'
  } >> "$GITHUB_OUTPUT"

  if [ "$cutover_parity" = 'true' ]; then
    if [ -z "$(trim "$current_tree")" ]; then
      annotation error 'current-tree is required when cutover-parity is true.'
      exit 1
    fi
    echo '::group::Cutover parity'
    "$cli_bin" parity --current "$current_tree" --rendered "$output_path" --allow-flux-source-diff true
    echo '::endgroup::'
  fi

  if [ "$check" = 'true' ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo '::group::Render drift'
    git diff --exit-code -- "$lock_path" "$node_contract_path" "$reachability_path" "$output_path"
    echo '::endgroup::'
  fi
}

main "$@"
