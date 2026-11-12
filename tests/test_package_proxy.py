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


# The proxy serves container images over the OCI distribution API too, and the
# three image-building reusable workflows pull base images through it.
PROXY_REGISTRY = "packages.jorisjonkers.dev"
MIRROR_BUILD_WORKFLOWS = (
    "container-publish.yml",
    "docker-image-ci.yml",
    "crac-train.yml",
)
MIRRORED_REGISTRIES = ("docker.io", "ghcr.io", "gcr.io", "quay.io")
# A mirror line in the buildkitd config is a conditional that renders empty
# without a token, so a proxy-less build changes nothing.
GATED_MIRROR_LINE = r"^\$\{\{ steps\.proxy\.outputs\.enabled == 'true' && '.*' \|\| '' \}\}$"


class PackageProxyRegistryMirror(unittest.TestCase):
    """A build with a token pulls its base images through the proxy too.

    The proxy mirrors docker.io, ghcr.io, gcr.io and quay.io, and BuildKit
    takes a mirror's credentials from the host's Docker config, so each image
    build logs in to the proxy with docker/login-action as the user
    `packages` and names it a mirror of the four public registries in
    setup-buildx-action's inline buildkitd config. BuildKit falls back to the
    registry itself when the mirror cannot serve an image, so the estate's
    private ghcr.io/jorisjonkers-dev/* images still pull from GHCR. The
    login's `if` and every config line's conditional render empty without a
    token: no login, no mirror, nothing changes.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.steps = {}
        for name in MIRROR_BUILD_WORKFLOWS:
            doc = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
            cls.steps[name] = [
                step for job in doc["jobs"].values() for step in (job.get("steps") or [])
            ]

    def _proxy_login_indexes(self, steps: list) -> list[int]:
        return [
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("docker/login-action")
            and str((step.get("with") or {}).get("registry", "")) == PROXY_REGISTRY
        ]

    def _buildkitd_config(self, steps: list) -> str:
        setups = [
            step
            for step in steps
            if str(step.get("uses", "")).startswith("docker/setup-buildx-action")
        ]
        self.assertEqual(1, len(setups), "expected exactly one setup-buildx step")
        return str((setups[0].get("with") or {}).get("buildkitd-config-inline") or "")

    def test_each_build_logs_in_to_the_proxy_only_with_a_token(self) -> None:
        for name, steps in self.steps.items():
            with self.subTest(workflow=name):
                logins = self._proxy_login_indexes(steps)
                self.assertEqual(1, len(logins), "expected exactly one proxy login step")
                login = steps[logins[0]]
                self.assertIn("steps.proxy.outputs.enabled == 'true'", login["if"])
                self.assertEqual("packages", login["with"]["username"])
                self.assertIn("PACKAGES_PROXY_TOKEN", login["with"]["password"])

    def test_each_buildx_names_the_proxy_a_mirror_of_every_public_registry(self) -> None:
        for name, steps in self.steps.items():
            with self.subTest(workflow=name):
                inline = self._buildkitd_config(steps)
                for registry in MIRRORED_REGISTRIES:
                    self.assertIn(f'[registry."{registry}"]', inline)
                self.assertEqual(
                    len(MIRRORED_REGISTRIES), inline.count(f'mirrors = ["{PROXY_REGISTRY}"]')
                )
                self.assertNotIn("PACKAGES_PROXY_TOKEN", inline)

    def test_the_mirror_lines_exist_only_with_a_token(self) -> None:
        for name, steps in self.steps.items():
            with self.subTest(workflow=name):
                inline = self._buildkitd_config(steps)
                lines = [line.strip() for line in inline.splitlines() if line.strip()]
                self.assertEqual(2 * len(MIRRORED_REGISTRIES), len(lines))
                for line in lines:
                    self.assertRegex(line, GATED_MIRROR_LINE)

    def test_the_proxy_login_runs_before_the_build(self) -> None:
        for name, steps in self.steps.items():
            with self.subTest(workflow=name):
                logins = self._proxy_login_indexes(steps)
                self.assertEqual(1, len(logins), "expected exactly one proxy login step")
                builds = [
                    index
                    for index, step in enumerate(steps)
                    if str(step.get("uses", "")).startswith("docker/build-push-action")
                ]
                self.assertEqual(1, len(builds), "expected exactly one build step")
                self.assertLess(logins[0], builds[0])

    def test_the_proxy_login_runs_after_the_proxy_step_and_before_buildx_setup(self) -> None:
        for name, steps in self.steps.items():
            with self.subTest(workflow=name):
                logins = self._proxy_login_indexes(steps)
                self.assertEqual(1, len(logins), "expected exactly one proxy login step")
                proxy = [i for i, step in enumerate(steps) if step.get("id") == "proxy"]
                setups = [
                    i
                    for i, step in enumerate(steps)
                    if str(step.get("uses", "")).startswith("docker/setup-buildx-action")
                ]
                self.assertEqual(1, len(proxy))
                self.assertEqual(1, len(setups))
                self.assertLess(proxy[0], logins[0])
                self.assertLess(logins[0], setups[0])

    def test_every_action_in_the_mirror_workflows_is_pinned_by_full_sha(self) -> None:
        for name, steps in self.steps.items():
            with self.subTest(workflow=name):
                for step in steps:
                    uses = step.get("uses")
                    if uses is None or str(uses).startswith("./"):
                        continue
                    self.assertRegex(str(uses), r"@[0-9a-f]{40}$", f"{uses} is not SHA-pinned")


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



GRADLE_WRAPPER_STEP = "Configure Gradle wrapper credentials"


def _gradle_action_steps() -> list[dict]:
    action = yaml.safe_load((ROOT / "actions" / "setup-java-gradle" / "action.yml").read_text(encoding="utf-8"))
    return action["runs"]["steps"]


class GradleWrapperCredentialsStep(unittest.TestCase):
    """The proxy serves Gradle distributions behind Basic auth; the wrapper reads
    gradle.wrapperUser / gradle.wrapperPassword from the Gradle user home."""

    def _step(self) -> dict:
        return next((s for s in _gradle_action_steps() if s.get("name") == GRADLE_WRAPPER_STEP), {})

    def test_step_exists_is_gated_and_takes_the_token_from_env(self) -> None:
        step = self._step()
        self.assertTrue(step, "step is missing")
        self.assertIn("inputs.packages-proxy-token != ''", step.get("if", ""))
        self.assertEqual("${{ inputs.packages-proxy-token }}", step["env"]["PACKAGES_PROXY_TOKEN"])
        self.assertNotIn("${{", step["run"])
        self.assertIn("systemProp.gradle.wrapperUser=packages", step["run"])
        self.assertIn("systemProp.gradle.wrapperPassword=", step["run"])

    def test_step_masks_the_token(self) -> None:
        self.assertIn("::add-mask::", self._step().get("run", ""))


@unittest.skipUnless(shutil.which("bash"), "the Gradle step is a bash script")
class GradleWrapperCredentialsRun(unittest.TestCase):
    def _run(self, existing: str | None, *, token: str = "TESTKEY-123", user_home: bool = False):
        step = next(s for s in _gradle_action_steps() if s.get("name") == GRADLE_WRAPPER_STEP)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            gradle_home = Path(tmp) / "guh" if user_home else home / ".gradle"
            home.mkdir()
            props = gradle_home / "gradle.properties"
            if existing is not None:
                gradle_home.mkdir(parents=True)
                props.write_text(existing, encoding="utf-8")
            env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "PACKAGES_PROXY_TOKEN": token}
            if user_home:
                env["GRADLE_USER_HOME"] = str(gradle_home)
            result = subprocess.run(
                ["bash", "-c", "set -euo pipefail\numask 022\n" + step["run"]],
                env=env, check=True, capture_output=True, text=True,
            )
            text = props.read_text(encoding="utf-8") if props.exists() else None
            mode = props.stat().st_mode & 0o777 if props.exists() else None
            return text, mode, result.stdout + result.stderr, home

    def test_creates_the_file_with_both_properties_privately(self) -> None:
        text, mode, _, _ = self._run(None)
        self.assertIn("systemProp.gradle.wrapperUser=packages\n", text)
        self.assertIn("systemProp.gradle.wrapperPassword=TESTKEY-123\n", text)
        self.assertEqual(0o600, mode)

    def test_existing_lines_survive_and_old_wrapper_lines_are_replaced(self) -> None:
        text, mode, _, _ = self._run(
            "org.gradle.jvmargs=-Xmx2g\n"
            "systemProp.gradle.wrapperUser=old\n"
            "systemProp.gradle.wrapperPassword=OLD\n"
            "systemProp.http.proxyHost=example\n"
        )
        self.assertIn("org.gradle.jvmargs=-Xmx2g", text)
        self.assertIn("systemProp.http.proxyHost=example", text)
        self.assertNotIn("OLD", text)
        self.assertNotIn("wrapperUser=old", text)
        self.assertEqual(1, text.count("wrapperUser="))
        self.assertEqual(1, text.count("wrapperPassword="))
        self.assertEqual(0o600, mode)

    def test_existing_file_without_trailing_newline_keeps_its_last_line(self) -> None:
        text, _, _, _ = self._run("org.gradle.daemon=false")
        self.assertIn("org.gradle.daemon=false\n", text)
        self.assertIn("systemProp.gradle.wrapperUser=packages\n", text)

    def test_rerun_is_idempotent(self) -> None:
        text, _, _, _ = self._run("systemProp.gradle.wrapperUser=packages\nsystemProp.gradle.wrapperPassword=TESTKEY-123\n")
        self.assertEqual(1, text.count("wrapperUser="))
        self.assertEqual(1, text.count("wrapperPassword="))

    def test_gradle_user_home_is_honoured(self) -> None:
        text, mode, _, home = self._run(None, user_home=True)
        self.assertIn("wrapperPassword=TESTKEY-123", text)
        self.assertEqual(0o600, mode)
        self.assertFalse((home / ".gradle" / "gradle.properties").exists())

    def test_empty_token_writes_nothing(self) -> None:
        text, _, _, _ = self._run(None, token="")
        self.assertIsNone(text)

    def test_empty_token_leaves_an_existing_file_alone(self) -> None:
        text, _, _, _ = self._run("a=b\n", token="")
        self.assertEqual("a=b\n", text)

    def test_the_token_is_not_printed_unmasked(self) -> None:
        _, _, output, _ = self._run(None)
        for line in output.splitlines():
            if "TESTKEY-123" in line:
                self.assertTrue(line.startswith("::add-mask::"), line)


# --- Retries through a proxy restart -------------------------------------
#
# The proxy can be unreachable for a minute or two (connection refused,
# resets, a bad certificate while routes load). Every package manager the
# action configures gets a bounded retry budget instead of failing the job on
# the first refused connection.

PROXY_ACTION = "setup-package-proxy"
PROXY_ACTION_DIR = ROOT / "actions" / PROXY_ACTION
RETRY_HELPER = PROXY_ACTION_DIR / "bin" / "proxy-retry"

NPM_STEP = "Configure packages proxy for npm"
PYTHON_STEP = "Export package proxy environment"
GRADLE_RETRY_STEP = "Configure packages proxy retries for Gradle"
RETRY_PATH_STEP = "Put the retry helper on PATH"

# npm's fetch-retries/fetch-retry-* are also pnpm's fetchRetries/fetchRetry*:
# 2, 4, 8, 16, then 30s each: up to about 180s in total.
NPM_RETRY_SETTINGS = (
    "fetch-retries=9",
    "fetch-retry-factor=2",
    "fetch-retry-mintimeout=2000",
    "fetch-retry-maxtimeout=30000",
)

PYTHON_RETRY_SETTINGS = (
    "PIP_RETRIES=9",
    "UV_HTTP_RETRIES=8",
    "UV_HTTP_TIMEOUT=60",
)

# The exact property names in Gradle's source: `initial.backoff` for the
# repository retries, `initial.backOff` for the network retries.
GRADLE_RETRY_PROPERTIES = (
    "systemProp.org.gradle.internal.http.connectionTimeout=60000",
    "systemProp.org.gradle.internal.http.socketTimeout=60000",
    "systemProp.org.gradle.internal.repository.max.tentatives=8",
    "systemProp.org.gradle.internal.repository.initial.backoff=1000",
    "systemProp.org.gradle.internal.network.retry.max.attempts=8",
    "systemProp.org.gradle.internal.network.retry.initial.backOff=1000",
)

EXPRESSION_RE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def _action_doc(action: str = PROXY_ACTION) -> dict:
    return yaml.safe_load((ROOT / "actions" / action / "action.yml").read_text(encoding="utf-8"))


def _action_step(name: str, action: str = PROXY_ACTION) -> dict:
    """The step called `name`, or {} so a missing step fails with a clear message."""
    return next((s for s in _action_doc(action)["runs"]["steps"] if s.get("name") == name), {})


def _resolve_expressions(value: str, mapping: dict[str, str]) -> str:
    return EXPRESSION_RE.sub(lambda match: mapping.get(match.group(1), ""), value)


def _step_env(step: dict, mapping: dict[str, str]) -> dict[str, str]:
    """The step's own env: mapping, with its ${{ ... }} expressions resolved."""
    return {
        key: _resolve_expressions(str(value), mapping)
        for key, value in (step.get("env") or {}).items()
    }


