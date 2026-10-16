#!/usr/bin/env python3
"""Turn gremlins' and Stryker's JSON reports into one pull request comment.

Both tools run only on the lines a pull request changes. The comment lists every
mutant that survived (a test ran the line but no test failed when it changed) and,
apart, every mutant no test reaches. It is a report: it never fails the build.

    mutation_report.py --gremlins gremlins.json --stryker mutation.json --out comment.md

A report that is absent counts as no mutants, so a repository with only Go or
only TypeScript passes one flag. A tool that crashed also leaves no report, so the
workflow names it with --failed, and the comment says so instead of claiming every
mutant was caught.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

# The comment is found again by this line, so a later run edits it instead of adding another.
MARKER = "<!-- mutation-report -->"


@dataclass(frozen=True, order=True)
class Mutant:
    file: str
    line: int
    mutator: str


def from_gremlins(report: dict) -> tuple[list[Mutant], list[Mutant]]:
    """The mutants gremlins found LIVED, and those NOT COVERED."""
    survived: list[Mutant] = []
    unreached: list[Mutant] = []
    for f in report.get("files") or []:
        for m in f.get("mutations") or []:
            mutant = Mutant(f["file_name"], m["line"], m["type"])
            if m["status"] == "LIVED":
                survived.append(mutant)
            elif m["status"] == "NOT COVERED":
                unreached.append(mutant)
    return survived, unreached


def from_stryker(report: dict) -> tuple[list[Mutant], list[Mutant]]:
    """The mutants Stryker marked Survived, and those with NoCoverage."""
    survived: list[Mutant] = []
    unreached: list[Mutant] = []
    for path, f in (report.get("files") or {}).items():
        for m in f.get("mutants") or []:
            mutant = Mutant(path, m["location"]["start"]["line"], m["mutatorName"])
            if m["status"] == "Survived":
                survived.append(mutant)
            elif m["status"] == "NoCoverage":
                unreached.append(mutant)
    return survived, unreached


def stryker_worked(report: dict) -> bool:
    """Whether Stryker's results can be trusted: it recorded which tests reach the mutants, and ran
    a test against every mutant it calls survived. A runner that does not support the project's
    Vitest does neither, and every mutant then reads as survived."""
    mutants = [m for f in (report.get("files") or {}).values() for m in f.get("mutants") or []]
    if not mutants:
        return True
    covered = any(m.get("coveredBy") for m in mutants)
    untested = any(m["status"] == "Survived" and not m.get("testsCompleted") for m in mutants)
    return covered and not untested


def _table(mutants: list[Mutant]) -> list[str]:
    rows = ["| where | mutation |", "|---|---|"]
    rows += [f"| `{m.file}:{m.line}` | {m.mutator} |" for m in sorted(mutants)]
    return rows


def render(
    survived: list[Mutant], unreached: list[Mutant], problems: list[str] | None = None, failed: list[str] | None = None
) -> str:
    lines = [MARKER, "### Mutation report", ""]
    if survived:
        noun = "mutant" if len(survived) == 1 else "mutants"
        lines += [
            f"{len(survived)} {noun} survived on the changed lines: a test ran the line, and no test failed "
            "when it changed. Each is a check a test is missing.",
            "",
            *_table(survived),
        ]
    elif not failed:
        lines.append("Every mutant on the changed lines was caught by a test.")
    for tool in failed or []:
        lines += ["", f"{tool} did not finish, so its mutants are not listed: see the job log."]
    if unreached:
        lines += ["", "#### No test reaches these lines", "", *_table(unreached)]
    for problem in problems or []:
        lines += ["", f"- {problem}"]
    lines += ["", "This report never fails the build."]
    return "\n".join(lines) + "\n"


def _read(path: str | None, name: str, problems: list[str]) -> dict:
    if not path or not Path(path).is_file():
        return {}
    try:
        return json.loads(Path(path).read_text())
    except ValueError:
        problems.append(f"The {name} report could not be read, so its mutants are not listed.")
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn mutation reports into one pull request comment.")
    parser.add_argument("--gremlins", help="gremlins' JSON report (--output)")
    parser.add_argument("--stryker", help="Stryker's JSON report (json reporter)")
    parser.add_argument("--out", required=True, help="where to write the comment")
    parser.add_argument("--failed", action="append", default=[], help="a tool whose run did not finish")
    args = parser.parse_args(argv)

    problems: list[str] = []
    g_survived, g_unreached = from_gremlins(_read(args.gremlins, "gremlins", problems))
    stryker = _read(args.stryker, "Stryker", problems)
    s_survived, s_unreached = from_stryker(stryker)
    if not stryker_worked(stryker):
        s_survived, s_unreached = [], []
        args.failed.append("Stryker")
        problems.append(
            "Stryker ran no test against its mutants, which happens when its test runner does not "
            "support the project's Vitest."
        )
    comment = render(g_survived + s_survived, g_unreached + s_unreached, problems, args.failed)
    Path(args.out).write_text(comment)
    print(comment)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
