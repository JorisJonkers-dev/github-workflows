from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

from scripts import path_buckets
from scripts.path_buckets import ConfigError, classify, decide, load, main, matches, self_test, to_regex

CONFIG = """
buckets:
  go: ['**/*.go', 'go.mod']
  web: ['web/**', '!web/README.md']
  ignore: ['docs/**', '*.md', 'web/README.md']
groups:
  app: [go, web]
fixtures:
  cmd/app/main.go: [go]
  go.mod: [go]
  web/src/main.ts: [web]
  web/README.md: [ignore]
  docs/adr/0001.md: [ignore]
  README.md: [ignore]
  .github/workflows/ci.yml: []
"""

WORKFLOW = """
jobs:
  test:
    if: fromJSON(needs.changes.outputs.run).go
  e2e:
    if: fromJSON(needs.changes.outputs.run).app
"""


def write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@contextmanager
def workdir():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as temp:
        os.chdir(temp)
        try:
            yield Path(temp)
        finally:
            os.chdir(previous)


def run_main(args: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(args)
    return code, out.getvalue()


class GlobTest(unittest.TestCase):
    def test_globstar_shapes(self) -> None:
        cases = [
            ("web/**", "web", True),
            ("web/**", "web/a/b.ts", True),
            ("web/**", "webx/a.ts", False),
            ("**/*.go", "main.go", True),
            ("**/*.go", "a/b/c.go", True),
            ("a/**/z.txt", "a/z.txt", True),
            ("a/**/z.txt", "a/b/c/z.txt", True),
            ("a/**b", "a/x/yb", True),
            ("*.md", "README.md", True),
            ("*.md", "docs/a.md", False),
            ("file?.txt", "file1.txt", True),
            ("file?.txt", "file/.txt", False),
            ("a.b", "aXb", False),
        ]
        for glob, path, expected in cases:
            with self.subTest(glob=glob, path=path):
                self.assertEqual(bool(to_regex(glob).match(path)), expected)

    def test_a_negation_only_subtracts(self) -> None:
        self.assertFalse(matches("web/README.md", ["web/**", "!web/README.md"]))
        self.assertTrue(matches("web/a.ts", ["web/**", "!web/README.md"]))
        self.assertFalse(matches("elsewhere.txt", ["!web/**"]))


class LoadTest(unittest.TestCase):
    def test_refuses_each_malformed_shape(self) -> None:
        cases = {
            "missing file": None,
            "no buckets": "groups: {}\n",
            "no ignore": "buckets:\n  go: ['*.go']\n",
            "patterns not a list": "buckets:\n  go: '*.go'\n  ignore: []\n",
            "group shadows a bucket": "buckets:\n  go: []\n  ignore: []\ngroups:\n  go: [go]\n",
            "group names ignore": "buckets:\n  go: []\n  ignore: []\ngroups:\n  app: [ignore]\n",
            "fixture names no bucket": "buckets:\n  ignore: []\nfixtures:\n  a: [web]\n",
        }
        with workdir() as root:
            for name, body in cases.items():
                with self.subTest(name):
                    path = root / f"{name}.yml"
                    if body is not None:
                        write(path, body)
                    with self.assertRaises(ConfigError):
                        load(path)

    def test_groups_and_fixtures_are_optional(self) -> None:
        with workdir() as root:
            config = load(write(root / "b.yml", "buckets:\n  ignore: []\n"))
        self.assertEqual(config, {"buckets": {"ignore": []}, "groups": {}, "fixtures": {}})


class SelfTestTest(unittest.TestCase):
    def config(self, body: str = CONFIG) -> dict:
        with workdir() as root:
            return load(write(root / "b.yml", body))

    def test_a_consistent_file_passes(self) -> None:
        with workdir() as root:
            workflow = write(root / "ci.yml", WORKFLOW)
            self.assertEqual(self_test(self.config(), [workflow], "changes"), [])

    def test_a_misplaced_fixture_fails(self) -> None:
        body = CONFIG.replace("go.mod: [go]", "go.mod: [web]")
        self.assertIn("go.mod: expected ['web'], got ['go']", self_test(self.config(body), [], "changes"))

    def test_an_unexercised_pattern_fails(self) -> None:
        body = CONFIG.replace("go: ['**/*.go', 'go.mod']", "go: ['**/*.go', 'go.mod', 'go.work']")
        self.assertIn("no fixture exercises go: go.work", self_test(self.config(body), [], "changes"))

    def test_an_undeclared_gate_fails(self) -> None:
        with workdir() as root:
            workflow = write(root / "ci.yml", WORKFLOW + "    if: fromJSON(needs.changes.outputs.run).frontend\n")
            failures = self_test(self.config(), [workflow], "changes")
        self.assertEqual(failures, [f"{workflow} gates a job on `frontend`, which the buckets file does not declare"])

    def test_gates_are_read_from_the_named_job_only(self) -> None:
        with workdir() as root:
            workflow = write(root / "ci.yml", "if: fromJSON(needs.other.outputs.run).frontend\n")
            self.assertEqual(self_test(self.config(), [workflow], "changes"), [])


class DecideTest(unittest.TestCase):
    def setUp(self) -> None:
        with workdir() as root:
            self.config = load(write(root / "b.yml", CONFIG))

    def test_turns_on_only_the_buckets_a_diff_reaches(self) -> None:
        on, stray = decide(self.config, ["cmd/app/main.go", "docs/x.md"])
        self.assertEqual(on, {"go": True, "web": False, "app": True})
        self.assertEqual(stray, [])

    def test_an_ignore_only_diff_turns_everything_off(self) -> None:
        on, _ = decide(self.config, ["README.md", "web/README.md"])
        self.assertEqual(on, {"go": False, "web": False, "app": False})

    def test_a_path_in_no_bucket_turns_everything_on(self) -> None:
        on, stray = decide(self.config, ["web/a.ts", ".github/workflows/ci.yml"])
        self.assertEqual(on, {"go": True, "web": True, "app": True})
        self.assertEqual(stray, [".github/workflows/ci.yml"])

    def test_no_diff_turns_everything_on(self) -> None:
        on, _ = decide(self.config, None)
        self.assertEqual(on, {"go": True, "web": True, "app": True})

    def test_classify_names_every_bucket_a_path_is_in(self) -> None:
        self.assertEqual(classify("web/README.md", self.config["buckets"]), {"ignore"})


class MainTest(unittest.TestCase):
    def test_self_test_passes_and_fails(self) -> None:
        with workdir() as root:
            write(root / ".github/buckets.yml", CONFIG)
            write(root / "ci.yml", WORKFLOW)
            self.assertEqual(run_main(["--self-test", "--workflow", "ci.yml"])[0], 0)
            write(root / ".github/buckets.yml", CONFIG.replace("go.mod: [go]", "go.mod: []"))
            code, out = run_main(["--self-test"])
        self.assertEqual(code, 1)
        self.assertIn("::error::self-test: go.mod", out)

    def test_a_malformed_file_is_an_error(self) -> None:
        with workdir():
            code, out = run_main(["--all"])
        self.assertEqual(code, 1)
        self.assertIn("::error::.github/buckets.yml does not exist", out)

    def test_all_writes_the_output_line(self) -> None:
        with workdir() as root:
            write(root / ".github/buckets.yml", CONFIG)
            output = root / "out.txt"
            os.environ["GITHUB_OUTPUT"] = str(output)
            try:
                code, out = run_main(["--all"])
            finally:
                del os.environ["GITHUB_OUTPUT"]
            self.assertEqual(code, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), out)
        self.assertEqual(json.loads(out.removeprefix("run=")), {"go": True, "web": True, "app": True})

    def test_a_diff_decides_from_git(self) -> None:
        with workdir() as root:
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.email", "test@example.invalid")
            git(root, "config", "user.name", "test")
            write(root / ".github/buckets.yml", CONFIG)
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "base")
            base = git(root, "rev-parse", "HEAD")
            write(root / "web/src/main.ts", "x")
            write(root / "tools/x.sh", "x")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "head")
            code, out = run_main(["--base", base])
            self.assertEqual(code, 0)
            self.assertIn("::notice::these changed paths are in no bucket, so every bucket runs: tools/x.sh", out)
            git(root, "rm", "-q", "tools/x.sh")
            git(root, "commit", "-q", "-m", "only web")
            code, out = run_main(["--base", base, "--head", "HEAD"])
        self.assertEqual(json.loads(out.strip().removeprefix("run=")), {"go": False, "web": True, "app": True})

    def test_the_module_runs_as_a_script(self) -> None:
        script = Path(path_buckets.__file__)
        with workdir() as root:
            write(root / ".github/buckets.yml", CONFIG)
            result = subprocess.run([sys.executable, str(script), "--self-test"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
