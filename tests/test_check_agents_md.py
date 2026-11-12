"""The AGENTS.md lint fails on a missing file, a dead path and a dead symbol.

The hygiene guard runs `scripts/check_agents_md.py` against every caller. These tests build
tiny repositories on disk and drive the script's own functions, so every line and branch is held.
"""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from scripts import check_agents_md as lint

WORKFLOW = (ROOT / ".github" / "workflows" / "repository-hygiene-guard.yml").read_text(encoding="utf-8")


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def problems(self, agents: str) -> list[str]:
        self.write("AGENTS.md", agents)
        return [line.split(": ", 1)[1] for line in lint.check_repo(self.root)]


class FileLevel(RepoCase):
    def test_missing_file(self) -> None:
        self.assertEqual(lint.check_repo(self.root), [f"{self.root}/AGENTS.md:1: AGENTS.md is missing"])

    def test_empty_file(self) -> None:
        self.assertEqual(self.problems("  \n\n"), ["AGENTS.md is empty"])

    def test_invalid_utf8(self) -> None:
        (self.root / "AGENTS.md").write_bytes(b"\xff\xfe\x00")
        self.assertEqual(lint.check_repo(self.root), [f"{self.root}/AGENTS.md:1: AGENTS.md is not valid utf-8"])

    def test_clean_pointer(self) -> None:
        self.write("CLAUDE.md", "x")
        self.write("docs/guide.md", "x")
        text = "See [claude](CLAUDE.md), [site](https://example.com), [top](#top), [q](?x=1), and `docs/guide.md`.\n"
        self.assertEqual(self.problems(text), [])


class Links(RepoCase):
    def test_dead_link_is_reported_with_its_line(self) -> None:
        self.write("AGENTS.md", "ok\n[gone](docs/gone.md#part)\n")
        self.assertEqual(lint.check_repo(self.root), [f"{self.root}/AGENTS.md:2: dead path: docs/gone.md"])

    def test_link_title_angle_brackets_and_encoding(self) -> None:
        self.write("docs/a b.md", "x")
        self.assertEqual(self.problems('[a](<docs/a%20b.md> "title") [b](docs/a%20b.md)'), [])

    def test_duplicate_problem_on_one_line_is_reported_once(self) -> None:
        self.assertEqual(self.problems("`gone/a.py` `gone/a.py`"), ["dead path: gone/a.py"])

    def test_problems_come_in_line_order(self) -> None:
        self.assertEqual(self.problems("`gone/b.py` [x](gone/a.md)"), ["dead path: gone/b.py", "dead path: gone/a.md"])


class Spans(RepoCase):
    def test_dead_path_span(self) -> None:
        self.assertEqual(self.problems("run `scripts/run.py`"), ["dead path: scripts/run.py"])

    def test_live_path_with_dot_slash_line_suffix_and_trailing_slash(self) -> None:
        self.write("scripts/run.py", "x")
        self.assertEqual(self.problems("`./scripts/run.py:12` `scripts/` `scripts/run.py()`"), [])

    def test_not_paths_are_ignored(self) -> None:
        text = "`ls -la` `*.py` `/etc/hosts` `~/x.md` `-f.md` `https://x.dev/a` `main` `v1.2` `a|b/c` ``"
        self.assertEqual(self.problems(text), [])

    def test_bare_file_with_known_extension_is_checked(self) -> None:
        self.assertEqual(self.problems("`gone.toml`"), ["dead path: gone.toml"])

    def test_dotfile_without_stem_is_not_a_path(self) -> None:
        self.assertEqual(self.problems("`.py`"), [])

    def test_fenced_blocks_are_skipped(self) -> None:
        text = "```\n`gone/a.py`\n~~~\n```\n~~~sh\n`gone/b.py`\n```\n~~~\n`gone/c.py`\n"
        self.assertEqual(self.problems(text), ["dead path: gone/c.py"])


class Symbols(RepoCase):
    def test_symbol_in_a_file(self) -> None:
        self.write("src/a.py", "def build_thing():\n    pass\n")
        self.assertEqual(self.problems("`src/a.py::build_thing` `src/a.py::build_thing()`"), [])

    def test_dead_symbol(self) -> None:
        self.write("src/a.py", "def build_thing():\n    pass\n")
        self.assertEqual(self.problems("`src/a.py::gone`"), ["dead symbol: src/a.py::gone"])

    def test_symbol_is_a_whole_word(self) -> None:
        self.write("src/a.py", "def build_things(): pass\n")
        self.assertEqual(self.problems("`src/a.py::build_thing`"), ["dead symbol: src/a.py::build_thing"])

    def test_nested_symbol_needs_every_part(self) -> None:
        self.write("src/a.py", "class Box:\n    def open(self): pass\n")
        self.assertEqual(self.problems("`src/a.py::Box.open` `src/a.py::Box::open`"), [])
        self.assertEqual(self.problems("`src/a.py::Box.close`"), ["dead symbol: src/a.py::Box.close"])

    def test_symbol_in_a_directory(self) -> None:
        self.write("src/deep/a.py", "def build_thing(): pass\n")
        self.write("src/b.py", "x = 1\n")
        self.assertEqual(self.problems("`src/::build_thing`"), [])
        self.assertEqual(self.problems("`src/::gone`"), ["dead symbol: src/::gone"])

    def test_unreadable_file_is_a_dead_symbol_not_a_crash(self) -> None:
        self.write("src/a.py", "def build_thing(): pass\n")
        with mock.patch.object(Path, "read_text", side_effect=OSError("denied")):
            self.assertEqual(lint._mentions(self.root / "src/a.py", "x"), False)

    def test_dead_path_before_the_symbol(self) -> None:
        self.assertEqual(self.problems("`src/gone.py::thing`"), ["dead path: src/gone.py"])

    def test_symbol_on_something_that_is_not_a_path_is_ignored(self) -> None:
        self.assertEqual(self.problems("`std::vec` `Foo::bar`"), [])


class Cli(RepoCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lint.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_clean_run(self) -> None:
        self.write("AGENTS.md", "# Agents\n")
        self.assertEqual(self.run_main(str(self.root)), (0, "agents-md: 1 repositories checked\n", ""))

    def test_problem_run(self) -> None:
        code, out, err = self.run_main(str(self.root))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("AGENTS.md is missing", err)

    def test_not_a_directory(self) -> None:
        code, _, err = self.run_main(str(self.root / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_defaults_to_the_current_directory(self) -> None:
        self.write("AGENTS.md", "# Agents\n")
        before = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, before)
        self.assertEqual(self.run_main()[0], 0)

    def test_run_as_a_script(self) -> None:
        self.write("AGENTS.md", "# Agents\n")
        argv = sys.argv
        sys.argv = ["check_agents_md.py", str(self.root)]
        self.addCleanup(setattr, sys, "argv", argv)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            runpy.run_path(str(ROOT / "scripts" / "check_agents_md.py"), run_name="__main__")
        self.assertEqual(raised.exception.code, 0)


class Workflow(unittest.TestCase):
    def test_guard_runs_the_lint_in_report_mode_by_default(self) -> None:
        self.assertIn("'agents-md':", WORKFLOW)
        self.assertIn("scripts/check_agents_md.py", WORKFLOW)
        self.assertIn("'agents-md-lint':", WORKFLOW)
        self.assertRegex(WORKFLOW, r"'agents-md-lint':(?:\n.*){3}\n\s+'default': 'report'")
        self.assertIn("agents-md-lint must be report or enforce", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
