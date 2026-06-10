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

normalize_overlays() {
  local raw="$1"
  raw="${raw//,/$'\n'}"

  OVERLAYS=()
  local line
  while IFS= read -r line || [ -n "${line}" ]; do
    line="${line//$'\r'/}"
    line="$(trim "${line}")"
    if [ -z "${line}" ] || [[ "${line}" == \#* ]]; then
      continue
    fi
    OVERLAYS+=( "${line}" )
  done <<< "${raw}"

  if [ "${#OVERLAYS[@]}" -eq 0 ]; then
    annotation error "No overlays were provided in overlay-paths."
    exit 1
  fi
}

main() {
  local mode="${MODE:-strict}"
  local working_directory="${WORKING_DIRECTORY:-.}"
  local platform_blueprints_dir=".platform-blueprints-flux-render"
  local action_workspace="${GITHUB_WORKSPACE:-$(pwd)}"
  local platform_blueprints_path="${action_workspace}/${platform_blueprints_dir}"

  case "${mode}" in
    strict|lenient)
      ;;
    *)
      annotation error "Unsupported mode: ${mode}. Use strict or lenient."
      exit 1
      ;;
  esac

  if [ ! -d "${working_directory}" ]; then
    annotation error "working-directory does not exist: ${working_directory}"
    exit 1
  fi
  if [ ! -x "${platform_blueprints_path}/scripts/validate-flux-render.sh" ]; then
    annotation error "Validation script not found in ${platform_blueprints_path}."
    exit 1
  fi
  if [ ! -d "${platform_blueprints_path}/schemas/crds" ]; then
    annotation error "CRD schema catalog not found in ${platform_blueprints_path}."
    exit 1
  fi

  local -a OVERLAYS=()
  normalize_overlays "${OVERLAY_PATHS:-}"

  local -a args=(--mode "${mode}" --crd-catalog "${platform_blueprints_path}/schemas/crds")
  local overlay
  for overlay in "${OVERLAYS[@]}"; do
    args+=(--overlay "${overlay}")
  done

  printf 'Flux render validation mode: %s\n' "${mode}"
  printf 'Working directory: %s\n' "${working_directory}"
  printf 'Overlay paths:\n'
  printf '  %s\n' "${OVERLAYS[@]}"

  (
    cd "${working_directory}"
    bash "${platform_blueprints_path}/scripts/validate-flux-render.sh" "${args[@]}"
  )
}

main "$@"
