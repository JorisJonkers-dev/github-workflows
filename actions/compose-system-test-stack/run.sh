#!/usr/bin/env bash

set -Eeuo pipefail

: "${COMPOSE_FILES:=docker-compose.yml}"
: "${SERVICES:=}"
: "${PROJECT_NAME:=}"
: "${WORKING_DIRECTORY:=.}"
: "${WAIT_STRATEGY:=compose-wait}"
: "${WAIT_COMMAND:=}"
: "${WAIT_ROUTES_FILE:=}"
: "${WAIT_TIMEOUT_SECONDS:=300}"
: "${WAIT_INTERVAL_SECONDS:=2}"
: "${MIGRATION_CHECK_COMMAND:=}"
: "${DIAGNOSTICS_COMMAND:=}"
: "${CLEANUP_COMMAND:=}"
: "${UP_ARGS:=--no-build --wait --timeout 300 -d}"
: "${DOWN_ON_COMPLETE:=true}"

compose_args=()
selected_services=()
stack_started=false

error() {
  echo "::error::$*" >&2
}

trim() {
  local value="$1"
  value="${value%$'\r'}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

is_true_false() {
  case "$1" in
    true|false) return 0 ;;
    *) return 1 ;;
  esac
}

build_compose_args() {
  local raw_line line

  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    line="$(trim "$raw_line")"
    if [ -z "$line" ] || [[ "$line" == \#* ]]; then
      continue
    fi
    if [ ! -f "$line" ]; then
      error "Compose file not found: $line"
      exit 1
    fi
    compose_args+=("-f" "$line")
  done <<< "$COMPOSE_FILES"

  if [ "${#compose_args[@]}" -eq 0 ]; then
    error "At least one compose file is required."
    exit 1
  fi

  if [ -n "$PROJECT_NAME" ]; then
    if [[ "$PROJECT_NAME" =~ [[:space:]] ]]; then
      error "project-name must not contain whitespace."
      exit 1
    fi
    compose_args=("-p" "$PROJECT_NAME" "${compose_args[@]}")
  fi
}

parse_services() {
  local normalized service
  normalized="${SERVICES//$'\n'/ }"
  read -r -a selected_services <<< "$normalized"

  for service in "${selected_services[@]}"; do
    if [[ ! "$service" =~ ^[A-Za-z0-9_.-]+$ ]]; then
      error "Invalid compose service name: $service"
      exit 1
    fi
  done
}

has_up_arg() {
  local expected="$1"
  local arg
  shift

  for arg in "$@"; do
    if [ "$arg" = "$expected" ] || [[ "$arg" == "$expected="* ]]; then
      return 0
    fi
  done

  return 1
}

run_compose() {
  docker compose "${compose_args[@]}" "$@"
}

run_custom_command() {
  local label="$1"
  local command="$2"

  if [ -z "$command" ]; then
    return 0
  fi

  echo "==> $label"
  bash -c "$command"
}

run_diagnostics() {
  echo "==> Built-in Docker Compose diagnostics"
  date -u || true
  run_compose ps -a || true
  run_compose logs --tail=200 --no-color || true
  docker ps -a || true
  docker system df || true

  if [ -n "$DIAGNOSTICS_COMMAND" ]; then
    run_custom_command "Caller diagnostics command" "$DIAGNOSTICS_COMMAND" || true
  fi
}

run_cleanup() {
  if [ -n "$CLEANUP_COMMAND" ]; then
    run_custom_command "Caller cleanup command" "$CLEANUP_COMMAND" || true
  fi

  if [ "$DOWN_ON_COMPLETE" = "true" ] && [ "$stack_started" = "true" ]; then
    echo "==> Stopping Docker Compose stack"
    run_compose down --remove-orphans || true
  fi
}

on_exit() {
  local status=$?
  if [ "$status" -ne 0 ] && [ "$stack_started" = "true" ]; then
    run_diagnostics
  fi
  run_cleanup
  exit "$status"
}

wait_for_route() {
  local name="$1"
  local url="$2"
  local deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))

  echo "Waiting for route ${name}: ${url}"
  while true; do
    if curl -fsS -L -k --max-time 5 -o /dev/null "$url"; then
      echo "Route reachable: ${name}"
      return 0
    fi

    if [ "$SECONDS" -ge "$deadline" ]; then
      error "Timed out waiting for route ${name}: ${url}"
      return 1
    fi

    sleep "$WAIT_INTERVAL_SECONDS"
  done
}

