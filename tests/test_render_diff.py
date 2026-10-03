"""The render-diff action: compose base and head, diff one Project's render, comment once.

Run against a stand-in deploy-kit command (tests/fixtures/estate-delivery/deploy-kit) whose
render of a project is its project file's `memory:` lines, and a stand-in gh.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions" / "render-diff"
STUB = ROOT / "tests" / "fixtures" / "estate-delivery" / "deploy-kit"


def project_file(directory: Path, body: str) -> None:
    (directory / "deploy").mkdir(parents=True, exist_ok=True)
    (directory / "deploy" / "notes.project.yml").write_text("project: notes\n" + body)


class RenderDiff(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        estate = self.dir / "estate"
        (estate / "platform").mkdir(parents=True)
        (estate / "fragments" / "data").mkdir(parents=True)
        (estate / "fragments" / "data" / "data.project.yml").write_text("project: data\nmemory: 1Gi\n")
        (estate / "cluster-state.yml").write_text("bindings: []\n")
        project_file(self.dir / "base", "memory: 128Mi\n")
        project_file(self.dir / "head", "memory: 256Mi\n")
        self.report = self.dir / "report.md"
        self.log = self.dir / "calls.log"

    def run_diff(self, base="base", head="head", **overrides):
        env = {
            **os.environ,
            "PROJECT_FILE": "deploy/notes.project.yml",
            "ESTATE_INPUTS": "estate",
            "BASE_DIRECTORY": base,
            "HEAD_DIRECTORY": head,
            "REPOSITORY": "JorisJonkers-dev/notes",
            "HEAD_SHA": "b" * 40,
            "BASE_SHA": "a" * 40,
            "REPORT": str(self.report),
            "DEPLOY_KIT_COMMAND": str(STUB),
            "SCHEMA_PACKAGE_INTEGRITY": "sha512-test",
            "STUB_LOG": str(self.log),
            "GITHUB_OUTPUT": str(self.dir / "output"),
            **overrides,
        }
        return subprocess.run(["bash", str(ACTION / "run.sh")], cwd=self.dir, env=env, capture_output=True, text=True)

    def test_a_changed_render_is_reported_as_a_diff_of_the_projects_files_only(self):
        run = self.run_diff()
        self.assertEqual(run.returncode, 0, run.stderr)
        report = self.report.read_text()

        self.assertTrue(report.startswith("<!-- render-diff:notes -->\n"))
        self.assertIn("1 rendered file(s) change", report)
        self.assertIn("--- a/workload.yaml\n+++ b/workload.yaml\n", report)
        self.assertIn("-memory: 128Mi\n+memory: 256Mi\n", report)
        # Another Project's render is in both compositions and in neither diff.
        self.assertNotIn("1Gi", report)
        self.assertIn("project=notes\n", (self.dir / "output").read_text())
        self.assertIn("changed=true\n", (self.dir / "output").read_text())

    def test_the_same_change_writes_the_same_report(self):
        self.assertEqual(self.run_diff().returncode, 0)
        first = self.report.read_text()
        self.assertEqual(self.run_diff().returncode, 0)
        self.assertEqual(self.report.read_text(), first)

    def test_both_sides_are_composed_with_the_estate_and_the_pinned_integrity(self):
        self.assertEqual(self.run_diff().returncode, 0)
        composes = [line for line in self.log.read_text().splitlines() if line.startswith("compose ")]
        self.assertEqual(len(composes), 2)
        for call in composes:
            self.assertIn(f"--platform {self.dir}/estate/platform", call)
            self.assertIn(f"--cluster-state {self.dir}/estate/cluster-state.yml", call)
            self.assertIn("--schema-package-integrity sha512-test", call)
            self.assertNotIn("--held", call)
            self.assertNotIn("--lock", call)

    def test_optional_estate_inputs_are_passed_when_present(self):
        estate = self.dir / "estate"
        (estate / "held").mkdir()
        (estate / "pins.json").write_text("{}")
        (estate / "previous").mkdir()
        (estate / "previous" / "lock.json").write_text("{}")
        (estate / "previous" / "COMMIT").write_text("c" * 40 + "\n")
        self.assertEqual(self.run_diff().returncode, 0)
        call = next(line for line in self.log.read_text().splitlines() if line.startswith("compose "))
        self.assertIn(f"--held {estate}/held", call)
        self.assertIn(f"--pins {estate}/pins.json", call)
        self.assertIn(f"--lock {estate}/previous/lock.json --lock-commit {'c' * 40}", call)

    def test_an_unchanged_render_says_so(self):
        project_file(self.dir / "head", "memory: 128Mi\n# a comment only\n")
        run = self.run_diff()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("No change to the rendered objects.", self.report.read_text())
        self.assertIn("changed=false\n", (self.dir / "output").read_text())

    def test_a_project_new_to_the_base_branch_is_all_additions(self):
        (self.dir / "empty").mkdir()
        run = self.run_diff(base="empty")
        self.assertEqual(run.returncode, 0, run.stderr)
        report = self.report.read_text()
        self.assertIn("+memory: 256Mi\n", report)
        self.assertNotIn("-memory", report)

    def test_a_head_composition_isolates_is_a_failure_with_its_codes(self):
        project_file(self.dir / "head", "memory: 256Mi\n# REFUSE\n")
        run = self.run_diff()
        self.assertEqual(run.returncode, 1)
        report = self.report.read_text()
        self.assertIn("**This change does not compose.**", report)
        self.assertIn("E_STUB", report)
        self.assertNotIn("```diff", report)

    def test_a_head_composition_refuses_outright_is_a_failure_with_its_output(self):
        project_file(self.dir / "head", "memory: 256Mi\n# CRASH\n")
        run = self.run_diff()
        self.assertEqual(run.returncode, 1)
        self.assertIn("E_PLATFORM the Platform document is refused", self.report.read_text())

    def test_what_a_pull_request_renders_cannot_close_the_code_block(self):
        project_file(self.dir / "head", "memory: 256Mi\nmemory: ```\nmemory: `````\n@someone look\n")
        run = self.run_diff()
        self.assertEqual(run.returncode, 0, run.stderr)
        lines = self.report.read_text().splitlines()
        opening = next(i for i, line in enumerate(lines) if line.endswith("diff") and line.startswith("```"))
        fence = lines[opening][: -len("diff")]
        self.assertEqual(fence, "`" * 6)
        # The block closes once, on the last line, with the fence that opened it.
        self.assertEqual(lines[-1], fence)
        self.assertEqual([i for i, line in enumerate(lines) if line == fence], [len(lines) - 1])

    def test_a_refusal_cannot_close_the_code_block_either(self):
        project_file(self.dir / "head", "memory: 256Mi\n# CRASH\n")
        stub = self.dir / "noisy"
        stub.write_text(f"#!/usr/bin/env bash\n\"{STUB}\" \"$@\" || {{ echo '```'; echo '# injected'; exit 1; }}\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        run = self.run_diff(DEPLOY_KIT_COMMAND=str(stub))
        self.assertEqual(run.returncode, 1)
        lines = self.report.read_text().splitlines()
        self.assertEqual(lines[-1], "````")
        self.assertEqual(lines.count("````"), 2)

    def test_a_project_name_that_is_not_one_is_never_used_as_a_path(self):
        (self.dir / "head" / "deploy" / "notes.project.yml").write_text("project: Not_A-Name\nmemory: 1Mi\n")
        run = self.run_diff()
        self.assertEqual(run.returncode, 1)
        self.assertIn("could not be packed", run.stderr)
        self.assertIn("is not a name", run.stderr)
        self.assertFalse(self.report.exists())

    def test_a_long_diff_is_cut_and_says_so(self):
        project_file(self.dir / "head", "".join(f"memory: {n}Mi\n" for n in range(400)))
        run = self.run_diff(MAX_DIFF_BYTES="300")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("... diff cut at 300 bytes", self.report.read_text())
        self.assertLess(len(self.report.read_text()), 1000)

    def test_missing_inputs_are_named(self):
        cases = {
            "no platform": lambda: (self.dir / "estate" / "platform").rmdir(),
            "no cluster state": lambda: (self.dir / "estate" / "cluster-state.yml").unlink(),
            "no head project file": lambda: (self.dir / "head" / "deploy" / "notes.project.yml").unlink(),
        }
        for name, remove in cases.items():
            with self.subTest(name):
                self.setUp()
                remove()
                run = self.run_diff()
                self.assertEqual(run.returncode, 1)
                self.assertIn("render-diff:", run.stderr)

    def test_a_lockfile_that_pins_no_toolkit_is_refused(self):
        (self.dir / "package-lock.json").write_text('{"packages": {}}')
        run = self.run_diff(SCHEMA_PACKAGE_INTEGRITY="")
        self.assertEqual(run.returncode, 1)
        self.assertIn("pins no @jorisjonkers-dev/deploy-kit", run.stderr)

        (self.dir / "package-lock.json").write_text(
            '{"packages": {"node_modules/@jorisjonkers-dev/deploy-kit": {"integrity": "sha512-pinned"}}}'
        )
        self.assertEqual(self.run_diff(SCHEMA_PACKAGE_INTEGRITY="").returncode, 0)
        self.assertIn("--schema-package-integrity sha512-pinned", self.log.read_text())


class Comment(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.report = self.dir / "report.md"
        self.report.write_text("<!-- render-diff:notes -->\n### Render diff: `notes`\n")
        self.log = self.dir / "gh.log"
        gh = self.dir / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$*" >>"$GH_LOG"\n'
            'case "$*" in *--paginate*) printf \'%s\' "${EXISTING:-[]}" ;; esac\n'
        )
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)

    def run_comment(self, **overrides):
        env = {
            **os.environ,
            "PATH": f"{self.dir}:{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "REPOSITORY": "JorisJonkers-dev/notes",
            "PULL_REQUEST": "12",
            "REPORT": str(self.report),
            **overrides,
        }
        return subprocess.run(["bash", str(ACTION / "comment.sh")], env=env, capture_output=True, text=True)

    def test_the_first_report_is_a_new_comment(self):
        run = self.run_comment()
        self.assertEqual(run.returncode, 0, run.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(calls[0], "api --paginate repos/JorisJonkers-dev/notes/issues/12/comments")
        self.assertEqual(
            calls[1], f"api --method POST repos/JorisJonkers-dev/notes/issues/12/comments -F body=@{self.report}"
        )

    def test_a_later_report_replaces_the_projects_comment(self):
        comments = [
            {"id": 6, "user": {"login": "someone"}, "body": "<!-- render-diff:notes -->\nopened with the marker by hand"},
            {"id": 7, "user": {"login": "github-actions[bot]"}, "body": "I quote it: <!-- render-diff:notes -->\nmine"},
            {"id": 8, "user": {"login": "github-actions[bot]"}, "body": "<!-- render-diff:notes-api -->\nanother Project's"},
            {"id": 991, "user": {"login": "github-actions[bot]"}, "body": "<!-- render-diff:notes -->\n### Render diff"},
        ]
        run = self.run_comment(EXISTING=json.dumps(comments))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            self.log.read_text().splitlines()[1],
            f"api --method PATCH repos/JorisJonkers-dev/notes/issues/comments/991 -F body=@{self.report}",
        )

    def test_someone_elses_comment_is_never_replaced(self):
        theirs = [{"id": 6, "user": {"login": "someone"}, "body": "<!-- render-diff:notes -->\nby hand"}]
        run = self.run_comment(EXISTING=json.dumps(theirs))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--method POST", self.log.read_text().splitlines()[1])

        mine = [{"id": 44, "user": {"login": "estate-bot[bot]"}, "body": "<!-- render-diff:notes -->\nearlier"}]
        self.log.unlink()
        run = self.run_comment(EXISTING=json.dumps(mine), COMMENT_AUTHOR="estate-bot[bot]")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--method PATCH repos/JorisJonkers-dev/notes/issues/comments/44", self.log.read_text())

    def test_a_report_without_a_plain_marker_is_not_posted(self):
        for first_line in (
            "### Render diff",
            '<!-- render-diff:notes") or true or startswith(" -->',
            "<!-- render-diff:../x -->",
            "<!-- render-diff: -->",
        ):
            with self.subTest(first_line):
                self.report.write_text(first_line + "\nbody\n")
                run = self.run_comment()
                self.assertEqual(run.returncode, 1)
                self.assertFalse(self.log.exists())

    def test_a_pull_request_that_is_not_a_number_is_refused(self):
        run = self.run_comment(PULL_REQUEST="12/../../issues/3")
        self.assertEqual(run.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_no_report_posts_nothing(self):
        self.report.unlink()
        run = self.run_comment()
        self.assertEqual(run.returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
