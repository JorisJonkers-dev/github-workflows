"""The packages proxy token must reach builds only through deliberate channels.

Two rules are pinned here so they cannot regress in workflows or composite
actions:

1. Upload cooldown. Every `pip install` command line in the workflows and
   actions must pass `--uploaded-prior-to` (7-day cooldown keeping
   brand-new releases out of CI), or be the pinned pip upgrade itself
   (`pip install --disable-pip-version-check "pip==<version>"`); Renovate
   moves that pin. Checked line by line on the raw text, because folded
   scalars (python-ci.yml's install-command default) span several lines.

2. No token prints. Within any run block (or shell script), a line that
   both references the token and is an echo/printf must not let the token
   reach stdout. Allowed: the `printf '::add-mask::%s\n'` masking line,
   echo/printf lines redirected into a file (`>` / `>>`), and any such
   line inside a `{ ... } > file` group such as the netrc writer, whose
   redirect is on the closing brace. Other references (env mappings, if
   conditions, Gradle reading $PACKAGES_PROXY_TOKEN) are not prints and
   are never flagged.

3. No token in build args. No `build-args:` value in any workflow or
   action may name PACKAGES_PROXY_TOKEN, pass the token itself
   (`secrets.PACKAGES_PROXY_TOKEN` interpolated as a value), or carry any
   `${{ secrets.* }}` reference at all: values in build args are baked
   into image layers. The only allowed mention is the emptiness check
   `secrets.PACKAGES_PROXY_TOKEN != ''` that gates the conditional proxy
   values.

4. No token in proxy URLs. The setup-package-proxy and setup-java-gradle
   actions must not embed the token in a GOPROXY / PIP_INDEX_URL /
   UV_DEFAULT_INDEX value or in a packages.jorisjonkers.dev URL.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

WORKFLOW_FILES = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTION_FILES = sorted((ROOT / "actions").rglob("action.yml"))
SCRIPT_FILES = sorted((ROOT / "actions").rglob("*.sh"))

# The pip upgrade pin the cooldown rule allows instead of a cooldown flag.
PINNED_PIP_UPGRADE_RE = re.compile(
    r"pip install .*--disable-pip-version-check.*['\"]pip==[^'\"]+['\"]"
)

# Any way a line can use the token or its secret/input source.
TOKEN_REF_RE = re.compile(
    r"\$\{?PACKAGES_PROXY_TOKEN"
    r"|secrets\.PACKAGES_PROXY_TOKEN"
    r"|inputs\.packages-proxy-token"
)

# A per-line redirect into a file. The `> file` of a brace group sits on
# the closing brace and is handled by the group tracking instead.
REDIRECT_RE = re.compile(r">>?\s*\S")

REDIRECT_GROUP_CLOSE_RE = re.compile(r"^\}\s*(>>?)\s*\S")

# The only allowed mention of the token source in a build-args value:
# checking that the token is set, never passing it.
BUILD_ARG_TOKEN_CHECK_RE = re.compile(r"secrets\.PACKAGES_PROXY_TOKEN\s*!=")


def pip_install_offenses(text: str) -> list[str]:
    """Describe each `pip install` line missing the cooldown or the pin."""
    findings: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("#") or "pip install" not in stripped:
            continue
        if "--uploaded-prior-to" in stripped:
            continue
        if PINNED_PIP_UPGRADE_RE.search(stripped):
            continue
        findings.append(f"line {lineno}: {stripped}")
    return findings


def _print_suspect(line: str) -> bool:
    """True for a line that is an unredirected, unmasked token print."""
    if not TOKEN_REF_RE.search(line):
        return False
    if "::add-mask::" in line:
        return False
    if not line.startswith(("echo", "printf")):
        return False
    return not REDIRECT_RE.search(line)


def stdout_token_lines(lines: list[str]) -> list[int]:
    """Line numbers of echo/printf lines that would print the token to stdout."""
    offending: list[int] = []
    group_open = False
    group_pending: list[int] = []
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if group_open:
            if REDIRECT_GROUP_CLOSE_RE.match(stripped):
                group_pending.clear()
                group_open = False
            elif stripped == "}":
                # A bare closing brace means the group printed to stdout.
                offending.extend(group_pending)
                group_pending = []
                group_open = False
            elif _print_suspect(stripped):
                group_pending.append(lineno)
            continue
        if stripped == "{":
            group_open = True
            group_pending = []
            continue
        if _print_suspect(stripped):
            offending.append(lineno)
    if group_open:
        # An unclosed group is untrusted: treat as if it printed.
        offending.extend(group_pending)
    return offending


def collect_run_blocks(node: object) -> list[str]:
    """Every run: block value in a parsed workflow or action document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from collect_run_blocks(value)
    elif isinstance(node, list):
        for item in node:
            yield from collect_run_blocks(item)


