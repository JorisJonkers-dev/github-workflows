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

validate_schema_kind() {
  case "$1" in
    platform|deploy-config|service-intent|fleet-inventory|vault-dynamic-secrets|auto)
      ;;
    *)
      annotation error "Unsupported schema-kind: $1. Use platform, deploy-config, service-intent, fleet-inventory, vault-dynamic-secrets, or auto."
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
    printf '%s\n' 'always-auth=true'
  } > "$npmrc"

  echo "::group::Install @jorisjonkers-dev/deploy-config-schema@$version" >&2
  (
    cd "$install_root"
    npm init -y >/dev/null
    npm install --userconfig "$npmrc" --no-audit --no-fund --save-exact "@jorisjonkers-dev/deploy-config-schema@$version" >&2
  )
  echo "::endgroup::" >&2

  (
    cd "$install_root"
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

expand_config_files() {
  local raw="$1"
  raw="${raw//,/$'\n'}"

  shopt -s nullglob globstar

  CONFIG_FILES=()
  local -A seen=()
  local pattern
  while IFS= read -r pattern || [ -n "$pattern" ]; do
    pattern="${pattern//$'\r'/}"
    pattern="$(trim "$pattern")"
    if [ -z "$pattern" ] || [[ "$pattern" == \#* ]]; then
      continue
    fi

    local -a matches=()
    # Intentionally unquoted so caller-provided glob patterns expand.
    # shellcheck disable=SC2206
    matches=( $pattern )
    if [ "${#matches[@]}" -eq 0 ] && [ -f "$pattern" ]; then
      matches=( "$pattern" )
    fi

    local match
    for match in "${matches[@]}"; do
      if [ -f "$match" ] && [ -z "${seen[$match]+x}" ]; then
        CONFIG_FILES+=( "$match" )
        seen["$match"]=1
      fi
    done
  done <<< "$raw"

  if [ "${#CONFIG_FILES[@]}" -eq 0 ]; then
    annotation error "No config files matched config-paths."
    exit 1
  fi
}

main() {
  local config_paths="${CONFIG_PATHS:-}"
  local schema_kind="${SCHEMA_KIND:-auto}"
  local package_version="${PACKAGE_VERSION:-0.3.0}"
  local drift_check
  local working_directory="${WORKING_DIRECTORY:-.}"

  if [ -z "$(trim "$package_version")" ]; then
    annotation error "package-version is required."
    exit 1
  fi

  validate_schema_kind "$schema_kind"
  drift_check="$(normalize_bool drift-check "${DRIFT_CHECK:-false}")"

  if [ ! -d "$working_directory" ]; then
    annotation error "working-directory does not exist: $working_directory"
    exit 1
  fi

  local install_root
  install_root="${RUNNER_TEMP:-/tmp}/deploy-config-schema-cli"
  local cli_bin
  cli_bin="$(install_schema_cli "$install_root" "$package_version")"

  cd "$working_directory"

  local -a CONFIG_FILES=()
  expand_config_files "$config_paths"
  local -a files=( "${CONFIG_FILES[@]}" )

  if [ "$drift_check" = "true" ] && [ "$schema_kind" != "platform" ] && [ "$schema_kind" != "auto" ]; then
    annotation error "drift-check only supports platform configs. Use schema-kind platform or auto."
    exit 1
  fi

  printf 'Validating %d platform config file(s) with @jorisjonkers-dev/deploy-config-schema@%s\n' "${#files[@]}" "$package_version"
  printf 'Schema kind: %s\n' "$schema_kind"
  printf 'Working directory: %s\n' "$working_directory"
  printf 'Drift check: %s\n' "$drift_check"
  printf 'Config files:\n'
  printf '  %s\n' "${files[@]}"

  echo "::group::deploy-config-schema validate"
  if ! "$cli_bin" validate "$schema_kind" "${files[@]}"; then
    echo "::endgroup::"
    annotation error "deploy-config-schema validate failed."
    exit 1
  fi
  echo "::endgroup::"

  if [ "$drift_check" = "true" ]; then
    local drift_file
    for drift_file in "${files[@]}"; do
      echo "::group::deploy-config-schema render-tree --check $drift_file"
      if ! "$cli_bin" render-tree "$drift_file" --check; then
        echo "::endgroup::"
        annotation error "deploy-config-schema render-tree --check reported drift for $drift_file."
        exit 1
      fi
      echo "::endgroup::"
    done
  fi
}

main "$@"
