from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import package_proxy_guard as guard

REPO_ROOT = Path(__file__).resolve().parents[1]
# Reusable workflows that install packages but are not in the guard's list: the guard itself
# installs only its own PyYAML, a tool bootstrap rather than caller packages.
GUARD_OWN_WORKFLOWS = frozenset({"repository-hygiene-guard.yml"})
INSTALL_ACTION = re.compile(
    r"^(?:\./\.github-workflows|JorisJonkers-dev/github-workflows)/actions/"
    r"(?:setup-node|setup-java-gradle|setup-package-proxy|path-buckets|api-client-publish)(?:@\S+)?$"
)

CLEAN = {
    ".github/workflows/ci.yml": """\
name: CI
on: push
jobs:
  node:
    steps:
      - uses: actions/checkout@v7
      - uses: JorisJonkers-dev/github-workflows/actions/setup-node@v6
        with:
          packages-proxy-token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
      - run: npm ci
  go:
    steps:
      - uses: JorisJonkers-dev/github-workflows/actions/setup-package-proxy@v0.44.0
        with:
          token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
      - run: go mod download
  jvm:
    steps:
      - uses: ./.github-workflows/actions/setup-java-gradle
        with:
          packages-proxy-token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
      - run: ./gradlew test
  call:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/node-ci.yml@v0.44.0
    secrets: inherit
  shared:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/python-ci.yml@v0.44.0
    secrets:
      PACKAGES_PROXY_TOKEN: ${{ secrets.PACKAGES_PROXY_TOKEN }}
  inline:
    steps:
      - run: printf 'machine packages.jorisjonkers.dev\\n' >> "$HOME/.netrc"
      - run: pip install --uploaded-prior-to P7D -r requirements.txt
  hygiene:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/repository-hygiene-guard.yml@v0.44.0
  add:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/add-to-project.yml@v0.44.0
  rel:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/release.yml@v0.44.0
""",
    "Dockerfile": """\
# A build with the proxy wired in.
FROM golang:1.25 AS build
WORKDIR /app
ARG GOPROXY
RUN --mount=type=secret,id=packages-netrc,target=/root/.netrc,required=false \\
    go mod download
RUN echo ready
""",
    ".npmrc": "registry=https://packages.jorisjonkers.dev/npm/\n",
    "build.gradle.kts": """\
repositories {
    mavenCentral()
    // maven { url = uri("https://example.com/commented") }
    maven { url = uri("https://maven.pkg.github.com/JorisJonkers-dev/openapi") }
}
""",
    "README.md": "# Clean repository\n",
    ".github/package-proxy-allow.txt": "docs/** Example text only, nothing is installed.\n",
}

UNCOVERED = {
    ".github/workflows/ci.yml": """\
name: CI
on: push
jobs:
  npm:
    steps:
      - uses: JorisJonkers-dev/github-workflows/actions/setup-node@v6
      - run: npm ci
  pip:
    steps:
      - run: |
          python -m pip install -r requirements.txt
          echo ok
  empty-token:
    steps:
      - uses: JorisJonkers-dev/github-workflows/actions/setup-package-proxy@v0.44.0
        with:
          token: ''
      - run: cargo build
  gradle:
    steps:
      - run: ./gradlew test
  maven:
    steps:
      - run: mvn -B verify
  pnpm:
    steps:
      - run: pnpm install
  wrapper:
    steps:
      - run: ./mvnw verify
  go-tools:
    steps:
      - run: go install ./cmd/tool
      - run: go mod tidy
  reusable-no-token:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/node-ci.yml@v0.44.0
    secrets:
      packages-token: ${{ secrets.GITHUB_TOKEN }}
  local-reusable:
    uses: ./.github/workflows/shared.yml
  late-cover:
    steps:
      - run: npm ci
      - uses: JorisJonkers-dev/github-workflows/actions/setup-package-proxy@v0.44.0
        with:
          token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
  no-steps: {}
  reusable-bare:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/node-ci.yml@v0.44.0
  buckets:
    uses: JorisJonkers-dev/github-workflows/.github/workflows/path-buckets.yml@v0.44.0
""",
    ".github/workflows/legacy.yml": """\
name: Legacy
on: push
jobs:
  go:
    steps:
      - run: go test ./...
""",
    ".github/workflows/broken.yml": "jobs: [unclosed\n",
    "Dockerfile": """\
# Installs without the proxy.

FROM golang:1.25 AS first
ARG GOPROXY
RUN --mount=type=secret,id=packages-netrc,target=/root/.netrc go mod download
FROM golang:1.25
RUN go mod download
RUN --mount=type=secret,id=packages-netrc,target=/root/.netrc pip install -r r.txt
RUN uv sync \\
    --frozen
RUN npm ci
""",
    ".npmrc": "registry=https://registry.npmjs.org/\n",
    "node_modules/some-package/.npmrc": "registry=https://registry.example.com/\n",
    "build.gradle.kts": """\
repositories {
    maven { url = uri("https://repo.example.com/maven") }
    maven("https://maven.pkg.github.com/JorisJonkers-dev/x")
    // maven { url = uri("https://comment.example.com") }
}
""",
    ".github/package-proxy-allow.txt": """\
# Comment lines are ignored.
.github/workflows/legacy.yml vendored job, pinned by the workspace
.github/workflows/noreason.yml
""",
}


