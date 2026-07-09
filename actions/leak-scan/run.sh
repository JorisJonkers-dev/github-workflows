#!/usr/bin/env bash
# actions/leak-scan/run.sh
# Detect sensitive data leaks using gitleaks, trufflehog, and the SC-8 deny-list.
# All inputs arrive via environment variables set by action.yml.
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

fail() {
  printf '::error::%s\n' "$*" >&2
  exit 1
}

emit_gate_summary() {
  local gate="$1"
  local check_name="$2"
  local status="$3"
  local reason="$4"
  local actor_decision="$5"
  local redacted=false
  shift 5
  for flag in "$@"; do
    if [[ "$flag" == "--redacted" ]]; then
      redacted=true
    fi
  done

  # SC-4: never list raw match content in gate-summary.json
  cat > gate-summary.json <<EOF
{
  "gate": "${gate}",
  "check_name": "${check_name}",
  "status": "${status}",
  "reason": "${reason}",
  "flaky_candidates": [],
  "actor_decision": "${actor_decision}",
  "redacted": ${redacted}
}
EOF
}

# ---------------------------------------------------------------------------
# Path deny-list scan
# Reads SC-8 patterns from GW_ROOT/data/leak-patterns.json.
# Sets the module-level variable path_scan_status ("pass" or "fail").
# Never prints matching content — only file names (redacted).
# ---------------------------------------------------------------------------

path_scan_status="pass"

run_path_deny_list_scan() {
  local patterns_file="${GW_ROOT}/data/leak-patterns.json"
  [[ -f "$patterns_file" ]] || fail "E_MISSING_LEAK_PATTERNS: ${patterns_file} not found"

  # Load category names for this mode
  local mode_categories
  mode_categories=$(jq -r --arg mode "$deny_list" '.modes[$mode] // empty | .[]' "$patterns_file")
  if [[ -z "$mode_categories" ]]; then
    fail "E_UNKNOWN_DENY_LIST_MODE: ${deny_list}"
  fi

  # Build combined pattern list from all categories
  local -a all_patterns=()
  while IFS= read -r category; do
    while IFS= read -r pattern; do
      all_patterns+=("$pattern")
    done < <(jq -r --arg cat "$category" '.categories[$cat].patterns[]' "$patterns_file")
  done <<< "$mode_categories"

  # Extra path scope for this mode
  local extra_path_scope
  extra_path_scope=$(jq -r --arg mode "$deny_list" '.extra_paths[$mode] // empty | .[]' "$patterns_file" 2>/dev/null || true)

  path_scan_status="pass"

  # Build the list of paths to scan
  local -a scan_paths=()
  for p in $PATHS; do
    scan_paths+=("$p")
  done
  for p in $extra_path_scope; do
    # Glob-expand extra paths
    # shellcheck disable=SC2086
    for expanded in $p; do
      [[ -e "$expanded" ]] && scan_paths+=("$expanded")
    done
  done

  for scan_path in "${scan_paths[@]}"; do
    [[ -e "$scan_path" ]] || continue
    for pattern in "${all_patterns[@]}"; do
      # grep: never print matching content, only file list
      local matches
      matches=$(grep -rn \
        --include='*.yaml' \
        --include='*.yml' \
        --include='*.json' \
        --include='*.sh' \
        --include='*.md' \
        -l -E "$pattern" \
        "$scan_path" 2>/dev/null || true)
      if [[ -n "$matches" ]]; then
        path_scan_status="fail"
        # Emit REDACTED: never log raw match content
        printf '::warning::leak-scan: pattern match found (redacted) in %s paths\n' \
          "$(printf '%s\n' "$matches" | wc -l)"
      fi
    done
  done

  local reason="path-deny-list-${path_scan_status}"
  if [[ "$path_scan_status" == "fail" ]]; then
    reason="pattern-match-found-REDACTED"
  fi
  emit_gate_summary "leak-scan" "Leak Scan" "$path_scan_status" "$reason" "none" --redacted
}

# ---------------------------------------------------------------------------
# Scan modes
# ---------------------------------------------------------------------------

