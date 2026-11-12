#!/usr/bin/env bash
# Apply the org-standard branch ruleset (.github/rulesets/main.json) to a repo.
#
# Every JorisJonkers-dev repo must carry the common ruleset that makes the single
# "Pipeline Complete" status check required before any PR can merge. This
# script is idempotent: it creates the "Main" ruleset if absent, otherwise
# updates the existing one in place.
#
# Bypass actors are preserved: the template's bypass_actors is [], and PUT
# replaces the whole ruleset, so a naive update would wipe the GitHub Apps that
# deploy repos keep as bypass actors for pin commits pushed straight to main.
# An update keeps existing actors and adds template ones; removing any requires
# --allow-bypass-removal.
#
# Usage: scripts/apply-ruleset.sh [--allow-bypass-removal] [<owner/repo>]
#        (default repo: the current one)
set -euo pipefail

ALLOW=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --allow-bypass-removal) ALLOW=1 ;;
    -*) echo "unknown argument: $a" >&2; exit 2 ;;
    *) ARGS+=("$a") ;;
  esac
done
REPO="${ARGS[*]:0:1}"
[[ -n "$REPO" ]] || REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
RULESET_JSON="$(dirname "$0")/../.github/rulesets/main.json"
BYPASS_JQ="$(cd "$(dirname "$0")" && pwd)/ruleset_bypass.jq"

existing_id="$(gh api "/repos/$REPO/rulesets" --jq '.[] | select(.name=="Main") | .id' 2>/dev/null || true)"

if [[ -n "$existing_id" ]]; then
  echo "Updating ruleset 'Main' ($existing_id) on $REPO"
  cur="$(gh api "/repos/$REPO/rulesets/$existing_id")"
  jq -e '(.bypass_actors | type) == "array"' >/dev/null <<<"$cur" || { echo "unexpected ruleset body; refusing to write" >&2; exit 1; }
  body="$(jq --argjson cur "$cur" --argjson allow "$ALLOW" -f "$BYPASS_JQ" "$RULESET_JSON")"
  jq -r --argjson want "$(jq -c .bypass_actors <<<"$body")" '
    (.bypass_actors // [])[] as $a
    | if any($want[]; .actor_type == $a.actor_type and .actor_id == $a.actor_id) then empty
      else "BYPASS REMOVE \($a.actor_type):\($a.actor_id)" end' <<<"$cur"
  jq -r --argjson want "$(jq -c .bypass_actors <<<"$body")" '(.bypass_actors // [])[] as $a | $want[] | select(.actor_type == $a.actor_type and .actor_id == $a.actor_id and .bypass_mode != $a.bypass_mode) | "BYPASS MODE \(.actor_type):\(.actor_id) \($a.bypass_mode) -> \(.bypass_mode)"' <<<"$cur"
  jq -r '.bypass_actors[] | "bypass actor \(.actor_type):\(.actor_id) (\(.bypass_mode))"' <<<"$body"
  gh api -X PUT "/repos/$REPO/rulesets/$existing_id" --input - <<<"$body" >/dev/null
else
  echo "Creating ruleset 'Main' on $REPO"
  gh api -X POST "/repos/$REPO/rulesets" --input "$RULESET_JSON" >/dev/null
fi
echo "Done. 'Pipeline Complete' is now the required check on the default branch of $REPO."
