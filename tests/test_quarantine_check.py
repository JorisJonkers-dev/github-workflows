from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from scripts.quarantine_check import Finding, check_line, is_test_file, main

TODAY = date(2026, 10, 16)
ISSUE = "https://github.com/JorisJonkers-dev/identity/issues/60"


class ReadingAMarker(unittest.TestCase):
    def test_a_marker_with_an_issue_and_a_date_within_fourteen_days_is_accepted(self) -> None:
        self.assertIsNone(check_line(f't.Skip("quarantine: {ISSUE} until 2026-10-30")', TODAY))

    def test_a_marker_expiring_today_is_still_accepted(self) -> None:
        self.assertIsNone(check_line(f"// quarantine: {ISSUE} until 2026-10-16", TODAY))

    def test_a_marker_past_its_date_fails_the_build_again(self) -> None:
        self.assertEqual(check_line(f"quarantine: {ISSUE} until 2026-10-15", TODAY), "expired on 2026-10-15: fix the test or remove it")

    def test_a_marker_more_than_fourteen_days_away_is_refused(self) -> None:
        self.assertEqual(check_line(f"quarantine: {ISSUE} until 2026-10-31", TODAY), "expires 2026-10-31, more than 14 days away")

    def test_a_marker_without_an_issue_link_is_refused(self) -> None:
        self.assertEqual(check_line("quarantine: flaky on CI until 2026-10-20", TODAY), "names no issue: give the GitHub issue that tracks the fix")

    def test_a_marker_without_a_date_is_refused(self) -> None:
        self.assertEqual(check_line(f"quarantine: {ISSUE}", TODAY), "has no date: end it with `until YYYY-MM-DD`")

    def test_a_marker_with_an_impossible_date_is_refused(self) -> None:
        self.assertEqual(check_line(f"quarantine: {ISSUE} until 2026-02-30", TODAY), "has no date: end it with `until YYYY-MM-DD`")

    def test_a_line_without_a_marker_is_ignored(self) -> None:
        self.assertIsNone(check_line("func TestAnAdminAddsAPersonToAGroup(t *testing.T) {", TODAY))


class WhichFilesAreTests(unittest.TestCase):
    def test_go_typescript_kotlin_and_java_tests_are_scanned(self) -> None:
        for path in ["internal/a/b_test.go", "web/src/x.spec.ts", "web/src/y.test.ts", "web/tests/e2e/gate.spec.ts",
                     "api/src/test/kotlin/FooTest.kt", "lib/src/integrationTest/java/BarTest.java"]:
            with self.subTest(path=path):
                self.assertTrue(is_test_file(path))

    def test_documents_and_sources_are_not_scanned(self) -> None:
        for path in ["docs/testing-standards.md", "internal/a/b.go", "web/src/x.ts", "README.md"]:
            with self.subTest(path=path):
                self.assertFalse(is_test_file(path))


def repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return directory


class CommandLine(unittest.TestCase):
    def test_a_repository_with_live_quarantines_passes(self) -> None:
        with repo({"a_test.go": f't.Skip("quarantine: {ISSUE} until 2026-10-20")\n'}) as root, redirect_stdout(io.StringIO()) as out:
            code = main(["--root", root, "--today", "2026-10-16"])
        self.assertEqual(code, 0)
        self.assertIn("1 quarantined test, every one live", out.getvalue())

    def test_an_expired_quarantine_fails_and_names_where_it_is(self) -> None:
        with repo({"web/x.spec.ts": "it('a', () => {})\n" + f"it.skip('b quarantine: {ISSUE} until 2026-10-01', () => {{}})\n"}) as root, redirect_stdout(io.StringIO()) as out:
            code = main(["--root", root, "--today", "2026-10-16"])
        self.assertEqual(code, 1)
        self.assertIn("::error file=web/x.spec.ts,line=2::quarantine expired on 2026-10-01", out.getvalue())

    def test_a_marker_outside_test_files_is_not_checked(self) -> None:
        with repo({"docs/standards.md": "quarantine: flaky until never\n"}) as root, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", root, "--today", "2026-10-16"]), 0)

    def test_a_repository_without_quarantines_passes(self) -> None:
        with repo({"a_test.go": "package a\n"}) as root, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--root", root, "--today", "2026-10-16"]), 0)
        self.assertIn("No quarantined tests.", out.getvalue())

    def test_today_defaults_to_the_current_date(self) -> None:
        with repo({"a_test.go": "package a\n"}) as root, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", root]), 0)

    def test_a_finding_reads_as_a_github_annotation(self) -> None:
        self.assertEqual(Finding("a_test.go", 3, "names no issue").annotation(), "::error file=a_test.go,line=3::quarantine names no issue")


if __name__ == "__main__":
    unittest.main()