run_all_refs_scan() {
  local gitleaks_exit=0
  local trufflehog_exit=0

  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect \
      --source=. \
      --all \
      --redact \
      --report-path=gitleaks-report.json \
      2>&1 || gitleaks_exit=$?
  else
    printf '::warning::gitleaks not found; skipping gitleaks all-refs scan\n'
  fi

  if command -v trufflehog >/dev/null 2>&1; then
    trufflehog filesystem \
      --directory=. \
      --json \
      --no-verification \
      > trufflehog-report.json \
      2>&1 || trufflehog_exit=$?
  else
    printf '::warning::trufflehog not found; skipping trufflehog scan\n'
  fi

  # Always run path deny-list on full tree
  PATHS="."
  run_path_deny_list_scan

  local overall_status="pass"
  [[ $gitleaks_exit -ne 0 ]] && overall_status="fail"
  [[ $trufflehog_exit -ne 0 ]] && overall_status="fail"
  [[ "$path_scan_status" != "pass" ]] && overall_status="fail"

  emit_gate_summary "leak-scan" "Leak Scan" "$overall_status" \
    "all-refs-scan-complete" "none" --redacted

  if [[ "$overall_status" != "pass" ]]; then
    exit 1
  fi
}

run_pr_diff_scan() {
  [[ -n "$BASE_REF" && -n "$HEAD_REF" ]] \
    || fail "E_MISSING_REFS: pr-diff mode requires base-ref and head-ref"

  local changed_files
  changed_files=$(git diff --name-only "$BASE_REF" "$HEAD_REF" 2>/dev/null || echo "")

  if [[ -z "$changed_files" ]]; then
    emit_gate_summary "leak-scan" "Leak Scan" "pass" "no-changed-files" "none" --redacted
    return 0
  fi

  # Apply EXCLUDE_PATHS: filter out files whose paths start with any excluded prefix.
  # EXCLUDE_PATHS is a space-separated list of path prefixes (e.g. ".internal-context/").
  local filtered_files="$changed_files"
  if [[ -n "${EXCLUDE_PATHS:-}" ]]; then
    for excl_prefix in $EXCLUDE_PATHS; do
      filtered_files=$(printf '%s\n' "$filtered_files" \
        | grep -v "^${excl_prefix}" || true)
    done
    local excluded_count
    excluded_count=$(( $(printf '%s\n' "$changed_files" | grep -c .) - $(printf '%s\n' "$filtered_files" | grep -c . || echo 0) ))
    if [[ $excluded_count -gt 0 ]]; then
      printf '::notice::leak-scan: excluded %d path(s) matching EXCLUDE_PATHS prefixes\n' \
        "$excluded_count"
    fi
    changed_files="$filtered_files"
  fi

  if [[ -z "$changed_files" ]]; then
    emit_gate_summary "leak-scan" "Leak Scan" "pass" "no-changed-files-after-exclusion" "none" --redacted
    return 0
  fi

  printf '%s\n' "$changed_files" > changed-files.txt

  local gitleaks_exit=0
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect \
      --source=. \
      --files-at-commit="$HEAD_REF" \
      --include-paths=changed-files.txt \
      --redact \
      --report-path=gitleaks-diff-report.json \
      2>&1 || gitleaks_exit=$?
  else
    printf '::warning::gitleaks not found; skipping gitleaks pr-diff scan\n'
  fi

  # Run path deny-list on changed files
  PATHS="$(printf '%s\n' "$changed_files" | tr '\n' ' ')"
  run_path_deny_list_scan

  local overall_status="pass"
  [[ $gitleaks_exit -ne 0 ]] && overall_status="fail"
  [[ "$path_scan_status" != "pass" ]] && overall_status="fail"

  emit_gate_summary "leak-scan" "Leak Scan" "$overall_status" \
    "pr-diff-scan-complete" "none" --redacted

  if [[ "$overall_status" != "pass" ]]; then
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

main() {
  local mode="${MODE:?MODE is required}"
  local deny_list="${DENY_LIST:-default}"

  # Export deny_list for run_path_deny_list_scan to read
  # (bash functions inherit the calling scope's local vars via dynamic scope
  # only when called in the same function; use a module-level variable here)
  export deny_list

  case "$mode" in
    all-refs)
      run_all_refs_scan
      ;;
    pr-diff)
      run_pr_diff_scan
      ;;
    path)
      [[ -n "${PATHS:-}" ]] \
        || fail "E_MISSING_PATHS: path mode requires paths input"
      run_path_deny_list_scan
      if [[ "$path_scan_status" != "pass" ]]; then
        exit 1
      fi
      ;;
    *)
      fail "E_UNKNOWN_LEAK_SCAN_MODE: ${mode} (allowed: all-refs|pr-diff|path)"
      ;;
  esac
}

main "$@"