def collect_build_arg_values(node: object) -> list[str]:
    """Every build-args: value, flattened when it is a multi-line |- block."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "build-args":
                if isinstance(value, list):
                    yield "\n".join(str(item) for item in value)
                elif value is not None:
                    yield str(value)
            else:
                yield from collect_build_arg_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from collect_build_arg_values(item)


def build_arg_offense(value: str) -> str | None:
    """Why a build-args value breaks the token rule, or None if clean."""
    remainder = BUILD_ARG_TOKEN_CHECK_RE.sub("", value)
    if "PACKAGES_PROXY_TOKEN" in remainder:
        return "names the token, not only the emptiness check"
    if "secrets." in remainder:
        return "interpolates a secrets.* reference"
    return None


PROXY_URL_RE = re.compile(r"packages\.jorisjonkers\.dev|(GOPROXY|PIP_INDEX_URL|UV_DEFAULT_INDEX)=")


class PackageProxyPipCooldown(unittest.TestCase):
    def test_every_pip_install_has_the_cooldown_or_pin(self) -> None:
        failures = []
        for path in WORKFLOW_FILES + ACTION_FILES + SCRIPT_FILES:
            rel = path.relative_to(ROOT)
            findings = pip_install_offenses(path.read_text(encoding="utf-8"))
            failures += [f"{rel}: {finding}" for finding in findings]
        self.assertEqual([], failures)


class PackageProxyTokenPrinting(unittest.TestCase):
    def test_no_run_block_prints_the_token(self) -> None:
        self._assert_no_token_prints(WORKFLOW_FILES + ACTION_FILES)

    def test_no_shell_script_prints_the_token(self) -> None:
        self._assert_no_token_prints(SCRIPT_FILES)

    def _assert_no_token_prints(self, paths: list[Path]) -> None:
        failures = []
        for path in paths:
            rel = path.relative_to(ROOT)
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".sh":
                run_blocks = [text]
            else:
                run_blocks = collect_run_blocks(yaml.safe_load(text))
            for block in run_blocks:
                for lineno in stdout_token_lines(block.splitlines()):
                    line = block.splitlines()[lineno - 1].strip()
                    failures.append(f"{rel}: line {lineno}: {line}")
        self.assertEqual([], failures)

    def test_no_token_in_build_args(self) -> None:
        failures = []
        for path in WORKFLOW_FILES + ACTION_FILES:
            rel = path.relative_to(ROOT)
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for value in collect_build_arg_values(doc):
                reason = build_arg_offense(value)
                if reason:
                    failures.append(f"{rel}: build-args {reason}: {value.strip()}")
        self.assertEqual([], failures)


class PackageProxyBuildKitSecretFiles(unittest.TestCase):
    """docker/build-push-action's `secrets` input takes name=value pairs only, so a
    `id=<name>,src=<file>` entry there mounts nothing. The proxy's netrc and npmrc are
    files, so they belong in `secret-files` as `<name>=<path>`."""

    def test_proxy_secret_files_use_the_secret_files_input(self) -> None:
        failures = []
        for path in WORKFLOW_FILES + ACTION_FILES:
            rel = path.relative_to(ROOT)
            text = path.read_text(encoding="utf-8")
            for lineno, raw in enumerate(text.splitlines(), 1):
                if re.search(r"id=packages-(?:netrc|npmrc),src=", raw):
                    failures.append(f"{rel}: line {lineno}: {raw.strip()}")
            if "packages-netrc=" in text and "secret-files:" not in text:
                failures.append(f"{rel}: names packages-netrc but has no secret-files input")
        self.assertEqual([], failures)

class PackageProxyTokenNotInProxyUrls(unittest.TestCase):
    def test_setup_actions_keep_token_out_of_proxy_urls(self) -> None:
        paths = [
            ROOT / "actions" / "setup-package-proxy" / "action.yml",
            ROOT / "actions" / "setup-java-gradle" / "action.yml",
        ]
        failures = []
        for path in paths:
            rel = path.relative_to(ROOT)
            for lineno, raw in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if PROXY_URL_RE.search(raw) and "PACKAGES_PROXY_TOKEN" in raw:
                    failures.append(f"{rel}: line {lineno}: {raw.strip()}")
        self.assertEqual([], failures)


@unittest.skipUnless(shutil.which("bash"), "the netrc step is a bash script")
class PackageProxyNetrcRewrite(unittest.TestCase):
    """The netrc step rewrites ~/.netrc. Entries it does not own must survive, including a default entry."""

    def _run_step(self, existing: str) -> str:
        action = yaml.safe_load((ROOT / "actions" / "setup-package-proxy" / "action.yml").read_text(encoding="utf-8"))
        step = next(s for s in action["runs"]["steps"] if s.get("name") == "Write packages proxy netrc")
        with tempfile.TemporaryDirectory() as home:
            netrc = Path(home) / ".netrc"
            netrc.write_text(existing, encoding="utf-8")
            env = {
                "HOME": home,
                "PATH": os.environ.get("PATH", ""),
                "RUNNER_TEMP": home,
                "PACKAGES_PROXY_TOKEN": "TESTKEY-123",
            }
            subprocess.run(
                # umask 022 is a runner's usual default: the file must still end up private.
                ["bash", "-c", "set -euo pipefail\numask 022\n" + step["run"]],
                env=env,
                check=True,
                capture_output=True,
            )
            self.mode = netrc.stat().st_mode & 0o777
            return netrc.read_text(encoding="utf-8")

    def test_the_rewritten_netrc_is_readable_only_by_its_owner(self) -> None:
        self._run_step("machine other.example.com\nlogin me\npassword pw\n")
        self.assertEqual(0o600, self.mode)

    def test_leading_default_entry_survives(self) -> None:
        text = self._run_step(
            "default login anon password none\n"
            "machine other.example.com\nlogin me\npassword pw\n"
            "machine packages.jorisjonkers.dev\nlogin packages\npassword OLD\n"
        )
        self.assertIn("default login anon password none", text)
        self.assertIn("machine other.example.com", text)
        self.assertNotIn("OLD", text)
        self.assertEqual(1, text.count("machine packages.jorisjonkers.dev"))

    def test_trailing_default_entry_after_the_proxy_entry_survives(self) -> None:
        text = self._run_step(
            "machine packages.jorisjonkers.dev\nlogin packages\npassword OLD\n"
            "default login anon password none\n"
        )
        self.assertIn("default login anon password none", text)
        self.assertNotIn("OLD", text)


@unittest.skipUnless(shutil.which("bash"), "the npm step is a bash script")
class PackageProxyNpmConfigLocation(unittest.TestCase):
    """The npm block goes to NPM_CONFIG_USERCONFIG outside the workspace, and to $HOME/.npmrc otherwise."""

    ACTIONS = ("setup-package-proxy", "setup-node")

    def _run(self, action_name: str, *, userconfig: str | None) -> tuple[str, str, str, str]:
        action = yaml.safe_load((ROOT / "actions" / action_name / "action.yml").read_text(encoding="utf-8"))
        step = next(s for s in action["runs"]["steps"] if s.get("name") == "Configure packages proxy for npm")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            workspace = Path(tmp) / "ws"
            home.mkdir()
            workspace.mkdir()
            github_env = Path(tmp) / "github_env"
            github_env.write_text("", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PATH": os.environ.get("PATH", ""),
                "RUNNER_TEMP": tmp,
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_ENV": str(github_env),
                "PACKAGES_PROXY_TOKEN": "TESTKEY-123",
            }
            if userconfig is not None:
                env["NPM_CONFIG_USERCONFIG"] = userconfig.replace("WS", str(workspace)).replace("TMP", tmp)
                Path(env["NPM_CONFIG_USERCONFIG"]).parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["bash", "-c", "set -euo pipefail\numask 022\n" + step["run"]],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            written = [f for f in (home / ".npmrc", Path(env.get("NPM_CONFIG_USERCONFIG", home / ".npmrc"))) if f.exists()]
            self.modes = {f.stat().st_mode & 0o777 for f in written}
            home_npmrc = (home / ".npmrc").read_text(encoding="utf-8") if (home / ".npmrc").exists() else ""
            target = env.get("NPM_CONFIG_USERCONFIG")
            target_text = Path(target).read_text(encoding="utf-8") if target and Path(target).exists() else ""
            return home_npmrc, target_text, github_env.read_text(encoding="utf-8"), result.stdout + result.stderr

    def test_the_npm_config_holding_the_key_is_readable_only_by_its_owner(self) -> None:
        for name in self.ACTIONS:
            for userconfig in (None, "TMP/runner/.npmrc"):
                with self.subTest(action=name, userconfig=userconfig):
                    self._run(name, userconfig=userconfig)
                    self.assertEqual({0o600}, self.modes)

    def test_unset_writes_home_npmrc(self) -> None:
        for name in self.ACTIONS:
            with self.subTest(action=name):
                home_npmrc, _, github_env, _ = self._run(name, userconfig=None)
                self.assertIn("# BEGIN packages-proxy", home_npmrc)
                self.assertIn("//packages.jorisjonkers.dev/npm/:_authToken=TESTKEY-123", home_npmrc)
                self.assertNotIn("NPM_CONFIG_USERCONFIG", github_env)

    def test_outside_the_workspace_writes_that_file(self) -> None:
        # A sibling whose name starts with the workspace path is still outside it.
        for name in self.ACTIONS:
            with self.subTest(action=name):
                home_npmrc, target_text, github_env, _ = self._run(name, userconfig="TMP/runner/.npmrc")
                self.assertIn("# BEGIN packages-proxy", target_text)
                self.assertNotIn("# BEGIN packages-proxy", home_npmrc)
                self.assertNotIn("NPM_CONFIG_USERCONFIG", github_env)
                sibling_home, sibling_target, _, _ = self._run(name, userconfig="WS-extra/.npmrc")
                self.assertIn("# BEGIN packages-proxy", sibling_target)
                self.assertNotIn("# BEGIN packages-proxy", sibling_home)

    def test_inside_the_workspace_writes_home_and_exports_it(self) -> None:
        for name in self.ACTIONS:
            with self.subTest(action=name):
                home_npmrc, target_text, github_env, output = self._run(name, userconfig="WS/.npmrc")
                self.assertIn("# BEGIN packages-proxy", home_npmrc)
                self.assertEqual("", target_text)
                self.assertIn("NPM_CONFIG_USERCONFIG=", github_env)
                self.assertIn("::warning::NPM_CONFIG_USERCONFIG points inside the workspace", output)