def _run_step(step: dict, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\numask 022\n" + step["run"]],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _marked_block(text: str) -> str:
    """The lines between the packages-proxy markers, or '' when there is no block."""
    match = re.search(r"# BEGIN packages-proxy\n(.*?)# END packages-proxy", text, re.S)
    return match.group(1) if match else ""


def _base_env(tmp: str) -> dict[str, str]:
    home = Path(tmp) / "home"
    home.mkdir(exist_ok=True)
    github_env = Path(tmp) / "github_env"
    github_env.write_text("", encoding="utf-8")
    github_path = Path(tmp) / "github_path"
    github_path.write_text("", encoding="utf-8")
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "RUNNER_TEMP": tmp,
        "GITHUB_ENV": str(github_env),
        "GITHUB_PATH": str(github_path),
    }


@unittest.skipUnless(shutil.which("bash"), "the npm step is a bash script")
class PackageProxyNpmRetries(unittest.TestCase):
    """npm and pnpm retry a proxy restart on their own, from the marked block."""

    def _run(self, runs: int = 1) -> str:
        step = _action_step(NPM_STEP)
        self.assertTrue(step, f"step {NPM_STEP!r} is missing")
        with tempfile.TemporaryDirectory() as tmp:
            env = _base_env(tmp)
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            env["GITHUB_WORKSPACE"] = str(workspace)
            env.update(_step_env(step, {"inputs.token": "TESTKEY-123", "inputs.node": "true"}))
            for _ in range(runs):
                _run_step(step, env)
            return (Path(env["HOME"]) / ".npmrc").read_text(encoding="utf-8")

    def test_the_block_sets_the_fetch_retry_settings(self) -> None:
        block = _marked_block(self._run())
        self.assertTrue(block, "the npm block is missing")
        for setting in NPM_RETRY_SETTINGS:
            self.assertIn(setting + "\n", block)

    def test_two_runs_leave_exactly_one_block(self) -> None:
        text = self._run(runs=2)
        self.assertEqual(1, text.count("# BEGIN packages-proxy"))
        self.assertEqual(1, text.count("# END packages-proxy"))
        for setting in NPM_RETRY_SETTINGS:
            self.assertEqual(1, text.count(setting), setting)


