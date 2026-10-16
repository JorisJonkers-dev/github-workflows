#!/usr/bin/env python3
"""Fail the build on a quarantined test whose quarantine has run out.

A flaky test is fixed, or quarantined for at most 14 days with the issue that
tracks its fix (workspace docs/testing-standards.md, section 7). The marker is one
string, put in the reason of whatever skips the test in its language:

    t.Skip("quarantine: https://github.com/<owner>/<repo>/issues/<n> until 2026-10-30")
    test.fixme(true, 'quarantine: https://github.com/... until 2026-10-30')
    @Disabled("quarantine: https://github.com/... until 2026-10-30")

Every tracked test file is read. A marker fails when its date has passed, when it
is more than 14 days away, when it names no issue, or when it has no date. So a
quarantine cannot become permanent: once its date passes, the build fails again.

    quarantine_check.py [--root DIR] [--today YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

LONGEST = timedelta(days=14)
MARKER = re.compile(r"quarantine:(?P<rest>[^\"'`)]*)")
ISSUE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+")
UNTIL = re.compile(r"\buntil\s+(\d{4}-\d{2}-\d{2})\b")
TEST_FILE = re.compile(r"(_test\.go|\.(spec|test)\.[cm]?[jt]sx?|Test\.(kt|java))$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    problem: str

    def annotation(self) -> str:
        return f"::error file={self.path},line={self.line}::quarantine {self.problem}"


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.search(path))


def check_line(line: str, today: date) -> str | None:
    """Why the quarantine on this line is not allowed, or None when it is, or there is none."""
    marker = MARKER.search(line)
    if not marker:
        return None
    rest = marker.group("rest")
    if not ISSUE.search(rest):
        return "names no issue: give the GitHub issue that tracks the fix"
    until = UNTIL.search(rest)
    try:
        expires = date.fromisoformat(until.group(1)) if until else None
    except ValueError:
        expires = None
    if expires is None:
        return "has no date: end it with `until YYYY-MM-DD`"
    if expires < today:
        return f"expired on {expires}: fix the test or remove it"
    if expires > today + LONGEST:
        return f"expires {expires}, more than 14 days away"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on a quarantined test whose quarantine has run out.")
    parser.add_argument("--root", default=".", help="the repository to read")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(), help="the date to judge against")
    args = parser.parse_args(argv)

    tracked = subprocess.run(["git", "ls-files"], cwd=args.root, capture_output=True, text=True, check=True).stdout.splitlines()
    findings: list[Finding] = []
    quarantined = 0
    for path in filter(is_test_file, tracked):
        for number, line in enumerate(Path(args.root, path).read_text(errors="replace").splitlines(), start=1):
            if MARKER.search(line):
                quarantined += 1
            problem = check_line(line, args.today)
            if problem:
                findings.append(Finding(path, number, problem))

    for finding in findings:
        print(finding.annotation())
    if not quarantined:
        print("No quarantined tests.")
    elif not findings:
        noun = "test" if quarantined == 1 else "tests"
        print(f"{quarantined} quarantined {noun}, every one live.")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
