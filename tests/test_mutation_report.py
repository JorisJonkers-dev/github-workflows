from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.mutation_report import MARKER, Mutant, from_gremlins, from_stryker, main, render, stryker_worked, written

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
                {"id": "1", "mutatorName": "ConditionalExpression", "status": "Survived", "coveredBy": ["0"], "testsCompleted": 1, "location": {"start": {"line": 14, "column": 5}}},
                {"id": "2", "mutatorName": "StringLiteral", "status": "Killed", "coveredBy": ["0", "1"], "testsCompleted": 1, "location": {"start": {"line": 15, "column": 1}}},
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

    def test_unreached_mutants_are_counted_per_file_not_listed(self) -> None:
        text = render([], [Mutant("b.ts", 9, "StringLiteral"), Mutant("b.ts", 12, "BooleanLiteral"), Mutant("a.ts", 1, "X")])
        self.assertIn("#### No test reaches 3 mutants on the changed lines", text)
        self.assertIn("| `b.ts` | 2 |", text)
        self.assertNotIn("StringLiteral", text)

    def test_one_unreached_mutant_is_counted_in_the_singular(self) -> None:
        self.assertIn("No test reaches 1 mutant on the changed lines", render([], [Mutant("b.ts", 9, "X")]))

    def test_the_files_with_the_most_unreached_mutants_come_first_and_the_rest_are_counted(self) -> None:
        unreached = [Mutant(f"f{i:02}.ts", 1, "X") for i in range(20)] + [Mutant("z.ts", n, "X") for n in range(3)]
        text = render([], unreached)
        self.assertLess(text.index("`z.ts`"), text.index("`f00.ts`"))
        self.assertIn("…and 6 more files.", text)
        self.assertNotIn("`f19.ts`", text)

    def test_survivors_past_the_limit_are_counted_not_listed(self) -> None:
        text = render([Mutant("a.go", n, "X") for n in range(1, 36)], [])
        self.assertIn("`a.go:30`", text)
        self.assertNotIn("`a.go:31`", text)
        self.assertIn("…and 5 more: see the job summary.", text)

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

    def test_a_stryker_run_that_measured_no_coverage_lists_none_of_its_mutants(self) -> None:
        # Stryker's vitest runner records which tests reach each mutant; with none recorded, its
        # setup never ran, no mutant was switched on, and every "survivor" is false.
        unmeasured = json.loads(json.dumps(STRYKER))
        for m in unmeasured["files"]["src/features/home/useLauncher.ts"]["mutants"]:
            m.pop("coveredBy", None)
            m["status"] = "Survived"
            m["testsCompleted"] = 11
        with tempfile.TemporaryDirectory() as directory:
            stryker, out = Path(directory) / "s.json", Path(directory) / "comment.md"
            stryker.write_text(json.dumps(unmeasured))
            with redirect_stdout(io.StringIO()):
                code = main(["--stryker", str(stryker), "--out", str(out)])
            text = out.read_text()
        self.assertEqual(code, 0)
        self.assertNotIn("useLauncher.ts", text)
        self.assertIn("Stryker did not finish", text)
        self.assertNotIn("Every mutant on the changed lines was caught", text)

    def test_a_stryker_survivor_no_test_ran_against_lists_none_of_its_mutants(self) -> None:
        # A mutant "survives" with no test completed when the runner found no test to run for it.
        untested = json.loads(json.dumps(STRYKER))
        untested["files"]["src/features/home/useLauncher.ts"]["mutants"][0]["testsCompleted"] = 0
        with tempfile.TemporaryDirectory() as directory:
            stryker, out = Path(directory) / "s.json", Path(directory) / "comment.md"
            stryker.write_text(json.dumps(untested))
            with redirect_stdout(io.StringIO()):
                main(["--stryker", str(stryker), "--out", str(out)])
            text = out.read_text()
        self.assertNotIn("useLauncher.ts", text)
        self.assertIn("Stryker did not finish", text)
        self.assertIn("Stryker ran no test against its mutants", text)

    def test_a_report_that_is_not_json_is_named_but_does_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken, out = Path(directory) / "g.json", Path(directory) / "comment.md"
            broken.write_text("not json")
            with redirect_stdout(io.StringIO()):
                code = main(["--gremlins", str(broken), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("could not be read", out.read_text())


class GeneratedCode(unittest.TestCase):
    def test_a_file_a_generator_wrote_is_left_out(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "client_gen.go").write_text("// Code generated by ogen, DO NOT EDIT.\npackage oas\n")
            (Path(root) / "service.go").write_text("package app\n")
            mutants = [Mutant("client_gen.go", 9, "X"), Mutant("service.go", 3, "Y")]
            self.assertEqual(written(mutants, root), [Mutant("service.go", 3, "Y")])

    def test_a_file_that_cannot_be_read_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(written([Mutant("absent.ts", 1, "X")], root), [Mutant("absent.ts", 1, "X")])

    def test_generated_mutants_never_reach_the_comment(self) -> None:
        report = {"files": [{"file_name": "oas_client_gen.go", "mutations": [{"type": "X", "status": "NOT COVERED", "line": 1}]}]}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "oas_client_gen.go").write_text("// Code generated by ogen, DO NOT EDIT.\n")
            gremlins, out = Path(directory) / "g.json", Path(directory) / "comment.md"
            gremlins.write_text(json.dumps(report))
            with redirect_stdout(io.StringIO()):
                main(["--gremlins", str(gremlins), "--go-root", directory, "--out", str(out)])
            self.assertNotIn("oas_client_gen.go", out.read_text())


class StrykerRuns(unittest.TestCase):
    def test_reports_of_several_runs_make_one_comment(self) -> None:
        other = {"files": {"src/b.ts": {"mutants": [
            {"id": "9", "mutatorName": "StringLiteral", "status": "Survived", "testsCompleted": 2, "location": {"start": {"line": 4}}},
            {"id": "10", "mutatorName": "StringLiteral", "status": "Killed", "testsCompleted": 1, "location": {"start": {"line": 5}}},
        ]}}}
        with tempfile.TemporaryDirectory() as directory:
            a, b, out = Path(directory) / "a.json", Path(directory) / "b.json", Path(directory) / "comment.md"
            a.write_text(json.dumps(STRYKER))
            b.write_text(json.dumps(other))
            with redirect_stdout(io.StringIO()):
                main(["--stryker", str(a), "--stryker", str(b), "--out", str(out)])
            text = out.read_text()
        self.assertIn("`src/features/home/useLauncher.ts:14`", text)
        self.assertIn("`src/b.ts:4`", text)

    def test_a_run_without_coverage_that_killed_mutants_is_trusted(self) -> None:
        # The command runner switches mutants on through the environment and records no coverage.
        report = {"files": {"src/b.ts": {"mutants": [
            {"mutatorName": "X", "status": "Killed", "testsCompleted": 1},
            {"mutatorName": "Y", "status": "Survived", "testsCompleted": 1},
        ]}}}
        self.assertTrue(stryker_worked(report))

    def test_a_run_that_switched_no_mutant_on_is_not_trusted(self) -> None:
        report = {"files": {"src/b.ts": {"mutants": [{"mutatorName": "Y", "status": "Survived", "testsCompleted": 3}]}}}
        self.assertFalse(stryker_worked(report))


if __name__ == "__main__":
    unittest.main()


class TheWorkflow(unittest.TestCase):
    def test_gremlins_runs_every_test_against_each_mutant(self) -> None:
        # gremlins otherwise picks the package to test by finding a directory named after the Go
        # package, so a mutant in `package main` under cmd/app runs no tests and reads as survived.
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/mutation-report.yml").read_text()
        unleash = next(line for line in workflow.splitlines() if "gremlins\" unleash" in line)
        self.assertIn("--integration", unleash)

    def test_stryker_runs_in_place_on_the_changed_lines_with_the_command_runner(self) -> None:
        # Its Vitest runner switches no mutant on under Vitest 5, and its sandbox loses imports from
        # outside the web directory; the command runner switches each mutant on through the environment.
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/mutation-report.yml").read_text()
        self.assertIn('testRunner: "command"', workflow)
        self.assertIn("inPlace: true", workflow)
        self.assertIn('--mutate "$spans"', workflow)
        self.assertNotIn("clear-text", workflow)