@unittest.skipUnless(shutil.which("bash"), "the environment step is a bash script")
class PackageProxyPythonRetries(unittest.TestCase):
    """pip and uv retry a proxy restart through their own environment variables."""

    def _run(self, *, python: str) -> str:
        step = _action_step(PYTHON_STEP)
        self.assertTrue(step, f"step {PYTHON_STEP!r} is missing")
        with tempfile.TemporaryDirectory() as tmp:
            env = _base_env(tmp)
            env.update(_step_env(step, {"inputs.go": "true", "inputs.python": python}))
            _run_step(step, env)
            return Path(env["GITHUB_ENV"]).read_text(encoding="utf-8")

    def test_the_python_retry_variables_are_exported(self) -> None:
        text = self._run(python="true")
        for setting in PYTHON_RETRY_SETTINGS:
            self.assertIn(setting + "\n", text)

    def test_no_python_variables_without_python(self) -> None:
        text = self._run(python="false")
        for name in (
            "PIP_RETRIES",
            "UV_HTTP_RETRIES",
            "UV_HTTP_TIMEOUT",
            "PIP_INDEX_URL",
            "UV_DEFAULT_INDEX",
        ):
            self.assertNotIn(name, text)


class PackageProxyGradleRetryInput(unittest.TestCase):
    def test_the_action_declares_the_gradle_input(self) -> None:
        inputs = _action_doc().get("inputs") or {}
        self.assertIn("gradle", inputs)
        self.assertEqual("true", str(inputs["gradle"].get("default")))

    def test_the_step_runs_only_with_a_token_and_gradle_enabled(self) -> None:
        step = _action_step(GRADLE_RETRY_STEP)
        self.assertTrue(step, f"step {GRADLE_RETRY_STEP!r} is missing")
        condition = step.get("if", "")
        self.assertIn("inputs.token != ''", condition)
        self.assertIn("inputs.gradle == 'true'", condition)