wait_for_routes() {
  local raw_line line name url extra count=0

  if [ -z "$WAIT_ROUTES_FILE" ]; then
    error "wait-routes-file is required when wait-strategy=routes."
    exit 1
  fi
  if [ ! -f "$WAIT_ROUTES_FILE" ]; then
    error "Route wait file not found: $WAIT_ROUTES_FILE"
    exit 1
  fi

  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    line="$(trim "$raw_line")"
    if [ -z "$line" ] || [[ "$line" == \#* ]]; then
      continue
    fi

    name=''
    url=''
    extra=''
    read -r name url extra <<< "$line"
    if [ -z "$url" ]; then
      url="$name"
      name="$url"
    fi
    if [ -n "$extra" ]; then
      error "Route wait entries must be either 'name url' or 'url': $line"
      exit 1
    fi

    count=$((count + 1))
    wait_for_route "$name" "$url"
  done < "$WAIT_ROUTES_FILE"

  if [ "$count" -eq 0 ]; then
    error "Route wait file has no routes: $WAIT_ROUTES_FILE"
    exit 1
  fi
}

main() {
  local up_args=()

  if [ ! -d "$WORKING_DIRECTORY" ]; then
    error "working-directory does not exist: $WORKING_DIRECTORY"
    exit 1
  fi

  case "$WAIT_STRATEGY" in
    compose-wait|services|command|routes|none)
      ;;
    *)
      error "Unsupported wait-strategy: $WAIT_STRATEGY. Use compose-wait, services, command, routes, or none."
      exit 1
      ;;
  esac

  if ! is_true_false "$DOWN_ON_COMPLETE"; then
    error "down-on-complete must be true or false."
    exit 1
  fi

  if ! is_positive_integer "$WAIT_TIMEOUT_SECONDS"; then
    error "wait-timeout-seconds must be a positive integer."
    exit 1
  fi

  if ! is_positive_integer "$WAIT_INTERVAL_SECONDS"; then
    error "wait-interval-seconds must be a positive integer."
    exit 1
  fi

  if ! command -v docker >/dev/null 2>&1; then
    error "docker is required to run compose-system-test-stack."
    exit 1
  fi

  cd "$WORKING_DIRECTORY"
  build_compose_args
  parse_services
  trap on_exit EXIT

  if [ -n "$UP_ARGS" ]; then
    read -r -a up_args <<< "$UP_ARGS"
  fi

  if [ "$WAIT_STRATEGY" = "compose-wait" ] || [ "$WAIT_STRATEGY" = "services" ]; then
    if ! has_up_arg "--wait" "${up_args[@]}"; then
      up_args+=("--wait")
    fi
  fi

  echo "==> Starting Docker Compose stack"
  stack_started=true
  run_compose up "${up_args[@]}" "${selected_services[@]}"

  case "$WAIT_STRATEGY" in
    compose-wait|services|none)
      ;;
    command)
      if [ -z "$WAIT_COMMAND" ]; then
        error "wait-command is required when wait-strategy=command."
        exit 1
      fi
      run_custom_command "Caller wait command" "$WAIT_COMMAND"
      ;;
    routes)
      wait_for_routes
      ;;
  esac

  if [ -n "$MIGRATION_CHECK_COMMAND" ]; then
    run_custom_command "Caller migration check command" "$MIGRATION_CHECK_COMMAND"
  fi
}

main "$@"
