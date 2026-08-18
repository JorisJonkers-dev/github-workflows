from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = json.loads((ROOT / "data" / "leak-patterns.json").read_text(encoding="utf-8"))
MODE = "deployment-composition"

# A private deploy repository legitimately contains node addresses. The question
# pr-diff mode must answer is whether a change *introduces* one, not whether the
# file it touched already had one.
EXISTING = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: metallb-pool
data:
  addresses: 192.168.0.240-192.168.0.250
  generatedAt: 2026-06-29T00:00:00Z
"""


def patterns_for_mode(mode: str) -> list[str]:
    return [
        pattern
        for category in PATTERNS["modes"][mode]
        for pattern in PATTERNS["categories"][category]["patterns"]
    ]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def added_lines(cwd: Path, base: str, head: str) -> list[str]:
    diff = git(cwd, "diff", "--unified=0", base, head)
    return [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def any_match(lines: list[str]) -> bool:
    return any(re.search(p, line) for p in patterns_for_mode(MODE) for line in lines)


class TestDiffScopedDenyList(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q", ".")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "test")
        target = self.repo / "cluster.yaml"
        target.write_text(EXISTING, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        self.target = target

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def test_editing_a_file_that_already_holds_an_address_passes(self) -> None:
        self.target.write_text(
            EXISTING.replace("name: metallb-pool", "name: metallb-address-pool"),
            encoding="utf-8",
        )
        head = self._commit("rename only")
        self.assertFalse(any_match(added_lines(self.repo, self.base, head)))

    def test_removing_an_address_passes(self) -> None:
        self.target.write_text(
            EXISTING.replace("  addresses: 192.168.0.240-192.168.0.250\n", ""),
            encoding="utf-8",
        )
        head = self._commit("drop the address")
        self.assertFalse(any_match(added_lines(self.repo, self.base, head)))

    def test_adding_an_address_still_fails(self) -> None:
        self.target.write_text(EXISTING + "  extra: 10.0.0.5\n", encoding="utf-8")
        head = self._commit("adds an address")
        self.assertTrue(any_match(added_lines(self.repo, self.base, head)))

    def test_whole_file_scan_would_have_failed_the_benign_edit(self) -> None:
        # Pins the reason this change exists: the old behaviour blocked an edit
        # that introduced nothing.
        self.assertTrue(
            any(
                re.search(pattern, EXISTING, re.M)
                for pattern in patterns_for_mode(MODE)
            )
        )


class TestModeCoverageUnchanged(unittest.TestCase):
    def test_all_refs_and_path_modes_still_defined(self) -> None:
        # Pre-existing content stays guarded by the modes built for that
        # question; only pr-diff changed.
        for mode in ("default", "deployment-artifact", MODE):
            self.assertIn(mode, PATTERNS["modes"])


if __name__ == "__main__":
    unittest.main()
