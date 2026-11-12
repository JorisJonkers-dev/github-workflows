#!/usr/bin/env bash
# Apply the org-standard "Main" ruleset (.github/rulesets/main.json) to every
# unarchived, non-fork repository of the org. Dry-run unless --apply is given.
#
#   scripts/apply-rulesets-org.sh                       dry-run, whole org
#   scripts/apply-rulesets-org.sh --repo fleet-infra    dry-run, one repo (repeatable)
#   scripts/apply-rulesets-org.sh --apply               create/update for real
#
# A required status check that never reports blocks every pull request, so the
# template is adjusted per repository before it is applied:
#
#  * "Pipeline Complete" is required only where the default branch's latest
#    commit already reports it; otherwise the repository is skipped.
#  * "repository-hygiene / Agent Attribution Guard" is required only where a
#    workflow calls repository-hygiene-guard.yml from a job named
#    "repository-hygiene" at github-workflows >= v0.46.0 (the release that added
#    the guard). Elsewhere it is dropped from that repository's ruleset, and the
#    repository is reported as needing a workflow bump.
#
# Idempotent: the ruleset is found by name; PUT when present, POST when absent.
# Repos whose ruleset already matches are reported as "unchanged" and not touched.
set -euo pipefail

OWNER="JorisJonkers-dev"
MIN_TAG="v0.46.0"
GUARD_CHECK="repository-hygiene / Agent Attribution Guard"
BASE_CHECK="Pipeline Complete"
TEMPLATE="$(cd "$(dirname "$0")/.." && pwd)/.github/rulesets/main.json"
APPLY=0
ONLY=()

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    --repo) ONLY+=("${2:?--repo needs a name}"); shift ;;
    --owner) OWNER="${2:?}"; shift ;;
    --template) TEMPLATE="${2:?}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

ORG_WORKFLOWS="$OWNER/github-workflows"
[[ "${OWNER,,}" == "nedap" ]] && { echo "refusing to touch nedap" >&2; exit 2; }
[[ -f "$TEMPLATE" ]] || { echo "template not found: $TEMPLATE" >&2; exit 2; }

# jq library: project an existing ruleset onto the shape of the desired one, so
# fields the API fills in with defaults do not show up as a difference, and sort
# arrays (rules by type, checks by context) so ordering never does either.
JQ_LIB='
def matches($e): ($e|type) == "object" and ((.type? != null and .type == $e.type) or (.context? != null and .context == $e.context));
def proj($d):
  if ($d|type) == "object" and type == "object" then
    with_entries(select(.key as $k | $d | has($k)) | .key as $k | .value |= proj($d[$k]))
  elif ($d|type) == "array" and type == "array" then
    map(. as $e | ([$d[] | select(matches($e))] | first) as $m
        | if $m == null then $e else ($e | proj($m)) end)
  else . end;
