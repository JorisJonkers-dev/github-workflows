#!/usr/bin/env python3
"""Decide which path buckets a pull request's diff reaches.

A repository declares its buckets in one file (`.github/buckets.yml` by default):

    buckets:
      go: ['**/*.go', 'go.mod', 'go.sum']
      web: ['web/**', '!web/README.md']
      ignore: ['docs/**', '*.md']
    groups:                       # optional: true when any member bucket is
      app: [go, web]
    fixtures:                     # one path per pattern; [] means "in no bucket"
      cmd/app/main.go: [go]
      web/src/main.ts: [web]
      web/README.md: [ignore]
      docs/adr/0001.md: [ignore]
      .github/workflows/ci.yml: []

A bucket claims a path when one of its patterns takes it and none of its `!`
patterns drops it. A changed path no bucket claims turns every bucket on, which
is how build files and the workflow itself reach every suite. `ignore` is a
bucket that gates nothing: it claims what no suite reads.

`--self-test` proves the file still means what its fixtures say: every fixture
lands where it says, every pattern is exercised by some fixture, and every
bucket a workflow gates on exists. A `!` pattern only ever subtracts from its own
bucket: it never claims the paths it does not match. It runs before every decision, so the rules CI applies are the rules
the self-test proves.

The output is one line, `run=<json>`, mapping each bucket and group to a bool.
A job reads one as `fromJSON(needs.<job>.outputs.run).<name>`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

IGNORE = "ignore"

# The three globstar shapes, lifted out before the single-segment wildcards so
# that `*` never sees half of a `**`.
ANY_SEGMENTS = "\x00"  # `**/` and `/**/`: any number of directories, none included
ANY_DESCENDANT = "\x01"  # trailing `/**`: the directory itself and everything under it
ANYTHING = "\x02"  # a bare `**`


class ConfigError(Exception):
    """The buckets file cannot be used as written."""


def to_regex(glob: str) -> re.Pattern[str]:
    """A glob with `**`, `*` and `?`, matched against a whole repository path."""
    glob = re.sub(r"/\*\*$", ANY_DESCENDANT, glob)
    while "/**/" in glob:
        glob = glob.replace("/**/", "/" + ANY_SEGMENTS, 1)
    if glob.startswith("**/"):
        glob = ANY_SEGMENTS + glob[3:]
    glob = glob.replace("**", ANYTHING)

    parts = ["(?s:"]
    for char in glob:
        if char == ANY_SEGMENTS:
            parts.append("(?:[^/]*/)*")
        elif char == ANY_DESCENDANT:
            parts.append("(?:/.*)?")
        elif char == ANYTHING:
            parts.append(".*")
        elif char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
    parts.append(r")\Z")
    return re.compile("".join(parts))


def matches(path: str, patterns: list[str]) -> bool:
    """A bucket claims a path when a positive pattern takes it and no `!` pattern drops it."""
    claimed = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if to_regex(pattern[1:]).match(path):
                return False
        elif to_regex(pattern).match(path):
            claimed = True
    return claimed


def classify(path: str, buckets: dict[str, list[str]]) -> set[str]:
    return {name for name, patterns in buckets.items() if matches(path, patterns)}


def load(path: Path) -> dict:
    """The buckets file, checked for the shape every other function assumes."""
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} does not exist") from exc
    if not isinstance(config, dict) or not isinstance(config.get("buckets"), dict):
        raise ConfigError(f"{path} declares no `buckets` mapping")
    buckets = config["buckets"]
    if IGNORE not in buckets:
        raise ConfigError(f"{path} declares no `{IGNORE}` bucket")
    for name, patterns in buckets.items():
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise ConfigError(f"bucket `{name}` is not a list of patterns")
    groups = config.get("groups") or {}
    for name, members in groups.items():
        if name in buckets:
            raise ConfigError(f"group `{name}` has a bucket's name")
        unknown = [m for m in members if m not in buckets or m == IGNORE]
        if unknown:
            raise ConfigError(f"group `{name}` names {unknown}, which are not gating buckets")
    fixtures = config.get("fixtures") or {}
    for fixture, expected in fixtures.items():
        unknown = [b for b in expected if b not in buckets]
        if unknown:
            raise ConfigError(f"fixture {fixture} expects {unknown}, which no bucket declares")
    return {"buckets": buckets, "groups": groups, "fixtures": fixtures}


def gates(config: dict) -> list[str]:
    """What a job can gate on: every bucket but `ignore`, then every group."""
    return [name for name in config["buckets"] if name != IGNORE] + list(config["groups"])


def unexercised(config: dict) -> list[str]:
    """Patterns whose removal no fixture would notice."""
    buckets, fixtures = config["buckets"], config["fixtures"]
    loose = []
    for name, patterns in buckets.items():
        for pattern in patterns:
            without = {k: [p for p in v if not (k == name and p == pattern)] for k, v in buckets.items()}
            if all(classify(path, without) == set(expected) for path, expected in fixtures.items()):
                loose.append(f"{name}: {pattern}")
    return loose


def unknown_gates(config: dict, workflow: Path, job: str) -> list[str]:
    """Names a job's `if` reads that the file does not declare; each would read as false."""
    gate = re.compile(r"needs\." + re.escape(job) + r"\.outputs\.run\)\.([A-Za-z0-9_-]+)")
    return sorted(set(gate.findall(workflow.read_text(encoding="utf-8"))) - set(gates(config)))


