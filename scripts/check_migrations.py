#!/usr/bin/env python3
"""Guard Flyway-style migrations against checksum and ordering regressions."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MIGRATION_REGEX = r"services/[^/]+/src/main/resources/db/migration(-pg)?/V[0-9][^/]*\.sql$"
DEFAULT_SCOPE_REGEX = r"(services/[^/]+)/.*"


@dataclass(frozen=True)
class Migration:
    path: str
    scope: str
    version: tuple[int, ...]


class MigrationError(RuntimeError):
    """Raised when git cannot provide the requested comparison data."""


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise MigrationError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line]


def version_from_path(path: str) -> tuple[int, ...]:
    match = re.search(r"/V([0-9]+(?:_[0-9]+)*)__[^/]*\.sql$", f"/{path}")
    if not match:
        raise ValueError(f"cannot extract Flyway version from {path}")
    return tuple(int(part) for part in match.group(1).split("_"))


def scope_for_path(path: str, scope_regex: re.Pattern[str]) -> str:
    match = scope_regex.match(path)
    if not match:
        return str(Path(path).parent)
    if match.groups():
        return match.group(1)
    return match.group(0)


def migrations(paths: list[str], migration_regex: re.Pattern[str], scope_regex: re.Pattern[str]) -> list[Migration]:
    found: list[Migration] = []
    for path in paths:
        if migration_regex.search(path):
            found.append(Migration(path, scope_for_path(path, scope_regex), version_from_path(path)))
    return found


def annotations(kind: str, message: str) -> None:
    print(f"::{kind}::{message}")


def check_migrations(base_ref: str, migration_pattern: str, scope_pattern: str, allow_change: bool) -> int:
    migration_regex = re.compile(migration_pattern)
    scope_regex = re.compile(scope_pattern)
    fail = False

    changed = migrations(
        lines(run_git(["diff", "--diff-filter=MDR", "--name-only", f"{base_ref}...HEAD"])),
        migration_regex,
        scope_regex,
    )
    if changed:
        changed_list = ", ".join(migration.path for migration in changed)
        if allow_change:
            annotations("warning", f"Existing migrations changed with override enabled: {changed_list}")
        else:
            annotations(
                "error",
                "Applied migrations are immutable. Add a new migration with a higher version. "
                f"Offending files: {changed_list}",
            )
            fail = True

    head = migrations(lines(run_git(["ls-files"])), migration_regex, scope_regex)
    base = migrations(lines(run_git(["ls-tree", "-r", "--name-only", base_ref])), migration_regex, scope_regex)

    head_by_scope: dict[str, list[Migration]] = {}
    base_by_scope: dict[str, list[Migration]] = {}
    for migration in head:
        head_by_scope.setdefault(migration.scope, []).append(migration)
    for migration in base:
        base_by_scope.setdefault(migration.scope, []).append(migration)

    for scope, scope_head in sorted(head_by_scope.items()):
        seen: dict[tuple[int, ...], list[str]] = {}
        for migration in scope_head:
            seen.setdefault(migration.version, []).append(migration.path)
        duplicates = {version: paths for version, paths in seen.items() if len(paths) > 1}
        for version, paths in sorted(duplicates.items()):
            annotations("error", f"[{scope}] duplicate migration version {format_version(version)}: {', '.join(paths)}")
            fail = True

        scope_base = base_by_scope.get(scope, [])
        if not scope_base:
            continue
        base_paths = {migration.path for migration in scope_base}
        base_max = max(migration.version for migration in scope_base)
        for migration in sorted(scope_head, key=lambda item: item.path):
            if migration.path in base_paths:
                continue
            if migration.version <= base_max:
                annotations(
                    "error",
                    f"[{scope}] new migration {Path(migration.path).name} "
                    f"(version {format_version(migration.version)}) must be greater than "
                    f"the highest existing version ({format_version(base_max)})",
                )
                fail = True

    if fail:
        return 1
    print("Migration guard: OK")
    return 0


def format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main", help="Base ref used for migration comparison.")
    parser.add_argument("--migration-regex", default=DEFAULT_MIGRATION_REGEX, help="Regex matching migration paths.")
    parser.add_argument("--scope-regex", default=DEFAULT_SCOPE_REGEX, help="Regex whose first capture group is the migration scope.")
    parser.add_argument(
        "--allow-change",
        action="store_true",
        default=os.environ.get("ALLOW_MIGRATION_CHANGE", "").lower() == "true",
        help="Warn instead of failing when existing migrations changed.",
    )
    args = parser.parse_args(argv)

    try:
        return check_migrations(args.base_ref, args.migration_regex, args.scope_regex, args.allow_change)
    except MigrationError as exc:
        annotations("error", str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