def srt: if type == "array" then map(srt) | sort_by(.type? // .context? // tostring)
         elif type == "object" then with_entries(.value |= srt) else . end;
def shape: {name,target,enforcement,conditions,rules,bypass_actors};
'

want_json() { # $1 = include guard (1/0)
  jq --arg guard "$GUARD_CHECK" --argjson inc "$1" '
    (.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks) |=
      map(select($inc == 1 or .context != $guard))' "$TEMPLATE"
}

# Prints the version-ok verdict for the repo's hygiene caller: "ok", or a reason.
list_workflows() {
  local out
  out="$(gh api "repos/$OWNER/$repo/contents/.github/workflows" --jq '.[].name' 2>&1)" && { grep -E '\.ya?ml$' <<<"$out" || true; return; }
  grep -q 'Not Found' <<<"$out" && return 0   # no workflows directory
  echo "list-failed" ; return 0
}
guard_status() {
  local repo="$1" f body line ref job found=0 reason="no workflow calls repository-hygiene-guard.yml"
  while IFS= read -r f; do
    [[ "$f" == list-failed ]] && { echo "unknown: cannot list workflows"; return; }
    body="$(gh api "repos/$OWNER/$repo/contents/.github/workflows/$f" -H 'Accept: application/vnd.github.raw' 2>/dev/null | grep -v '^[[:space:]]*#' || true)"
    grep -q 'repository-hygiene-guard\.yml@' <<<"$body" || continue
    found=1
    line="$(grep -n 'repository-hygiene-guard\.yml@' <<<"$body" | head -1)"
    ref="$(sed -E "s/.*repository-hygiene-guard\.yml@([^'\" #]+).*/\1/" <<<"${line#*:}")"
    job="$(head -n "${line%%:*}" <<<"$body" | grep -E "^  ['\"]?[A-Za-z0-9_-]+['\"]?:[[:space:]]*$" | tail -1 | tr -d " '\":")"
    if [[ "$job" != "repository-hygiene" ]]; then
      reason="$f calls the guard from job '$job', so the check would be named differently"; continue
    fi
    if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
      st="$(gh api "repos/$ORG_WORKFLOWS/compare/$MIN_TAG...$ref" --jq .status 2>/dev/null || echo unknown)"
      [[ "$st" == ahead || "$st" == identical ]] && { echo ok; return; }
      [[ "$st" == unknown ]] && { echo "unknown: could not compare $ref"; return; }
      reason="$f pins $ref, older than $MIN_TAG"
    else
      if [[ "$(printf '%s\n%s\n' "$MIN_TAG" "$ref" | sort -V | head -1)" == "$MIN_TAG" ]]; then echo ok; return; fi
      reason="$f pins $ref, older than $MIN_TAG"
    fi
  done < <(list_workflows "$repo")
  echo "$reason"
}

reports_base_check() { # any of the last 10 commits on the branch reported it (CI may skip bot commits)
  local repo="$1" branch="$2" sha names
  for sha in $(gh api "repos/$OWNER/$repo/commits?sha=$branch&per_page=10" --jq '.[].sha' 2>/dev/null); do
    names="$(gh api "repos/$OWNER/$repo/commits/$sha/check-runs?per_page=100" --jq '.check_runs[]|select(.app.id==15368)|.name' 2>/dev/null || true)"
    grep -qx "$BASE_CHECK" <<<"$names" && return 0
  done
  return 1
}

if ((${#ONLY[@]})); then
  repos=("${ONLY[@]}")
else
  list="$(gh repo list "$OWNER" --no-archived --source -L 1000 --json name -q '.[].name')" || { echo "cannot list repos" >&2; exit 1; }
  mapfile -t repos < <(sort <<<"$list")
  ((${#repos[@]} < 1000)) || { echo "repo list may be truncated" >&2; exit 1; }
fi

declare -A SUMMARY
FAILED=0; rc=0
mode="dry-run"; ((APPLY)) && mode="apply"
echo "== $OWNER rulesets ($mode), template $TEMPLATE"

for repo in "${repos[@]}"; do
  meta="$(gh api "repos/$OWNER/$repo" --jq '[.archived,.fork,.default_branch]|@tsv' 2>/dev/null || true)"
  [[ -n "$meta" ]] || { echo "-- $repo: SKIP (not found)"; SUMMARY[$repo]="skip: not found"; continue; }
  IFS=$'\t' read -r archived fork branch <<<"$meta"
  if [[ "$archived" == true || "$fork" == true ]]; then
    echo "-- $repo: SKIP ($([[ $archived == true ]] && echo archived || echo fork))"; SUMMARY[$repo]="skip: archived or fork"; continue
  fi
  if [[ "$branch" != main ]]; then
    echo "-- $repo: SKIP (default branch is '$branch', the ruleset targets refs/heads/main)"; SUMMARY[$repo]="skip: default branch $branch"; continue
  fi
  if ! reports_base_check "$repo" "$branch"; then
    echo "-- $repo: SKIP (latest commit on $branch reports no '$BASE_CHECK' check; requiring it would block every PR)"
    SUMMARY[$repo]="skip: no '$BASE_CHECK' check"; continue
  fi

  verdict="$(guard_status "$repo")"
  if [[ "$verdict" == unknown* || "$verdict" == *list-failed* ]]; then
    echo "-- $repo: SKIP (cannot determine guard status: $verdict)"; SUMMARY[$repo]="skip: guard status unknown"; continue
  fi
  if [[ "$verdict" == ok ]]; then inc=1; note="attribution guard required"
  else inc=0; note="attribution guard NOT required: $verdict"; fi

  if ! rulesets="$(gh api --paginate "repos/$OWNER/$repo/rulesets?includes_parents=false" --jq '.[]' 2>&1 | jq -s '.' 2>&1)"; then
    echo "-- $repo: SKIP (cannot read rulesets: ${rulesets//$'\n'/ })"; SUMMARY[$repo]="skip: rulesets unavailable"; continue
  fi
  id="$(jq -r '[.[] | select(.name=="Main" and .source_type=="Repository")][0].id // empty' <<<"$rulesets")"
  want="$(want_json "$inc")"
  wantn="$(jq -S "$JQ_LIB shape | srt" <<<"$want")"

  if [[ -n "$id" ]]; then
    have="$(gh api "repos/$OWNER/$repo/rulesets/$id" | jq -S --argjson d "$want" "$JQ_LIB shape | proj(\$d) | srt")" || { echo "-- $repo: SKIP (cannot read ruleset $id)"; SUMMARY[$repo]="skip: ruleset unreadable"; continue; }
    if [[ "$have" == "$wantn" ]]; then
      echo "-- $repo: UNCHANGED ($note)"; SUMMARY[$repo]="unchanged; $note"; continue
    fi
    action=update
  else
    have='{}'; action=create
  fi
  echo "-- $repo: ${action^^} ($note)"
  diff -u --label "$repo (current)" --label "$repo (desired)" <(jq -S . <<<"$have") <(jq -S . <<<"$wantn") | sed 's/^/   /' || true
  SUMMARY[$repo]="$action; $note"
  if ((APPLY)); then
    if [[ $action == update ]]; then
      gh api -X PUT "repos/$OWNER/$repo/rulesets/$id" --input - <<<"$want" >/dev/null || rc=1
    else
      gh api -X POST "repos/$OWNER/$repo/rulesets" --input - <<<"$want" >/dev/null || rc=1
    fi
    if [[ ${rc:-0} == 1 ]]; then echo "   FAILED"; SUMMARY[$repo]="FAILED $action"; FAILED=1; rc=0; else echo "   applied"; fi
  fi
done

echo
echo "== summary ($mode)"
for repo in "${repos[@]}"; do printf '%-34s %s\n' "$repo" "${SUMMARY[$repo]:-}"; done
((FAILED)) && { echo "some repos FAILED; see above" >&2; exit 1; }
((APPLY)) || echo "dry-run: nothing was changed. Re-run with --apply to write."
