#!/usr/bin/env python3
"""The lines a pull request adds or changes, per file, as Stryker's --mutate ranges.

    git diff -U0 origin/main...HEAD --relative -- src | changed_ranges.py

prints one line per file: the file, a tab, and its ranges, `src/a.ts:12-14,src/a.ts:20-20`.
Stryker then mutates only those lines, as gremlins --diff does for Go, so the report is about what
the pull request changed and a large file costs no more than its changed lines.
"""

from __future__ import annotations

import re
import sys

# The new side of a hunk: `@@ -a,b +c,d @@`, where an absent d means one line and 0 means none.
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Each file's added or changed line ranges, first to last, from a `git diff -U0`."""
    out: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            current = path[2:] if path.startswith("b/") else None
            continue
        match = HUNK.match(line)
        if not match or current is None:
            continue
        start, count = int(match.group(1)), int(match.group(2) or "1")
        if count:
            out.setdefault(current, []).append((start, start + count - 1))
    return out


def render(changed: dict[str, list[tuple[int, int]]]) -> str:
    return "".join(
        f"{path}\t{','.join(f'{path}:{a}-{b}' for a, b in spans)}\n" for path, spans in sorted(changed.items())
    )


def main() -> int:  # pragma: no cover
    sys.stdout.write(render(ranges(sys.stdin.read())))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
