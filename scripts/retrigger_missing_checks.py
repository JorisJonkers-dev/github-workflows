#!/usr/bin/env python3
"""Close and reopen open pull requests whose head commit lacks a required check.

The organization's rulesets require status checks. When a required check is added,
or a workflow run is lost, an open pull request's head commit never gets that check
and GitHub waits for it forever. Rerunning the old run does not help, because it
reuses the old workflow file. Closing and reopening the pull request does: the
reopened event starts fresh runs from the current workflows.

For every non-archived repository and every open pull request this tool skips

- drafts,
- pull requests opened by a bot or app (user type Bot, or a login ending in [bot]),
- pull requests with merge conflicts (mergeable is false: no merge ref, so no run
  would start anyway).

It reads the required status check contexts that apply to the base branch from the
branch rules, and the head commit's check runs and commit statuses. A context is
present when a check run with that name or a status with that context exists, in
any state. When a context is missing and the head commit is old enough, the pull
request is closed and reopened, once per head commit: a reopened event after the
head commit's date means the tool already tried, so a check that can never report
does not cause a loop.

    retrigger_missing_checks.py --org ORG [--min-age-minutes N] [--dry-run]

The token comes from GITHUB_TOKEN and needs pull request write access. The exit
code is 0 unless the API fails (1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

API = "https://api.github.com"
PER_PAGE = 100
# A bound on pages per listing, so an API that never returns a short page cannot grow memory
# without end. 50 pages of 100 is far above any listing this org has.
MAX_PAGES = 50
DEFAULT_MIN_AGE_MINUTES = 30

Request = Callable[..., Any]


class ApiError(Exception):
    """A GitHub API call failed."""


def make_http_request(token: str) -> Request:
    def request(method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "retrigger-missing-checks",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            raise ApiError(f"{method} {path}: HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ApiError(f"{method} {path}: {error.reason}") from error

    return request


def paginate(request: Request, path: str, key: str | None = None, params: dict | None = None) -> list:
    items: list = []
    for page in range(1, MAX_PAGES + 1):
        result = request("GET", path, {**(params or {}), "per_page": PER_PAGE, "page": page})
        batch = result[key] if key else result
        items.extend(batch)
        if len(batch) < PER_PAGE:
            return items
    raise ApiError(f"GET {path}: more than {MAX_PAGES} pages")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_bot(user: dict) -> bool:
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")


def required_contexts(request: Request, slug: str, branch: str) -> list[str]:
    rules = paginate(request, f"/repos/{slug}/rules/branches/{urllib.parse.quote(branch, safe='')}")
    contexts: list[str] = []
    for rule in rules:
        if rule.get("type") == "required_status_checks":
            for check in rule["parameters"]["required_status_checks"]:
                if check["context"] not in contexts:
                    contexts.append(check["context"])
    return contexts


def reported_contexts(request: Request, slug: str, sha: str) -> set[str]:
    runs = paginate(request, f"/repos/{slug}/commits/{sha}/check-runs", "check_runs")
    statuses = paginate(request, f"/repos/{slug}/commits/{sha}/status", "statuses")
    return {run["name"] for run in runs} | {status["context"] for status in statuses}


def sweep_pull(request: Request, slug: str, pull: dict, min_age: timedelta, now: datetime,
               dry_run: bool, out: Callable[[str], Any]) -> None:
    if pull.get("draft") or is_bot(pull["user"]):
        return
    number = pull["number"]
    if request("GET", f"/repos/{slug}/pulls/{number}").get("mergeable") is False:
        return
    required = required_contexts(request, slug, pull["base"]["ref"])
    if not required:
        return
    sha = pull["head"]["sha"]
    missing = [context for context in required if context not in reported_contexts(request, slug, sha)]
    if not missing:
        return
    label = f"{slug}#{number} missing {', '.join(missing)}"
    commit_date = parse_time(request("GET", f"/repos/{slug}/commits/{sha}")["commit"]["committer"]["date"])
    if now - commit_date < min_age:
        out(f"{label}: head commit too young, skipped\n")
        return
    events = paginate(request, f"/repos/{slug}/issues/{number}/events")
    if any(e["event"] == "reopened" and parse_time(e["created_at"]) > commit_date for e in events):
        out(f"{label}: already reopened since the head commit, skipped\n")
        return
    if dry_run:
        out(f"{label}: would close and reopen\n")
        return
    request("PATCH", f"/repos/{slug}/pulls/{number}", body={"state": "closed"})
    request("PATCH", f"/repos/{slug}/pulls/{number}", body={"state": "open"})
    out(f"{label}: closed and reopened\n")


def sweep(request: Request, org: str, min_age: timedelta, now: datetime, dry_run: bool,
          out: Callable[[str], Any]) -> None:
    for repo in paginate(request, f"/orgs/{org}/repos", params={"type": "all"}):
        if repo["archived"]:
            continue
        slug = f"{org}/{repo['name']}"
        for pull in paginate(request, f"/repos/{slug}/pulls", params={"state": "open"}):
            sweep_pull(request, slug, pull, min_age, now, dry_run, out)


def main(argv: list[str] | None = None, request: Request | None = None,
         now: datetime | None = None, out: Callable[[str], Any] = sys.stdout.write) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", required=True)
    parser.add_argument("--min-age-minutes", type=int, default=DEFAULT_MIN_AGE_MINUTES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if request is None:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            out("GITHUB_TOKEN is not set\n")
            return 1
        request = make_http_request(token)
    try:
        sweep(request, args.org, timedelta(minutes=args.min_age_minutes),
              now or datetime.now(timezone.utc), args.dry_run, out)
    except ApiError as error:
        out(f"API failure: {error}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