def make_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="package-proxy-guard-"))
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def run_guard(root: Path, *, report_only: bool, allowlist: str = guard.DEFAULT_ALLOWLIST) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = guard.run(root, allowlist, report_only)
    return code, out.getvalue()


class CleanTree(unittest.TestCase):
    def test_clean_checkout_has_no_findings(self) -> None:
        root = make_tree(CLEAN)
        code, output = run_guard(root, report_only=False)
        self.assertEqual(0, code)
        self.assertEqual("package-proxy-guard: 0 finding(s) in 0 file(s), 0 allowlisted, mode enforce\n", output)


class LocalSetupActions(unittest.TestCase):
    def test_covering_uses_accepts_this_repositories_own_actions(self) -> None:
        self.assertTrue(guard.COVERING_USES.match("./actions/setup-package-proxy"))
        self.assertTrue(guard.COVERING_USES.match("./actions/setup-package-proxy@v0.45.0"))
        self.assertTrue(guard.COVERING_USES.match("./actions/setup-java-gradle"))

    def test_covering_uses_rejects_other_local_actions(self) -> None:
        self.assertIsNone(guard.COVERING_USES.match("./actions/setup-python"))
        self.assertIsNone(guard.COVERING_USES.match("./actions/setup-package-proxy-x"))

    def test_local_setup_package_proxy_covers_a_pip_install(self) -> None:
        files = {
            ".github/workflows/ci.yml": """\
name: CI
on: push
jobs:
  python:
    steps:
      - uses: actions/checkout@v7
      - uses: ./actions/setup-package-proxy
        with:
          token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
      - run: pip install x
"""
        }
        root = make_tree(files)
        findings, _ = guard.collect(root, guard.DEFAULT_ALLOWLIST)
        self.assertEqual([], [f for f in findings if f.rule == "uncovered-install"])

    def test_local_setup_java_gradle_covers_gradlew(self) -> None:
        files = {
            ".github/workflows/ci.yml": """\
name: CI
on: push
jobs:
  jvm:
    steps:
      - uses: actions/checkout@v7
      - uses: ./actions/setup-java-gradle
        with:
          token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
      - run: ./gradlew test
"""
        }
        root = make_tree(files)
        findings, _ = guard.collect(root, guard.DEFAULT_ALLOWLIST)
        self.assertEqual([], [f for f in findings if f.rule == "uncovered-install"])


