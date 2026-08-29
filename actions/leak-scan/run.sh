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

# Scan only the lines a change adds, rather than whole files.
#
# The whole-file scan is right for all-refs and path modes, where the question is
# "does this tree contain an address". It is the wrong question for pr-diff,
# where what matters is whether this change introduces one. A private deploy
# repository legitimately contains node addresses and subnet literals — one such
# repository has them in 24 of 388 tracked files — so a whole-file scan there
# fails on the content the repository exists to hold, and blocks every edit to
# those files including edits that remove an address.
#
# Pre-existing content is not left unguarded: all-refs mode still reads whole
# files across every ref, which is the mode built for that question.
# Emit the added lines of a unified diff read on stdin, dropping any whose file
# path starts with one of the space-separated prefixes in $1.
#
# EXCLUDE_PATHS is documented as "Only effective in pr-diff mode", but it was
# only ever wired into the path scan -- the pr-diff deny-list ignored it
# entirely, so callers passing exclude-paths to suppress a known false-positive
# class silently got no exclusion at all.
#
# Path prefixes are matched as literal string prefixes, matching the path scan's
# documented semantics. substr is used rather than $2 so paths containing spaces
# are handled.
filter_added_lines() {
  local excludes="$1"
  awk -v excludes="$excludes" '
    BEGIN { n = split(excludes, ex, " "); skip = 0 }
    /^\+\+\+ / {
      path = substr($0, 5)
      sub(/^b\//, "", path)
      skip = 0
      for (i = 1; i <= n; i++) {
        if (ex[i] != "" && index(path, ex[i]) == 1) { skip = 1; break }
      }
      next
    }
    /^\+/ { if (!skip) print substr($0, 2) }
  '
}

run_diff_deny_list_scan() {
  local patterns_file="${GW_ROOT}/data/leak-patterns.json"
  [[ -f "$patterns_file" ]] || fail "E_MISSING_LEAK_PATTERNS: ${patterns_file} not found"

  local mode_categories
  mode_categories=$(jq -r --arg mode "$deny_list" '.modes[$mode] // empty | .[]' "$patterns_file")
  [[ -n "$mode_categories" ]] || fail "E_UNKNOWN_DENY_LIST_MODE: ${deny_list}"

  local -a all_patterns=()
  while IFS= read -r category; do
    while IFS= read -r pattern; do
      all_patterns+=("$pattern")
    done < <(jq -r --arg cat "$category" '.categories[$cat].patterns[]' "$patterns_file")
  done <<< "$mode_categories"

  path_scan_status="pass"

  # Added lines only, with the leading marker stripped so a pattern anchored at
  # line start still behaves. --unified=0 keeps context lines out.
  # EXCLUDE_PATHS is applied here, per its documented "pr-diff mode" contract.
  local added all_added
  all_added=$(git diff --unified=0 "$BASE_REF" "$HEAD_REF" -- $PATHS 2>/dev/null \
    | filter_added_lines "" || true)
  added=$(git diff --unified=0 "$BASE_REF" "$HEAD_REF" -- $PATHS 2>/dev/null \
    | filter_added_lines "${EXCLUDE_PATHS:-}" || true)

  if [[ -n "${EXCLUDE_PATHS:-}" ]]; then
    local before after excluded
    before=$(printf '%s\n' "$all_added" | grep -c . || true)
    after=$(printf '%s\n' "$added" | grep -c . || true)
    excluded=$(( before - after ))
    if [[ "$excluded" -gt 0 ]]; then
      printf '::notice::leak-scan: excluded %d added line(s) matching EXCLUDE_PATHS prefixes\n' \
        "$excluded"
    fi
  fi

  if [[ -z "$added" ]]; then
    printf '::notice::leak-scan: no added lines in scope\n'
    emit_gate_summary "leak-scan" "Leak Scan" "pass" "no-added-lines" "none" --redacted
    return 0
  fi

  local matched=0
  for pattern in "${all_patterns[@]}"; do
    local count
    # Count only; matching content is never printed.
    # -e is required, not stylistic: a pattern beginning with '-'
    # (-----BEGIN .* PRIVATE KEY-----) is otherwise parsed as an option and
    # grep exits with a usage error, so the private-key rule never fired.
    count=$(printf '%s\n' "$added" | grep -c -E -e "$pattern" || true)
    [[ "$count" -gt 0 ]] && { path_scan_status="fail"; matched=$((matched + count)); }
  done

  if [[ "$path_scan_status" != "pass" ]]; then
    printf '::warning::leak-scan: %d added line(s) match the deny-list (redacted)\n' "$matched"
  fi

  local reason="diff-deny-list-${path_scan_status}"
  [[ "$path_scan_status" == "fail" ]] && reason="pattern-match-in-added-lines-REDACTED"
  emit_gate_summary "leak-scan" "Leak Scan" "$path_scan_status" "$reason" "none" --redacted
}

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
      # Exclude the action's own checkout. The consumer workflow clones
      # github-workflows into .github-workflows/ at the repo root, and PATHS is
      # usually ".", so the tree being scanned contains data/leak-patterns.json -
      # the deny-list patterns themselves, as literal JSON strings. Without this
      # the scan matches its own configuration and can never pass. Exclude the
      # directory, never weaken the patterns.
      matches=$(grep -rn \
        --exclude-dir='.github-workflows' \
        --include='*.yaml' \
        --include='*.yml' \
        --include='*.json' \
        --include='*.sh' \
        --include='*.md' \
        -l -E -e "$pattern" \
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

  # Fail-closed: all-refs is a pre-flip prerequisite; gitleaks must be
  # available.  The action.yml install step runs first; if it succeeded,
  # gitleaks will be on PATH.  If it is still absent (install failed or the
  # action was bypassed), the job must fail loudly — not warn-and-degrade.
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect \
      --source=. \
      --log-opts="--all" \
      --redact \
      --report-path=gitleaks-report.json \
      2>&1 || gitleaks_exit=$?
  else
    emit_gate_summary "leak-scan" "Leak Scan" "fail" \
      "E_GITLEAKS_MISSING: gitleaks not found after install step; all-refs scan cannot proceed" \
      "none" --redacted
    fail "E_GITLEAKS_MISSING: gitleaks not found; all-refs scan requires gitleaks to be installed"
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
    # `grep -c .` prints 0 AND exits 1 on no match, so `|| echo 0` appended a
    # second "0" and the arithmetic saw "1 - 0\n0" -- a syntax error that killed
    # the scan whenever EXCLUDE_PATHS excluded every changed file. Capture the
    # counts first and let `|| true` absorb the exit status.
    local before_count after_count excluded_count
    before_count=$(printf '%s\n' "$changed_files" | grep -c . || true)
    after_count=$(printf '%s\n' "$filtered_files" | grep -c . || true)
    excluded_count=$(( before_count - after_count ))
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

  # Build a temporary gitleaks config that extends the default rules and adds
  # allowlist.paths entries for any EXCLUDE_PATHS prefixes.  gitleaks 8.x does
  # not support path filtering flags, so path exclusion must be expressed via the
  # config allowlist.  The temp config is cleaned up after the scan.
  local gitleaks_config_flag=()
  local gitleaks_tmp_config=""
  local regex_prefix
  if [[ -n "${EXCLUDE_PATHS:-}" ]]; then
    gitleaks_tmp_config=$(mktemp --suffix=.toml)
    {
      printf '[extend]\nuseDefault = true\n\n[allowlist]\npaths = [\n'
      for excl_prefix in $EXCLUDE_PATHS; do
        # Escape the prefix as a regex: anchor with ^ and escape dots/special chars.
        regex_prefix=$(printf '%s' "$excl_prefix" | sed 's/\./\\./g')
        printf "  '^%s',\n" "$regex_prefix"
      done
      printf ']\n'
    } > "$gitleaks_tmp_config"
    gitleaks_config_flag=(--config="$gitleaks_tmp_config")
  fi

  local gitleaks_exit=0
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks git \
      --log-opts="${BASE_REF}..${HEAD_REF}" \
      "${gitleaks_config_flag[@]}" \
      --redact \
      --report-path=gitleaks-diff-report.json \
      . \
      2>&1 || gitleaks_exit=$?
  else
    printf '::warning::gitleaks not found; skipping gitleaks pr-diff scan\n'
  fi
  [[ -n "$gitleaks_tmp_config" ]] && rm -f "$gitleaks_tmp_config"

  # Deny-list over the lines this change adds, not over whole files.
  PATHS="$(printf '%s\n' "$changed_files" | tr '\n' ' ')"
  run_diff_deny_list_scan

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
