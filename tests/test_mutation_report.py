from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.mutation_report import MARKER, Mutant, from_gremlins, from_stryker, main, render

GREMLINS = {
    "go_module": "github.com/example/app",
    "files": [
        {
            "file_name": "internal/access/app/service.go",
            "mutations": [
                {"type": "CONDITIONALS_BOUNDARY", "status": "LIVED", "line": 42, "column": 7},
                {"type": "INVERT_NEGATIVES", "status": "KILLED", "line": 50, "column": 3},
                {"type": "ARITHMETIC_BASE", "status": "NOT COVERED", "line": 61, "column": 9},
            ],
        }
    ],
}

STRYKER = {
    "schemaVersion": "1",
    "files": {
        "src/features/home/useLauncher.ts": {
            "language": "typescript",
            "mutants": [
                {"id": "1", "mutatorName": "ConditionalExpression", "status": "Survived", "location": {"start": {"line": 14, "column": 5}}},
                {"id": "2", "mutatorName": "StringLiteral", "status": "Killed", "location": {"start": {"line": 15, "column": 1}}},
                {"id": "3", "mutatorName": "BooleanLiteral", "status": "NoCoverage", "location": {"start": {"line": 20, "column": 2}}},
            ],
        }
    },
}


class ReadingGremlins(unittest.TestCase):
    def test_a_mutant_that_lived_is_a_survivor(self) -> None:
        survived, _ = from_gremlins(GREMLINS)
        self.assertEqual(survived, [Mutant("internal/access/app/service.go", 42, "CONDITIONALS_BOUNDARY")])

    def test_a_mutant_no_test_reaches_is_reported_apart(self) -> None:
        _, unreached = from_gremlins(GREMLINS)
        self.assertEqual(unreached, [Mutant("internal/access/app/service.go", 61, "ARITHMETIC_BASE")])

    def test_a_run_with_no_changed_go_reports_nothing(self) -> None:
        self.assertEqual(from_gremlins({"files": None}), ([], []))


class ReadingStryker(unittest.TestCase):
    def test_a_surviving_mutant_is_a_survivor(self) -> None:
        survived, _ = from_stryker(STRYKER)
        self.assertEqual(survived, [Mutant("src/features/home/useLauncher.ts", 14, "ConditionalExpression")])

    def test_a_mutant_without_coverage_is_reported_apart(self) -> None:
        _, unreached = from_stryker(STRYKER)
        self.assertEqual(unreached, [Mutant("src/features/home/useLauncher.ts", 20, "BooleanLiteral")])


class Rendering(unittest.TestCase):
    def test_the_comment_carries_the_marker_it_is_found_by(self) -> None:
        self.assertTrue(render([], []).startswith(MARKER))

    def test_no_survivors_says_every_mutant_was_caught(self) -> None:
        self.assertIn("Every mutant on the changed lines was caught by a test.", render([], []))

    def test_each_survivor_is_listed_with_where_it_lives(self) -> None:
        text = render([Mutant("a.go", 3, "INVERT_NEGATIVES")], [])
        self.assertIn("| `a.go:3` | INVERT_NEGATIVES |", text)
        self.assertIn("1 mutant survived", text)

    def test_unreached_mutants_get_their_own_section(self) -> None:
        text = render([], [Mutant("b.ts", 9, "StringLiteral")])
        self.assertIn("No test reaches these lines", text)
        self.assertIn("| `b.ts:9` | StringLiteral |", text)

    def test_the_comment_says_it_never_fails_the_build(self) -> None:
        self.assertIn("This report never fails the build.", render([], []))

    def test_survivors_are_sorted_by_file_and_line(self) -> None:
        text = render([Mutant("b.go", 1, "X"), Mutant("a.go", 9, "Y"), Mutant("a.go", 2, "Z")], [])
        self.assertLess(text.index("`a.go:2`"), text.index("`a.go:9`"))
        self.assertLess(text.index("`a.go:9`"), text.index("`b.go:1`"))


class CommandLine(unittest.TestCase):
    def test_it_writes_one_comment_from_both_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gremlins, stryker, out = (Path(directory) / name for name in ("g.json", "s.json", "comment.md"))
            gremlins.write_text(json.dumps(GREMLINS))
            stryker.write_text(json.dumps(STRYKER))
            with redirect_stdout(io.StringIO()):
                code = main(["--gremlins", str(gremlins), "--stryker", str(stryker), "--out", str(out)])
            text = out.read_text()
        self.assertEqual(code, 0)
        self.assertIn("`internal/access/app/service.go:42`", text)
        self.assertIn("`src/features/home/useLauncher.ts:14`", text)

    def test_a_missing_report_counts_as_no_mutants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "comment.md"
            with redirect_stdout(io.StringIO()):
                code = main(["--gremlins", str(Path(directory) / "absent.json"), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("Every mutant on the changed lines was caught by a test.", out.read_text())

    def test_a_tool_that_did_not_finish_is_named_instead_of_reading_as_all_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "comment.md"
            with redirect_stdout(io.StringIO()):
                code = main(["--failed", "Stryker", "--out", str(out)])
            text = out.read_text()
        self.assertEqual(code, 0)
        self.assertIn("Stryker did not finish, so its mutants are not listed: see the job log.", text)
        self.assertNotIn("Every mutant on the changed lines was caught", text)

    def test_a_report_that_is_not_json_is_named_but_does_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken, out = Path(directory) / "g.json", Path(directory) / "comment.md"
            broken.write_text("not json")
            with redirect_stdout(io.StringIO()):
                code = main(["--gremlins", str(broken), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("could not be read", out.read_text())


if __name__ == "__main__":
    unittest.main()


class TheWorkflow(unittest.TestCase):
    def test_gremlins_runs_every_test_against_each_mutant(self) -> None:
        # gremlins otherwise picks the package to test by finding a directory named after the Go
        # package, so a mutant in `package main` under cmd/app runs no tests and reads as survived.
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/mutation-report.yml").read_text()
        unleash = next(line for line in workflow.splitlines() if "gremlins\" unleash" in line)
        self.assertIn("--integration", unleash)