class UncoveredTree(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tree(UNCOVERED)
        self.findings, self.suppressed = guard.collect(self.root, guard.DEFAULT_ALLOWLIST)

    def at(self, path: str, rule: str) -> list[int]:
        return [f.line for f in self.findings if f.path == path and f.rule == rule]

    def test_uncovered_installs_report_each_line(self) -> None:
        ci = ".github/workflows/ci.yml"
        # npm ci (setup-node without a token), pip, cargo (empty token), gradlew, mvn, pnpm,
        # ./mvnw, go install, go mod tidy, and npm ci before the proxy step (order matters).
        self.assertEqual([7, 11, 18, 21, 24, 27, 30, 33, 34, 43], self.at(ci, "uncovered-install"))
        messages = [f.message for f in self.findings if f.path == ci and f.rule == "uncovered-install"]
        self.assertTrue(messages[0].startswith("npm ci runs outside the packages proxy"))
        self.assertTrue(messages[1].startswith("pip install runs outside the packages proxy"))

    def test_block_scalar_line_numbers_are_real_lines(self) -> None:
        # run: | starts on line 10; its first content line is line 11.
        self.assertIn(11, self.at(".github/workflows/ci.yml", "uncovered-install"))

    def test_reusable_calls_without_the_token_are_reported(self) -> None:
        # Line 36 calls node-ci with packages-token only; line 49 calls node-ci with no secrets;
        # line 51 calls path-buckets with no secrets. The local call on line 40 is not an installer.
        self.assertEqual([36, 49, 51], self.at(".github/workflows/ci.yml", "reusable-without-token"))

    def test_dockerfile_findings_are_scoped_per_stage(self) -> None:
        dockerfile = "Dockerfile"
        # Line 7 is in a new stage, so GOPROXY from the first stage does not count there.
        # Line 8: pip without ARG PIP_INDEX_URL. Lines 9 (uv) and 11 (npm) lack their mounts.
        self.assertEqual([7, 7, 8, 9, 9, 11], self.at(dockerfile, "dockerfile-netrc"))
        messages = [f.message for f in self.findings if f.path == dockerfile]
        self.assertTrue(any("does not declare ARG GOPROXY" in m for m in messages))
        self.assertTrue(any("does not declare ARG PIP_INDEX_URL" in m for m in messages))
        self.assertTrue(any("does not declare ARG UV_DEFAULT_INDEX" in m for m in messages))
        self.assertTrue(any("without the packages-npmrc secret mount" in m for m in messages))
        self.assertTrue(any("without the packages-netrc secret mount" in m for m in messages))

    def test_npmrc_and_gradle_findings(self) -> None:
        self.assertEqual([1], self.at(".npmrc", "npmrc-registry"))
        self.assertEqual([2], self.at("build.gradle.kts", "gradle-repository"))
        self.assertEqual([], self.at("node_modules/some-package/.npmrc", "npmrc-registry"))

    def test_allowlist_suppresses_and_reports_missing_reason(self) -> None:
        self.assertEqual(1, self.suppressed)
        self.assertEqual([], [f for f in self.findings if f.path == ".github/workflows/legacy.yml"])
        self.assertEqual([3], self.at(".github/package-proxy-allow.txt", "allowlist"))

    def test_unparsable_workflow_is_reported_not_raised(self) -> None:
        self.assertEqual([1], self.at(".github/workflows/broken.yml", "unparsable"))

    def test_report_only_exits_zero_and_enforce_exits_one(self) -> None:
        code, output = run_guard(self.root, report_only=True)
        self.assertEqual(0, code)
        self.assertIn("mode report", output)
        code, output = run_guard(self.root, report_only=False)
        self.assertEqual(1, code)
        self.assertIn("mode enforce", output)
        self.assertIn("by rule:", output)

    def test_summary_counts(self) -> None:
        _, output = run_guard(self.root, report_only=True)
        self.assertIn("package-proxy-guard: by rule:", output)
        self.assertIn("23 finding(s) in 6 file(s), 1 allowlisted, mode report", output.splitlines()[-1])


def workflow_installs(doc: object) -> bool:
    """True when a parsed workflow runs an installer or sets up packages itself."""
    if not isinstance(doc, dict):
        return False
    for job in (doc.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses") or ""
            if INSTALL_ACTION.match(uses) or uses.startswith("docker/build-push-action"):
                return True
            if any(guard.match_installer(line) for line in (step.get("run") or "").splitlines()):
                return True
    return False


def derive_installing_workflows(root: Path) -> set[str]:
    """Reusable workflows in root that install packages, minus the guard's own bootstrap."""
    derived: set[str] = set()
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
        if isinstance(on, dict) and "workflow_call" in on and workflow_installs(doc):
            if path.name not in GUARD_OWN_WORKFLOWS:
                derived.add(path.name)
    return derived


class InstallingWorkflows(unittest.TestCase):
    def test_guard_list_matches_this_repositorys_installing_workflows(self) -> None:
        self.assertEqual(derive_installing_workflows(REPO_ROOT), set(guard.INSTALLING_WORKFLOWS))

    def test_the_named_installing_workflows_are_listed(self) -> None:
        named = {
            "node-ci.yml", "jvm-ci.yml", "python-ci.yml", "container-publish.yml",
            "publish-fragment.yml", "mutation-report.yml", "publish-api-clients.yml", "path-buckets.yml",
        }
        self.assertTrue(named <= set(guard.INSTALLING_WORKFLOWS))

    def test_add_to_project_repository_hygiene_guard_and_release_are_not_listed(self) -> None:
        for name in ("add-to-project.yml", "repository-hygiene-guard.yml", "release.yml"):
            self.assertNotIn(name, guard.INSTALLING_WORKFLOWS)

    def test_calls_to_non_installing_workflows_are_not_flagged_fixture(self) -> None:
        # CLEAN calls add-to-project, repository-hygiene-guard and release with no secrets.
        root = make_tree(CLEAN)
        findings, _ = guard.collect(root, guard.DEFAULT_ALLOWLIST)
        self.assertEqual([], [f for f in findings if f.rule == "reusable-without-token"])


class Allowlist(unittest.TestCase):
    def test_missing_allowlist_suppresses_nothing(self) -> None:
        files = {k: v for k, v in UNCOVERED.items() if not k.startswith(".github/package-proxy-allow")}
        root = make_tree(files)
        findings, suppressed = guard.collect(root, guard.DEFAULT_ALLOWLIST)
        self.assertEqual(0, suppressed)
        self.assertNotIn("allowlist", {f.rule for f in findings})
        self.assertTrue(any(f.path == ".github/workflows/legacy.yml" for f in findings))

    def test_alternative_allowlist_path_is_read(self) -> None:
        files = dict(UNCOVERED)
        files["config/allow.txt"] = ".github/workflows/legacy.yml pinned\n"
        root = make_tree(files)
        findings, suppressed = guard.collect(root, "config/allow.txt")
        self.assertEqual(1, suppressed)
        self.assertFalse(any(f.path == ".github/workflows/legacy.yml" for f in findings))


class Main(unittest.TestCase):
    def test_main_runs_against_a_root_and_returns_the_exit_code(self) -> None:
        root = make_tree(CLEAN)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, guard.main(["--root", str(root)]))
        bad = make_tree(UNCOVERED)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, guard.main(["--root", str(bad), "--report-only"]))
            self.assertEqual(1, guard.main(["--root", str(bad)]))

    def test_usage_error_exits_two(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            guard.main(["--no-such-flag"])
        self.assertEqual(2, raised.exception.code)


class Helpers(unittest.TestCase):
    def test_match_installer_returns_none_for_plain_commands(self) -> None:
        self.assertIsNone(guard.match_installer("echo hello"))

    def test_match_installer_names_the_manager_and_index(self) -> None:
        hit = guard.match_installer("RUN uv pip install -r x")
        self.assertEqual(("python", "UV_DEFAULT_INDEX"), (hit.manager, hit.index))

    def test_dockerfile_instructions_skip_comments_and_join_continuations(self) -> None:
        text = "# comment\n\nRUN a \\\n    b\n"
        self.assertEqual([(3, "RUN a b")], list(guard.dockerfile_instructions(text)))

    def test_dockerfile_instructions_flush_an_unfinished_continuation(self) -> None:
        self.assertEqual([(1, "RUN a")], list(guard.dockerfile_instructions("RUN a \\\n")))

    def test_is_dockerfile_matches_the_usual_names(self) -> None:
        self.assertTrue(guard.is_dockerfile("Dockerfile"))
        self.assertTrue(guard.is_dockerfile("Dockerfile.dev"))
        self.assertTrue(guard.is_dockerfile("api.Dockerfile"))
        self.assertTrue(guard.is_dockerfile("api.dockerfile"))
        self.assertFalse(guard.is_dockerfile("README.md"))

    def test_load_allowlist_skips_blank_and_comment_lines(self) -> None:
        root = make_tree({"allow.txt": "\n# note\nsome/path reason here\n"})
        entries, problems = guard.load_allowlist(root / "allow.txt", "allow.txt")
        self.assertEqual([guard.Allow("some/path", "reason here")], entries)
        self.assertEqual([], problems)

    def test_workflow_without_jobs_has_no_findings(self) -> None:
        findings: list[guard.Finding] = []
        guard.scan_workflow(".github/workflows/empty.yml", "name: nothing\n", findings)
        self.assertEqual([], findings)

    def test_scan_file_ignores_unrelated_files(self) -> None:
        root = make_tree({"README.md": "npm ci\n"})
        findings: list[guard.Finding] = []
        guard.scan_file("README.md", root / "README.md", findings)
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
