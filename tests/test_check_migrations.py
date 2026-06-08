from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from scripts.check_migrations import main


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@contextmanager
def fixture_repo():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        run(["git", "init", "-b", "main"], repo)
        run(["git", "config", "user.email", "test@example.invalid"], repo)
        run(["git", "config", "user.name", "Test User"], repo)
        write(repo / "services/auth-api/src/main/resources/db/migration/V1__init.sql", "select 1;\n")
        write(repo / "services/auth-api/src/main/resources/db/migration/V2__next.sql", "select 2;\n")
        write(repo / "README.md", "fixture\n")
        run(["git", "add", "."], repo)
        run(["git", "commit", "-m", "base"], repo)
        run(["git", "branch", "base"], repo)
        run(["git", "checkout", "-b", "feature"], repo)
        os.chdir(repo)
        try:
            yield repo
        finally:
            os.chdir(previous)


class CheckMigrationsTest(unittest.TestCase):
    def test_allows_repo_without_matching_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            run(["git", "init", "-b", "main"], repo)
            run(["git", "config", "user.email", "test@example.invalid"], repo)
            run(["git", "config", "user.name", "Test User"], repo)
            write(repo / "README.md", "fixture\n")
            run(["git", "add", "."], repo)
            run(["git", "commit", "-m", "base"], repo)
            run(["git", "branch", "base"], repo)
            run(["git", "checkout", "-b", "feature"], repo)
            previous = Path.cwd()
            os.chdir(repo)
            try:
                self.assertEqual(main(["--base-ref", "base"]), 0)
            finally:
                os.chdir(previous)

    def test_rejects_modified_existing_migration(self) -> None:
        with fixture_repo() as repo:
            write(repo / "services/auth-api/src/main/resources/db/migration/V1__init.sql", "select 10;\n")
            run(["git", "add", "."], repo)
            run(["git", "commit", "-m", "modify migration"], repo)
            self.assertEqual(main(["--base-ref", "base"]), 1)

    def test_allows_modified_existing_migration_with_override(self) -> None:
        with fixture_repo() as repo:
            write(repo / "services/auth-api/src/main/resources/db/migration/V1__init.sql", "select 10;\n")
            run(["git", "add", "."], repo)
            run(["git", "commit", "-m", "modify migration"], repo)
            self.assertEqual(main(["--base-ref", "base", "--allow-change"]), 0)

    def test_rejects_new_migration_below_base_max(self) -> None:
        with fixture_repo() as repo:
            write(repo / "services/auth-api/src/main/resources/db/migration/V1_1__late.sql", "select 11;\n")
            run(["git", "add", "."], repo)
            self.assertEqual(main(["--base-ref", "base"]), 1)

    def test_allows_new_migration_above_base_max(self) -> None:
        with fixture_repo() as repo:
            write(repo / "services/auth-api/src/main/resources/db/migration/V3__new.sql", "select 3;\n")
            run(["git", "add", "."], repo)
            self.assertEqual(main(["--base-ref", "base"]), 0)

    def test_rejects_duplicate_versions_across_split_dirs(self) -> None:
        with fixture_repo() as repo:
            write(repo / "services/auth-api/src/main/resources/db/migration-pg/V2__pg.sql", "select 2;\n")
            run(["git", "add", "."], repo)
            self.assertEqual(main(["--base-ref", "base"]), 1)

    def test_custom_scope_regex_groups_by_database_directory(self) -> None:
        with fixture_repo() as repo:
            shutil.rmtree(repo / "services")
            write(repo / "database/main/migrations/V1__init.sql", "select 1;\n")
            run(["git", "add", "-A"], repo)
            run(["git", "commit", "-m", "replace layout"], repo)
            run(["git", "branch", "-f", "base"], repo)
            write(repo / "database/main/migrations/V1__dupe.sql", "select 1;\n")
            run(["git", "add", "."], repo)
            self.assertEqual(
                main(
                    [
                        "--base-ref",
                        "base",
                        "--migration-regex",
                        r"database/[^/]+/migrations/V[0-9][^/]*\.sql$",
                        "--scope-regex",
                        r"(database/[^/]+)/.*",
                    ]
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