@unittest.skipUnless(shutil.which("bash"), "the Gradle step is a bash script")
class PackageProxyGradleRetries(unittest.TestCase):
    """Gradle's own retries and timeouts live in the Gradle user home."""

    def _run(
        self,
        existing: str | None = None,
        *,
        user_home: bool = False,
        runs: int = 1,
    ) -> tuple[str | None, bool, bool]:
        """The written properties, plus whether the file landed in $HOME/.gradle
        and in GRADLE_USER_HOME. The temp directory is gone by the time the
        assertions run, so the two existence checks happen here."""
        step = _action_step(GRADLE_RETRY_STEP)
        self.assertTrue(step, f"step {GRADLE_RETRY_STEP!r} is missing")
        with tempfile.TemporaryDirectory() as tmp:
            env = _base_env(tmp)
            home = Path(env["HOME"])
            gradle_home = Path(tmp) / "guh" if user_home else home / ".gradle"
            props = gradle_home / "gradle.properties"
            if existing is not None:
                gradle_home.mkdir(parents=True, exist_ok=True)
                props.write_text(existing, encoding="utf-8")
            if user_home:
                env["GRADLE_USER_HOME"] = str(gradle_home)
            env.update(_step_env(step, {"inputs.token": "TESTKEY-123", "inputs.gradle": "true"}))
            for _ in range(runs):
                _run_step(step, env)
            text = props.read_text(encoding="utf-8") if props.exists() else None
            return (
                text,
                (home / ".gradle" / "gradle.properties").exists(),
                props.exists(),
            )

    def test_the_block_sets_the_six_retry_properties(self) -> None:
        text, _, _ = self._run()
        self.assertIsNotNone(text, "gradle.properties was not written")
        block = _marked_block(text)
        self.assertTrue(block, "the Gradle block is missing")
        for prop in GRADLE_RETRY_PROPERTIES:
            self.assertIn(prop + "\n", block)

    def test_unrelated_lines_survive(self) -> None:
        text, _, _ = self._run(
            "org.gradle.jvmargs=-Xmx2g\n"
            "systemProp.http.proxyHost=example\n"
            "org.gradle.daemon=false"
        )
        self.assertIn("org.gradle.jvmargs=-Xmx2g\n", text)
        self.assertIn("systemProp.http.proxyHost=example\n", text)
        self.assertIn("org.gradle.daemon=false\n", text)

    def test_a_rerun_replaces_the_block_instead_of_stacking_it(self) -> None:
        text, _, _ = self._run(runs=2)
        self.assertEqual(1, text.count("# BEGIN packages-proxy"))
        self.assertEqual(1, text.count("# END packages-proxy"))
        for prop in GRADLE_RETRY_PROPERTIES:
            self.assertEqual(1, text.count(prop), prop)

    def test_an_existing_private_file_stays_private(self) -> None:
        step = _action_step(GRADLE_RETRY_STEP)
        with tempfile.TemporaryDirectory() as tmp:
            env = _base_env(tmp)
            gradle_home = Path(env["HOME"]) / ".gradle"
            gradle_home.mkdir(parents=True)
            props = gradle_home / "gradle.properties"
            props.write_text("gpr.key=SECRET\n", encoding="utf-8")
            props.chmod(0o600)
            env.update(_step_env(step, {"inputs.token": "TESTKEY-123", "inputs.gradle": "true"}))
            _run_step(step, env)
            self.assertEqual(0o600, props.stat().st_mode & 0o777)
            self.assertIn("gpr.key=SECRET\n", props.read_text(encoding="utf-8"))

    def test_gradle_user_home_is_honoured(self) -> None:
        text, home_props, gradle_props = self._run(user_home=True)
        self.assertIn(GRADLE_RETRY_PROPERTIES[0], text)
        self.assertTrue(gradle_props, "gradle.properties is missing from GRADLE_USER_HOME")
        self.assertFalse(home_props, "gradle.properties was written to $HOME/.gradle as well")


