#!/usr/bin/env bash
# Post the report on the pull request, replacing this Project's earlier comment
# so a pull request carries one render diff per Project, not one per push.
set -euo pipefail

: "${REPOSITORY:?}" "${PULL_REQUEST:?}" "${REPORT:?}"
[ -f "$REPORT" ] || {
  echo "render-diff: no report was written; nothing to post" >&2
  exit 0
}

# The report's first line is its marker.
marker="$(head -n 1 "$REPORT")"
case "$marker" in
  "<!-- render-diff:"*" -->") ;;
  *)
    echo "render-diff: the report carries no marker" >&2
    exit 1
    ;;
esac

existing="$(gh api --paginate "repos/${REPOSITORY}/issues/${PULL_REQUEST}/comments" \
  --jq ".[] | select(.body | startswith(\"${marker}\")) | .id" | head -n 1)"

if [ -n "$existing" ]; then
  gh api --method PATCH "repos/${REPOSITORY}/issues/comments/${existing}" -F "body=@${REPORT}" >/dev/null
  echo "updated comment ${existing}"
else
  gh api --method POST "repos/${REPOSITORY}/issues/${PULL_REQUEST}/comments" -F "body=@${REPORT}" >/dev/null
  echo "posted a new comment"
fi
