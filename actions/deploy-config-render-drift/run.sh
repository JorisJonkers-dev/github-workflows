#!/usr/bin/env bash
set -euo pipefail

annotation() {
  local kind="$1"
  local message="$2"
  printf '::%s::%s\n' "${kind}" "${message}"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
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
  } > "${npmrc}"

  echo "::group::Install @jorisjonkers-dev/deploy-config-schema@${version}" >&2
  (
    cd "${install_root}"
    npm init -y >/dev/null
    npm install --userconfig "${npmrc}" --no-audit --no-fund --save-exact "@jorisjonkers-dev/deploy-config-schema@${version}" >&2
  )
  echo "::endgroup::" >&2

  (
    cd "${install_root}"
    node - <<'NODE'
const path = require("path");
const packageRoot = path.resolve("node_modules/@jorisjonkers-dev/deploy-config-schema");
const manifest = require(path.join(packageRoot, "package.json"));
const bin = manifest.bin;

let relativeBin = null;
if (typeof bin === "string") {
  relativeBin = bin;
} else if (bin && typeof bin === "object") {
  relativeBin = bin["deploy-config-schema"] || Object.values(bin)[0];
}

if (!relativeBin) {
  console.error("Package @jorisjonkers-dev/deploy-config-schema does not declare a CLI bin.");
  process.exit(1);
}

console.log(path.join(packageRoot, relativeBin));
NODE
  )
}

parse_targets() {
  local raw="$1"

  ADAPTERS=()
  TARGET_PATHS=()

  local line
  while IFS= read -r line || [ -n "${line}" ]; do
    line="${line//$'\r'/}"
    line="$(trim "${line}")"
    if [ -z "${line}" ] || [[ "${line}" == \#* ]]; then
      continue
    fi
    if [[ "${line}" != *=* ]]; then
      annotation error "Invalid target entry: ${line}. Use adapter=relative/path.yaml."
      exit 1
    fi

    local adapter="${line%%=*}"
    local target_path="${line#*=}"
    adapter="$(trim "${adapter}")"
    target_path="$(trim "${target_path}")"

    if [ -z "${adapter}" ] || [ -z "${target_path}" ]; then
      annotation error "Invalid target entry: ${line}. Adapter and path are required."
      exit 1
    fi
    if [[ "${target_path}" = /* ]]; then
      annotation error "Invalid target path for ${adapter}: ${target_path}. Use a relative path."
      exit 1
    fi

    ADAPTERS+=( "${adapter}" )
    TARGET_PATHS+=( "${target_path}" )
  done <<< "${raw}"

  if [ "${#ADAPTERS[@]}" -eq 0 ]; then
    annotation error "No render drift targets were provided."
    exit 1
  fi
}

main() {
  local package_version="${PACKAGE_VERSION:-^0.6.0}"
  local deploy_config_command="${DEPLOY_CONFIG_COMMAND:-}"
  local targets="${TARGETS:-}"
  local working_directory="${WORKING_DIRECTORY:-.}"

  package_version="$(trim "${package_version}")"
  if [ -z "${package_version}" ]; then
    annotation error "package-version is required."
    exit 1
  fi
  if [ -z "$(trim "${deploy_config_command}")" ]; then
    annotation error "deploy-config-command is required."
    exit 1
  fi
  if [ ! -d "${working_directory}" ]; then
    annotation error "working-directory does not exist: ${working_directory}"
    exit 1
  fi

  local -a ADAPTERS=()
  local -a TARGET_PATHS=()
  parse_targets "${targets}"

  local install_root
  install_root="${RUNNER_TEMP:-/tmp}/deploy-config-render-drift-cli"
  local cli_bin
  cli_bin="$(install_schema_cli "${install_root}" "${package_version}")"

  local temp_root
  temp_root="$(mktemp -d "${RUNNER_TEMP:-/tmp}/deploy-config-render-drift.XXXXXX")"
  local render_root="${temp_root}/rendered"
  mkdir -p "${render_root}"

  export DEPLOY_CONFIG_SCHEMA_LOCAL="${install_root}/node_modules/@jorisjonkers-dev/deploy-config-schema"
  export DEPLOY_CONFIG_OUT="${temp_root}/deploy-config.json"
  export PATH="${install_root}/node_modules/.bin:${PATH}"

  printf 'Deploy config render drift package: %s\n' "${package_version}"
  printf 'Working directory: %s\n' "${working_directory}"
  printf 'Deploy config output: %s\n' "${DEPLOY_CONFIG_OUT}"
  printf 'Targets:\n'
  local target_index
  for target_index in "${!ADAPTERS[@]}"; do
    printf '  %s=%s\n' "${ADAPTERS[$target_index]}" "${TARGET_PATHS[$target_index]}"
  done

  echo "::group::Generate deploy config"
  (
    cd "${working_directory}"
    eval "${deploy_config_command}"
  )
  echo "::endgroup::"

  if [ ! -s "${DEPLOY_CONFIG_OUT}" ]; then
    annotation error "deploy-config-command did not write a non-empty file to DEPLOY_CONFIG_OUT: ${DEPLOY_CONFIG_OUT}"
    exit 1
  fi

  local drift_found=0
  for target_index in "${!ADAPTERS[@]}"; do
    local adapter="${ADAPTERS[$target_index]}"
    local committed_path="${working_directory}/${TARGET_PATHS[$target_index]}"
    local rendered_path="${render_root}/${adapter}.yaml"
    mkdir -p "$(dirname "${rendered_path}")"

    echo "::group::Render ${adapter}"
    "${cli_bin}" render "${adapter}" "${DEPLOY_CONFIG_OUT}" --output "${rendered_path}"
    echo "::endgroup::"

    if [ ! -f "${committed_path}" ]; then
      printf 'DIFF %s=%s (committed file missing)\n' "${adapter}" "${TARGET_PATHS[$target_index]}"
      annotation error "Committed render target is missing: ${committed_path}"
      drift_found=1
      continue
    fi

    # Functional drift check: compare canonicalised YAML so formatting, key
    # order, and comments do not register as drift, but real content changes
    # do. This replaces exact byte matching without losing drift detection.
    if diff -q \
      <(yq -P 'sort_keys(..)' "${rendered_path}") \
      <(yq -P 'sort_keys(..)' "${committed_path}") >/dev/null 2>&1; then
      printf 'MATCH %s=%s\n' "${adapter}" "${TARGET_PATHS[$target_index]}"
    else
      printf 'DIFF %s=%s\n' "${adapter}" "${TARGET_PATHS[$target_index]}"
      annotation error "deploy-config-schema render ${adapter} drifted from ${TARGET_PATHS[$target_index]} (semantic YAML comparison)"
      drift_found=1
    fi
  done

  if [ "${drift_found}" -ne 0 ]; then
    exit 1
  fi
}

main "$@"
