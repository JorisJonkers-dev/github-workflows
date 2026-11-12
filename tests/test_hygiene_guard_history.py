"""The hygiene guard must fail loudly, never crash or pass, when history is missing.

v0.46.0 ran `git fetch --depth=1` for the base branch inside a full clone; that cut the base
tip's history, so `origin/main...HEAD` had no merge base once main had moved on.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "repository-hygiene-guard.yml").read_text(encoding="utf-8")


def git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


class HygieneGuardHistory(unittest.TestCase):
    def test_no_depth_limited_fetch(self) -> None:
        self.assertNotIn("--depth", WORKFLOW)

    def test_merge_base_is_verified_with_a_clear_error(self) -> None:
        self.assertIn('git merge-base "$base_ref" HEAD', WORKFLOW)
        self.assertIn("share no commit in this checkout", WORKFLOW)
        self.assertIn("git diff {base_ref}...HEAD failed", WORKFLOW)

    def test_depth_one_fetch_reproduces_the_missing_merge_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, clone = root / "origin", root / "clone"
            git(root, "init", "-q", "-b", "main", str(origin))
            for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
                git(origin, "config", key, value)
            (origin / "a").write_text("1")
            git(origin, "add", "a")
            git(origin, "commit", "-q", "-m", "base")
            git(root, "clone", "-q", f"file://{origin}", str(clone))
            git(clone, "checkout", "-q", "-b", "pr")
            for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
                git(clone, "config", key, value)
            (clone / "b").write_text("2")
            git(clone, "add", "b")
            git(clone, "commit", "-q", "-m", "pr")
            for n in ("2", "3"):
                (origin / "a").write_text(n)
                git(origin, "commit", "-q", "-am", f"main {n}")
            git(clone, "fetch", "-q", "--no-tags", "--depth=1", "origin", "main")
            failed = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"], cwd=clone, capture_output=True, text=True
            )
            self.assertNotEqual(failed.returncode, 0)
            git(clone, "fetch", "-q", "--no-tags", "--unshallow", "origin")
            self.assertTrue(git(clone, "merge-base", "origin/main", "HEAD").strip())


if __name__ == "__main__":
    unittest.main()
