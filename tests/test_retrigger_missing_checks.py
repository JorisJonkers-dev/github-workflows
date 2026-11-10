from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from scripts import retrigger_missing_checks as tool

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
YOUNG = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
LATER = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
EARLIER = (NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fake:
    """Routes keyed by path. A route is a value, a list of pages, or a callable(params)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if method != "GET":
            return {}
        route = self.routes[path]
        if callable(route):
            return route(params or {})
        if (params or {}).get("page", 1) > 1:
            # A fixed route is a single page: later pages are empty.
            if isinstance(route, dict):
                return {k: ([] if isinstance(v, list) else v) for k, v in route.items()}
            return []
        return route

    def writes(self):
        return [(m, p, b) for m, p, _, b in self.calls if m != "GET"]


def pr(number=1, draft=False, login="alice", user_type="User", sha="abc", base="main"):
    return {
        "number": number,
        "draft": draft,
        "user": {"login": login, "type": user_type},
        "base": {"ref": base},
        "head": {"sha": sha},
    }


def world(prs=None, mergeable=True, required=("Pipeline Complete",), runs=(), statuses=(),
          commit_date=OLD, events=(), repos=None):
    prs = [pr()] if prs is None else prs
    repos = repos if repos is not None else [{"name": "r", "archived": False}]
    rules = [{"type": "deletion"}]
    if required is not None:
        rules.append({
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": c} for c in required]},
        })
    return Fake({
        "/orgs/o/repos": repos,
        "/repos/o/r/pulls": prs,
        "/repos/o/r/pulls/1": {"mergeable": mergeable},
        "/repos/o/r/rules/branches/main": rules,
        "/repos/o/r/commits/abc": {"commit": {"committer": {"date": commit_date}}},
        "/repos/o/r/commits/abc/check-runs": {"check_runs": [{"name": n} for n in runs]},
        "/repos/o/r/commits/abc/status": {"statuses": [{"context": c} for c in statuses]},
        "/repos/o/r/issues/1/events": [{"event": e, "created_at": t} for e, t in events],
    })


def run(fake, *argv):
    out = io.StringIO()
    code = tool.main(["--org", "o", *argv], request=fake, now=NOW, out=out.write)
    return code, out.getvalue()


class RetriggerTests(unittest.TestCase):
    def test_missing_context_closes_and_reopens(self):
        fake = world()
        code, out = run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.writes(), [
            ("PATCH", "/repos/o/r/pulls/1", {"state": "closed"}),
            ("PATCH", "/repos/o/r/pulls/1", {"state": "open"}),
        ])
        self.assertIn("o/r#1", out)
        self.assertIn("Pipeline Complete", out)

    def test_all_present_not_retriggered(self):
        fake = world(runs=["Pipeline Complete"])
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_status_counts_as_present(self):
        fake = world(statuses=["Pipeline Complete"])
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_no_required_checks_rule(self):
        fake = world(required=None)
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_too_young(self):
        fake = world(commit_date=YOUNG)
        code, out = run(fake)
        self.assertEqual(fake.writes(), [])
        self.assertIn("o/r#1", out)
        self.assertIn("too young", out)

    def test_min_age_option(self):
        fake = world(commit_date=YOUNG)
        run(fake, "--min-age-minutes", "1")
        self.assertEqual(len(fake.writes()), 2)

    def test_draft_skipped(self):
        fake = world(prs=[pr(draft=True)])
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_bot_user_type_skipped(self):
        fake = world(prs=[pr(user_type="Bot")])
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_bot_login_suffix_skipped(self):
        fake = world(prs=[pr(login="renovate[bot]")])
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_conflicting_skipped(self):
        fake = world(mergeable=False)
        run(fake)
        self.assertEqual(fake.writes(), [])

    def test_unknown_mergeable_not_skipped(self):
        fake = world(mergeable=None)
        run(fake)
        self.assertEqual(len(fake.writes()), 2)

    def test_already_reopened_after_head_commit(self):
        fake = world(events=[("reopened", LATER)])
        code, out = run(fake)
        self.assertEqual(fake.writes(), [])
        self.assertIn("already reopened", out)

    def test_reopened_before_head_commit_acts(self):
        fake = world(events=[("labeled", LATER), ("reopened", EARLIER)])
        run(fake)
        self.assertEqual(len(fake.writes()), 2)

    def test_check_run_pagination(self):
        def pages(params):
            names = ["a", "b", "Pipeline Complete"]
            size = tool.PER_PAGE
            start = (params["page"] - 1) * size
            return {"check_runs": [{"name": n} for n in names[start:start + size]]}

        fake = world()
        fake.routes["/repos/o/r/commits/abc/check-runs"] = pages
        with mock.patch.object(tool, "PER_PAGE", 2):
            run(fake)
        self.assertEqual(fake.writes(), [])

    def test_dry_run_acts_on_nothing(self):
        fake = world()
        code, out = run(fake, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(fake.writes(), [])
        self.assertIn("would", out)
        self.assertIn("Pipeline Complete", out)

    def test_archived_repo_skipped(self):
        fake = world(repos=[{"name": "r", "archived": True}])
        run(fake)
        self.assertNotIn(("GET", "/repos/o/r/pulls"), [(m, p) for m, p, _, _ in fake.calls])

    def test_repo_pagination(self):
        def pages(params):
            repos = [{"name": "x", "archived": True}, {"name": "r", "archived": False}]
            start = (params["page"] - 1) * tool.PER_PAGE
            return repos[start:start + tool.PER_PAGE]

        fake = world()
        fake.routes["/orgs/o/repos"] = pages
        with mock.patch.object(tool, "PER_PAGE", 1):
            run(fake)
        self.assertEqual(len(fake.writes()), 2)

    def test_a_context_required_by_two_rules_is_listed_once(self):
        rule = {"type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "Pipeline Complete"}]}}
        fake = Fake({"/repos/o/r/rules/branches/main": [rule, rule]})
        self.assertEqual(tool.required_contexts(fake, "o/r", "main"), ["Pipeline Complete"])

    def test_endless_pagination_stops_with_an_error(self):
        def always_full(method, path, params=None, body=None):
            return [{"name": "r", "archived": True}] * tool.PER_PAGE

        with mock.patch.object(tool, "PER_PAGE", 1), mock.patch.object(tool, "MAX_PAGES", 3):
            with self.assertRaises(tool.ApiError):
                tool.paginate(always_full, "/orgs/o/repos")

    def test_api_failure_exits_nonzero(self):
        def boom(method, path, params=None, body=None):
            raise tool.ApiError("boom")

        out = io.StringIO()
        code = tool.main(["--org", "o"], request=boom, now=NOW, out=out.write)
        self.assertEqual(code, 1)
        self.assertIn("boom", out.getvalue())


class HttpTests(unittest.TestCase):
    def test_http_request_builds_call_and_parses_json(self):
        seen = {}

        class Resp:
            def read(self):
                return b'{"ok": true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["data"] = req.data
            seen["auth"] = req.get_header("Authorization")
            return Resp()

        request = tool.make_http_request("tok")
        with mock.patch.object(tool.urllib.request, "urlopen", fake_urlopen):
            result = request("PATCH", "/repos/o/r/pulls/1", {"a": 1}, {"state": "open"})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen["url"], "https://api.github.com/repos/o/r/pulls/1?a=1")
        self.assertEqual(seen["method"], "PATCH")
        self.assertEqual(json.loads(seen["data"]), {"state": "open"})
        self.assertEqual(seen["auth"], "Bearer tok")

    def test_http_error_becomes_api_error(self):
        err = tool.urllib.error.HTTPError("u", 500, "bad", {}, io.BytesIO(b"x"))
        with mock.patch.object(tool.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(tool.ApiError):
                tool.make_http_request("t")("GET", "/x")

    def test_url_error_becomes_api_error(self):
        err = tool.urllib.error.URLError("down")
        with mock.patch.object(tool.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(tool.ApiError):
                tool.make_http_request("t")("GET", "/x")

    def test_missing_token_exits_nonzero(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True):
            code = tool.main(["--org", "o"], out=out.write)
        self.assertEqual(code, 1)
        self.assertIn("GITHUB_TOKEN", out.getvalue())

    def test_main_uses_env_token_and_defaults(self):
        captured = {}

        def fake_make(token):
            captured["token"] = token
            return Fake({"/orgs/o/repos": []})

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}), \
                mock.patch.object(tool, "make_http_request", fake_make), \
                contextlib.redirect_stdout(io.StringIO()):
            code = tool.main(["--org", "o"])
        self.assertEqual((code, captured["token"]), (0, "tok"))


if __name__ == "__main__":
    unittest.main()
