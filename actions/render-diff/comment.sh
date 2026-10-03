#!/usr/bin/env bash
# Post the report on the pull request, replacing this Project's earlier comment
# so a pull request carries one render diff per Project, not one per push.
set -euo pipefail

: "${REPOSITORY:?}" "${PULL_REQUEST:?}" "${REPORT:?}"
[ -f "$REPORT" ] || {
  echo "render-diff: no report was written; nothing to post" >&2
  exit 0
}

# The report's first line is its marker. It names a Project, and nothing else:
# it selects which comment is replaced, so it is never taken as written.
marker="$(head -n 1 "$REPORT")"
[[ "$marker" =~ ^\<!--\ render-diff:[a-z0-9]([a-z0-9-]*[a-z0-9])?\ --\>$ ]] || {
  echo "render-diff: the report carries no marker" >&2
  exit 1
}
[[ "$PULL_REQUEST" =~ ^[0-9]+$ ]] || {
  echo "render-diff: '${PULL_REQUEST}' is not a pull request number" >&2
  exit 1
}

# Only a comment this action wrote is ever replaced: the marker is handed to jq
# as a value, and the comment must open with it.
existing="$(gh api --paginate "repos/${REPOSITORY}/issues/${PULL_REQUEST}/comments" |
  jq -r --arg marker "$marker" '.[] | select(.body | startswith($marker + "\n")) | .id' | head -n 1)"
[[ "$existing" =~ ^[0-9]*$ ]] || {
  echo "render-diff: the comment listing returned an id that is not one" >&2
  exit 1
}

if [ -n "$existing" ]; then
  gh api --method PATCH "repos/${REPOSITORY}/issues/comments/${existing}" -F "body=@${REPORT}" >/dev/null
  echo "updated comment ${existing}"
else
  gh api --method POST "repos/${REPOSITORY}/issues/${PULL_REQUEST}/comments" -F "body=@${REPORT}" >/dev/null
  echo "posted a new comment"
fi
