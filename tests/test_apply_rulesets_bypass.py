"""apply-rulesets-org.sh and apply-ruleset.sh must never wipe a repo's bypass actors.

The scripts run for real against a stub `gh` that serves one repository and records
every PUT body, so the assertions are about what would have been written.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORG = ROOT / "scripts" / "apply-rulesets-org.sh"
ONE = ROOT / "scripts" / "apply-ruleset.sh"
TEMPLATE = json.loads((ROOT / ".github" / "rulesets" / "main.json").read_text())

APP_A = {"actor_id": 5165539, "actor_type": "Integration", "bypass_mode": "always"}
APP_B = {"actor_id": 5165602, "actor_type": "Integration", "bypass_mode": "always"}
ADMIN = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}

STUB = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
state = json.load(open(os.environ["STUB_STATE"]))
log = os.environ["STUB_LOG"]
if args[:2] == ["repo", "view"]:
    print("O/r"); sys.exit(0)
method = "GET"
path = None
jq = None
i = 1
while i < len(args):
    a = args[i]
    if a == "-X": method = args[i + 1]; i += 1
    elif a == "--jq": jq = args[i + 1]; i += 1
    elif a in ("-H", "--input"): i += 1
    elif not a.startswith("-"): path = a.lstrip("/")
    i += 1
if method != "GET":
    open(log, "a").write(json.dumps({"method": method, "path": path, "body": json.load(sys.stdin)}) + "\n")
    print("{}"); sys.exit(0)
ruleset = state["ruleset"]
base = path.split("?")[0]
routes = {
    "repos/JorisJonkers-dev/r": {"archived": False, "fork": False, "default_branch": "main"},
    "repos/JorisJonkers-dev/r/commits": [{"sha": "abc"}],
    "repos/JorisJonkers-dev/r/commits/abc/check-runs": {"check_runs": [{"name": "Pipeline Complete", "app": {"id": 15368}}]},
    "repos/JorisJonkers-dev/r/contents/.github/workflows": [],
    "repos/JorisJonkers-dev/r/rulesets": [{"id": 7, "name": "Main", "source_type": "Repository"}] if ruleset else [],
    "repos/JorisJonkers-dev/r/rulesets/7": ruleset,
    "repos/O/r/rulesets": [{"id": 7, "name": "Main"}] if ruleset else [],
    "repos/O/r/rulesets/7": ruleset,
}
if base not in routes:
    sys.stderr.write("stub: unrouted " + str(args) + "\n"); sys.exit(1)
doc = json.dumps(routes[base])
if jq:
    sys.stdout.write(subprocess.run(["jq", "-r", jq], input=doc, capture_output=True, text=True, check=True).stdout)
else:
    print(doc)
'''


def current_ruleset(actors):
    """The repo's live ruleset: the template minus the guard check, with API-filled fields."""
    r = json.loads(json.dumps(TEMPLATE))
    checks = r["rules"][-1]["parameters"]["required_status_checks"]
    r["rules"][-1]["parameters"]["required_status_checks"] = checks[:1]
    r.update(id=7, source_type="Repository", bypass_actors=actors)
    return r


class Harness(unittest.TestCase):
    def run_script(self, script, args, ruleset):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bindir = tmp / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        (tmp / "state.json").write_text(json.dumps({"ruleset": ruleset}))
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", STUB_STATE=str(tmp / "state.json"), STUB_LOG=str(tmp / "log"))
        proc = subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)
        log = tmp / "log"
        puts = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return proc, puts