def self_test(config: dict, workflows: list[Path], job: str) -> list[str]:
    """Every way the file can stop meaning what it says, as one message each."""
    failures = []
    buckets = config["buckets"]
    for path, expected in config["fixtures"].items():
        actual = classify(path, buckets)
        if actual != set(expected):
            failures.append(f"{path}: expected {sorted(expected)}, got {sorted(actual)}")
    failures += [f"no fixture exercises {pattern}" for pattern in unexercised(config)]
    for workflow in workflows:
        failures += [
            f"{workflow} gates a job on `{name}`, which the buckets file does not declare"
            for name in unknown_gates(config, workflow, job)
        ]
    return failures


def changed(base: str, head: str) -> list[str]:
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in diff.stdout.splitlines() if line]


def decide(config: dict, paths: list[str] | None) -> tuple[dict[str, bool], list[str]]:
    """Each gate on or off, and the changed paths no bucket claims. `None` means every path."""
    buckets = config["buckets"]
    names = [name for name in buckets if name != IGNORE]
    if paths is None:
        on = dict.fromkeys(names, True)
        stray: list[str] = []
    else:
        stray = [path for path in paths if not classify(path, buckets)]
        if stray:
            on = dict.fromkeys(names, True)
        else:
            on = {name: any(matches(path, buckets[name]) for path in paths) for name in names}
    for group, members in config["groups"].items():
        on[group] = any(on[member] for member in members)
    return on, stray


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide which path buckets a diff reaches.")
    parser.add_argument("--config", type=Path, default=Path(".github/buckets.yml"))
    parser.add_argument("--workflow", type=Path, action="append", default=[],
                        help="a workflow whose gates must exist; repeatable")
    parser.add_argument("--job", default="changes", help="the job whose `run` output the gates read")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--all", action="store_true", help="a run with no diff: every bucket on")
    mode.add_argument("--base", help="the diff's base commit")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    try:
        config = load(args.config)
    except ConfigError as exc:
        print(f"::error::{exc}")
        return 1

    if args.self_test:
        failures = self_test(config, args.workflow, args.job)
        for failure in failures:
            print(f"::error::self-test: {failure}")
        if failures:
            return 1
        print(f"self-test ok: {len(config['fixtures'])} fixtures place correctly")
        return 0

    on, stray = decide(config, None if args.all else changed(args.base, args.head))
    if stray:
        print("::notice::these changed paths are in no bucket, so every bucket runs: " + " ".join(stray))
    line = "run=" + json.dumps(on, sort_keys=True)
    print(line)
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