@unittest.skipUnless(shutil.which("bash"), "the retry helper is a bash script")
class PackageProxyRetryHelper(unittest.TestCase):
    """Go has no retry of its own, so `proxy-retry` wraps the command."""

    def _run(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        env = {
            "HOME": cwd or os.environ.get("HOME", ""),
            "PATH": os.environ.get("PATH", ""),
            "PROXY_RETRY_INITIAL_DELAY": "0",
        }
        env.update(env_extra or {})
        return subprocess.run(
            [str(RETRY_HELPER), *args],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def test_the_script_is_executable(self) -> None:
        self.assertTrue(RETRY_HELPER.exists(), f"{RETRY_HELPER} is missing")
        self.assertTrue(os.access(RETRY_HELPER, os.X_OK), "proxy-retry is not executable")

    def test_zero_padded_and_oversized_attempt_counts_are_handled(self) -> None:
        for value, expected in (("08", 8), ("003", 3), ("999", 32)):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                counter = Path(tmp) / "counter"
                result = self._run(
                    ["bash", "-c", f'echo x >> "{counter}"; exit 5'],
                    cwd=tmp,
                    env_extra={"PROXY_RETRY_ATTEMPTS": value},
                )
                self.assertEqual(5, result.returncode, result.stderr)
                self.assertEqual(expected, len(counter.read_text(encoding="utf-8").splitlines()))

    def test_a_successful_command_runs_once_without_a_retry_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            counter = Path(tmp) / "counter"
            result = self._run(
                ["bash", "-c", f'echo ran >> "{counter}"'],
                cwd=tmp,
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual(1, len(counter.read_text(encoding="utf-8").splitlines()))
            self.assertNotIn("proxy-retry:", result.stderr)

    def test_two_failures_then_success_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            counter = Path(tmp) / "counter"
            command = (
                f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1)); '
                f'echo "$n" > "{counter}"; [ "$n" -ge 3 ]'
            )
            result = self._run(["bash", "-c", command], cwd=tmp)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("3", counter.read_text(encoding="utf-8").strip())
            self.assertIn(
                "proxy-retry: attempt 1/8 failed (exit 1), retrying in 0s",
                result.stderr,
            )
            self.assertIn("proxy-retry: attempt 2/8 failed (exit 1)", result.stderr)

    def test_an_always_failing_command_is_bounded_and_keeps_its_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            counter = Path(tmp) / "counter"
            result = self._run(
                ["bash", "-c", f'echo x >> "{counter}"; exit 7'],
                cwd=tmp,
                env_extra={"PROXY_RETRY_ATTEMPTS": "3"},
            )
            self.assertEqual(7, result.returncode)
            # Exactly three attempts: the loop stops at the budget, not at a timeout.
            self.assertEqual(3, len(counter.read_text(encoding="utf-8").splitlines()))
            self.assertIn("proxy-retry: attempt 1/3 failed (exit 7)", result.stderr)
            self.assertNotIn("attempt 4", result.stderr)

    def test_no_arguments_is_a_usage_error(self) -> None:
        result = self._run([])
        self.assertEqual(2, result.returncode)
        self.assertTrue(result.stderr.strip(), "expected a usage message on stderr")

    def test_garbage_env_values_fall_back_to_the_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            counter = Path(tmp) / "counter"
            command = (
                f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1)); '
                f'echo "$n" > "{counter}"; [ "$n" -ge 2 ]'
            )
            result = self._run(
                ["bash", "-c", command],
                cwd=tmp,
                env_extra={
                    "PROXY_RETRY_ATTEMPTS": "garbage",
                    "PROXY_RETRY_INITIAL_DELAY": "garbage",
                    "PROXY_RETRY_MAX_DELAY": "garbage",
                },
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("2", counter.read_text(encoding="utf-8").strip())
            # The default attempt budget, not the garbage value.
            self.assertIn("proxy-retry: attempt 1/8 failed", result.stderr)


@unittest.skipUnless(shutil.which("bash"), "the PATH step is a bash script")
class PackageProxyRetryHelperOnPath(unittest.TestCase):
    def test_the_step_appends_the_action_bin_directory(self) -> None:
        step = _action_step(RETRY_PATH_STEP)
        self.assertTrue(step, f"step {RETRY_PATH_STEP!r} is missing")
        self.assertNotIn("${{", step["run"], "the path belongs in an env var, not inline")
        condition = step.get("if", "")
        self.assertIn("inputs.token != ''", condition)
        self.assertIn("inputs.go == 'true'", condition)
        with tempfile.TemporaryDirectory() as tmp:
            env = _base_env(tmp)
            env.update(
                _step_env(
                    step,
                    {
                        "inputs.token": "TESTKEY-123",
                        "inputs.go": "true",
                        "github.action_path": str(PROXY_ACTION_DIR),
                    },
                )
            )
            _run_step(step, env)
            lines = [
                line
                for line in Path(env["GITHUB_PATH"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([str(PROXY_ACTION_DIR / "bin")], lines)