class OrgScript(Harness):
    def org(self, actors, *extra, apply=True):
        args = ["--repo", "r", *extra] + (["--apply"] if apply else [])
        return self.run_script(ORG, args, current_ruleset(actors))

    def test_apply_keeps_existing_bypass_actors(self) -> None:
        # The incident: the template's bypass_actors is [] and PUT replaced the repo's apps.
        proc, puts = self.org([APP_A, APP_B])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(puts, [], "nothing but the already-matching actors differ, so no PUT")
        self.assertIn("UNCHANGED", proc.stdout)
        self.assertIn("BYPASS KEEP", proc.stdout)

    def test_a_real_update_still_carries_the_bypass_actors(self) -> None:
        ruleset = current_ruleset([APP_A, APP_B])
        ruleset["enforcement"] = "disabled"
        proc, puts = self.run_script(ORG, ["--repo", "r", "--apply"], ruleset)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["method"], "PUT")
        self.assertEqual(puts[0]["path"], "repos/JorisJonkers-dev/r/rulesets/7")
        self.assertEqual(puts[0]["body"]["bypass_actors"], [APP_A, APP_B])
        self.assertEqual(puts[0]["body"]["enforcement"], "active")

    def test_dry_run_shows_kept_actors_and_never_writes(self) -> None:
        ruleset = current_ruleset([APP_A, APP_B])
        ruleset["enforcement"] = "disabled"
        proc, puts = self.run_script(ORG, ["--repo", "r"], ruleset)
        self.assertEqual(puts, [])
        self.assertIn("BYPASS KEEP    Integration:5165539", proc.stdout)
        self.assertIn("BYPASS KEEP    Integration:5165602", proc.stdout)
        self.assertNotIn("BYPASS REMOVE", proc.stdout)
        self.assertIn("bypass kept", proc.stdout.split("== summary")[1])

    def test_template_actors_are_added_and_existing_bypass_mode_wins(self) -> None:
        template = json.loads(json.dumps(TEMPLATE))
        template["bypass_actors"] = [ADMIN, dict(APP_A, bypass_mode="pull_request")]
        tpl = Path(tempfile.mkdtemp()) / "t.json"
        self.addCleanup(shutil.rmtree, tpl.parent, True)
        tpl.write_text(json.dumps(template))
        ruleset = current_ruleset([APP_A])
        ruleset["enforcement"] = "disabled"
        proc, puts = self.run_script(ORG, ["--repo", "r", "--apply", "--template", str(tpl)], ruleset)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(puts[0]["body"]["bypass_actors"], [APP_A, ADMIN])
        self.assertIn("BYPASS ADD     RepositoryRole:5", proc.stdout)
        self.assertNotIn("BYPASS REMOVE", proc.stdout)

    def test_removal_needs_the_explicit_flag_and_is_loud(self) -> None:
        proc, puts = self.org([APP_A, APP_B], "--allow-bypass-removal")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["body"]["bypass_actors"], [])
        self.assertIn("BYPASS REMOVE  Integration:5165539", proc.stdout)
        self.assertIn("BYPASS REMOVAL", proc.stdout.split("== summary")[1])

    def test_dry_run_with_the_flag_reports_the_removal_without_writing(self) -> None:
        proc, puts = self.org([APP_A, APP_B], "--allow-bypass-removal", apply=False)
        self.assertEqual(puts, [])
        self.assertIn("BYPASS REMOVE  Integration:5165602", proc.stdout)

    def test_removal_flag_without_a_repo_is_refused(self) -> None:
        proc, puts = self.run_script(ORG, ["--allow-bypass-removal", "--apply"], current_ruleset([APP_A]))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(puts, [])

    def test_a_mode_difference_is_reported_not_applied(self) -> None:
        template = json.loads(json.dumps(TEMPLATE))
        template["bypass_actors"] = [dict(APP_A, bypass_mode="pull_request")]
        tpl = Path(tempfile.mkdtemp()) / "t.json"
        self.addCleanup(shutil.rmtree, tpl.parent, True)
        tpl.write_text(json.dumps(template))
        proc, puts = self.run_script(ORG, ["--repo", "r", "--template", str(tpl)], current_ruleset([APP_A]))
        self.assertIn("BYPASS MODE    Integration:5165539", proc.stdout)

    def test_a_mode_change_under_the_removal_flag_is_loud(self) -> None:
        template = json.loads(json.dumps(TEMPLATE))
        template["bypass_actors"] = [dict(APP_A, bypass_mode="pull_request")]
        tpl = Path(tempfile.mkdtemp()) / "t.json"
        self.addCleanup(shutil.rmtree, tpl.parent, True)
        tpl.write_text(json.dumps(template))
        proc, puts = self.run_script(ORG, ["--repo", "r", "--allow-bypass-removal", "--template", str(tpl)], current_ruleset([APP_A]))
        self.assertIn("WILL CHANGE", proc.stdout)

    def test_a_non_ruleset_body_is_never_trusted(self) -> None:
        proc, puts = self.run_script(ORG, ["--repo", "r", "--apply"], {"message": "oops"})
        self.assertEqual(puts, [])
        self.assertIn("SKIP", proc.stdout)

    def test_a_missing_ruleset_is_created_with_the_template_actors(self) -> None:
        proc, puts = self.run_script(ORG, ["--repo", "r", "--apply"], None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(puts[0]["method"], "POST")
        self.assertEqual(puts[0]["body"]["bypass_actors"], [])


class SingleRepoScript(Harness):
    def test_update_keeps_existing_bypass_actors(self) -> None:
        proc, puts = self.run_script(ONE, ["O/r"], current_ruleset([APP_A, APP_B]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(puts[0]["method"], "PUT")
        self.assertEqual(puts[0]["body"]["bypass_actors"], [APP_A, APP_B])
        self.assertNotIn("BYPASS REMOVE", proc.stdout)

    def test_unknown_flags_are_refused(self) -> None:
        proc, puts = self.run_script(ONE, ["--dry-run", "O/r"], current_ruleset([APP_A]))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(puts, [])

    def test_removal_needs_the_explicit_flag(self) -> None:
        proc, puts = self.run_script(ONE, ["--allow-bypass-removal", "O/r"], current_ruleset([APP_A]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(puts[0]["body"]["bypass_actors"], [])
        self.assertIn("BYPASS REMOVE Integration:5165539", proc.stdout)


if __name__ == "__main__":
    unittest.main()
