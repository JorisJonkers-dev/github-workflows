"""
Regression tests for deploy-config-schema 0.16.0 CLI interface drift.

Test groups:
  T-CLI1: Recorded-interface tests — parse run.sh invocations and compare the
          flag sets used against the checked-in interface spec
          (tests/fixtures/deploy-artifact/cli-interface-0.16.0.json).
  T-CLI2: Count-parsing helper — verify the count_lines pattern (grep -c with
          || true instead of || echo 0) does not produce a two-line value.
  T-CLI3: deploy-preview positional args — second positional must be the
          deploy-dir, not the deployment.yml path.
  T-CLI4: deploy-artifact positional args — same constraint.
  T-CLI5: Silent || true removal — render and emit-contract in deploy-preview
          must not use unconditional `|| true`; failures must be captured.
  T-CLI6: Context package handling — both actions pull the context by digest
          via oras (service repos never hold the context), discover
          cluster-context-public.yml in the pulled tree via the
          find_cluster_context helper, and pass the digest ref to --context
          with the pulled file as --context-path.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEPLOY_PREVIEW_RUN = ROOT / "actions/deploy-preview/run.sh"
DEPLOY_ARTIFACT_RUN = ROOT / "actions/deploy-artifact/run.sh"
CLI_INTERFACE_SPEC = (
    ROOT / "tests/fixtures/deploy-artifact/cli-interface-0.16.0.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_script(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_spec() -> dict:
    with CLI_INTERFACE_SPEC.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _extract_render_fragment_invocations(text: str) -> list[str]:
    """
    Return lines (with continuations joined) that contain
    'deploy-config-schema render <fragment-id-or-variable>' patterns.

    The fragment-id may be a literal like 'kubernetes-workload-fragment' or a
    shell variable like '"$fragment"' (loop variable iterating over the list).
    """
    # Join line continuations so each logical invocation is one string
    joined = text.replace("\\\n", " ")
    lines = []
    for line in joined.splitlines():
        stripped = line.strip()
        # Matches both literal fragment ids and loop variables
        if re.search(
            r'deploy-config-schema\s+render\s+(\S+-fragment|"\$\w+"|\'?\$\w+)',
            stripped,
        ):
            lines.append(stripped)
    return lines


def _extract_emit_contract_invocations(text: str) -> list[str]:
    """Return logical lines that contain 'artifact emit-contract'."""
    joined = text.replace("\\\n", " ")
    lines = []
    for line in joined.splitlines():
        stripped = line.strip()
        if "artifact emit-contract" in stripped and "deploy-config-schema" in stripped:
            lines.append(stripped)
    return lines


def _flags_in_invocation(invocation: str) -> set[str]:
    """Extract all --flag tokens from an invocation string."""
    return set(re.findall(r"--[a-z][a-z0-9-]*", invocation))


# ---------------------------------------------------------------------------
# T-CLI1: Recorded-interface tests against the spec fixture
# ---------------------------------------------------------------------------


class CliInterfaceSpecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = _load_spec()
        cls.preview_text = read_script(DEPLOY_PREVIEW_RUN)
        cls.artifact_text = read_script(DEPLOY_ARTIFACT_RUN)

    def test_spec_fixture_exists_and_has_version(self) -> None:
        self.assertIn("version", self.spec)
        self.assertEqual(self.spec["version"], "0.16.0")

    def test_spec_has_render_fragment_subcommand(self) -> None:
        self.assertIn("render_fragment", self.spec["subcommands"])

    def test_spec_has_emit_contract_subcommand(self) -> None:
        self.assertIn("artifact_emit_contract", self.spec["subcommands"])

    # ---- render fragment: required flags must appear in each run.sh ----

    def _required_render_flags(self) -> set[str]:
        return set(self.spec["subcommands"]["render_fragment"]["required_flags"])

    def test_deploy_preview_render_has_required_flags(self) -> None:
        invocations = _extract_render_fragment_invocations(self.preview_text)
        self.assertGreater(
            len(invocations),
            0,
            "deploy-preview/run.sh must contain at least one render <fragment> invocation",
        )
        required = self._required_render_flags()
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            missing = required - flags
            self.assertFalse(
                missing,
                f"deploy-preview render invocation missing required flags {missing}: {inv!r}",
            )

    def test_deploy_artifact_render_has_required_flags(self) -> None:
        invocations = _extract_render_fragment_invocations(self.artifact_text)
        self.assertGreater(
            len(invocations),
            0,
            "deploy-artifact/run.sh must contain at least one render <fragment> invocation",
        )
        required = self._required_render_flags()
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            missing = required - flags
            self.assertFalse(
                missing,
                f"deploy-artifact render invocation missing required flags {missing}: {inv!r}",
            )

    # ---- emit-contract: required flags must appear; absent flags must not ----

    def _required_emit_flags(self) -> set[str]:
        return set(self.spec["subcommands"]["artifact_emit_contract"]["required_flags"])

    def _absent_emit_flags(self) -> set[str]:
        return set(self.spec["subcommands"]["artifact_emit_contract"].get("absent_flags", []))

    def test_deploy_preview_emit_contract_has_required_flags(self) -> None:
        invocations = _extract_emit_contract_invocations(self.preview_text)
        self.assertGreater(
            len(invocations),
            0,
            "deploy-preview/run.sh must contain at least one artifact emit-contract invocation",
        )
        required = self._required_emit_flags()
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            missing = required - flags
            self.assertFalse(
                missing,
                f"deploy-preview emit-contract invocation missing required flags {missing}: {inv!r}",
            )

    def test_deploy_artifact_emit_contract_has_required_flags(self) -> None:
        invocations = _extract_emit_contract_invocations(self.artifact_text)
        self.assertGreater(
            len(invocations),
            0,
            "deploy-artifact/run.sh must contain at least one artifact emit-contract invocation",
        )
        required = self._required_emit_flags()
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            missing = required - flags
            self.assertFalse(
                missing,
                f"deploy-artifact emit-contract invocation missing required flags {missing}: {inv!r}",
            )

    def test_deploy_preview_emit_contract_no_absent_flags(self) -> None:
        """--schema-version must not appear in emit-contract invocations."""
        invocations = _extract_emit_contract_invocations(self.preview_text)
        absent = self._absent_emit_flags()
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            present_absent = absent & flags
            self.assertFalse(
                present_absent,
                f"deploy-preview emit-contract has forbidden flag(s) {present_absent}: {inv!r}",
            )

    def test_deploy_artifact_emit_contract_no_absent_flags(self) -> None:
        """--schema-version must not appear in emit-contract invocations."""
        invocations = _extract_emit_contract_invocations(self.artifact_text)
        absent = self._absent_emit_flags()
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            present_absent = absent & flags
            self.assertFalse(
                present_absent,
                f"deploy-artifact emit-contract has forbidden flag(s) {present_absent}: {inv!r}",
            )


# ---------------------------------------------------------------------------
# T-CLI2: Count-parsing helper — grep -c || true vs || echo 0
# ---------------------------------------------------------------------------


class CountParsingHelperTest(unittest.TestCase):
    """
    Verify that the scripts use `|| true` (not `|| echo 0`) after grep -c so
    that the result is always a single integer (not a two-line value).

    grep -c already prints 0 when no lines match; `|| echo 0` appends a second
    line when grep exits 1, making the result "0\n0" which breaks [[ arithmetic.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.preview_text = read_script(DEPLOY_PREVIEW_RUN)
        cls.artifact_text = read_script(DEPLOY_ARTIFACT_RUN)

    def _find_grep_c_or_echo_zero(self, text: str) -> list[str]:
        """Return lines that have `grep -c ... || echo 0` (the broken pattern)."""
        bad_lines = []
        for line in text.splitlines():
            if re.search(r"grep\s+(-[^ ]*c[^ ]*).*\|\|\s*echo\s+0", line) or re.search(
                r"grep\s+-c\b.*\|\|\s*echo\s+0", line
            ):
                bad_lines.append(line.strip())
        return bad_lines

    def test_deploy_preview_no_grep_c_or_echo_zero(self) -> None:
        bad = self._find_grep_c_or_echo_zero(self.preview_text)
        self.assertFalse(
            bad,
            f"deploy-preview/run.sh has `grep -c ... || echo 0` (broken two-line pattern) on lines: {bad}",
        )

    def test_deploy_artifact_no_grep_c_or_echo_zero(self) -> None:
        bad = self._find_grep_c_or_echo_zero(self.artifact_text)
        self.assertFalse(
            bad,
            f"deploy-artifact/run.sh has `grep -c ... || echo 0` (broken two-line pattern) on lines: {bad}",
        )

    def test_count_lines_helper_via_bash(self) -> None:
        """
        Shell-level check: simulate the fixed pattern in bash and assert the
        result is exactly one line containing a single integer.
        """
        script = r"""
set -euo pipefail
# Simulate: no lines matching — grep -c exits 1; || true prevents abort.
result=$(printf '' | grep -c '^.' || true)
# result must be a single token (no newline inside)
lines=$(printf '%s' "$result" | wc -l | tr -d ' ')
if [[ "$lines" -ne 0 ]]; then
    echo "FAIL: result has ${lines} embedded newlines" >&2
    exit 1
fi
# Must be numeric
if ! [[ "$result" =~ ^[0-9]+$ ]]; then
    echo "FAIL: result is not a plain integer: '${result}'" >&2
    exit 1
fi
echo "ok: result=${result}"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"bash count_lines simulation failed: {result.stderr}",
        )
        self.assertIn("ok:", result.stdout)

    def test_broken_or_echo_zero_produces_two_lines(self) -> None:
        """
        Confirm that `grep -c ... || echo 0` IS the broken pattern by
        demonstrating it produces two lines when grep exits 1.
        The test documents WHY we forbid it.
        """
        script = r"""
# This is the BROKEN pattern: grep -c exits 1 (no match), then echo 0 fires.
result=$(printf '' | grep -c '^.' || echo 0)
line_count=$(printf '%s\n' "$result" | wc -l | tr -d ' ')
# We expect 2 lines ("0" from grep-c then "0" from echo)
printf '%s' "$line_count"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Two-line output means the broken pattern is confirmed
        self.assertEqual(
            result.stdout.strip(),
            "2",
            "Expected two-line output from broken || echo 0 pattern (this test documents the bug)",
        )


# ---------------------------------------------------------------------------
# T-CLI3: deploy-preview positional arg — deploy-dir, not deployment.yml
# ---------------------------------------------------------------------------


class DeployPreviewPositionalArgTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = read_script(DEPLOY_PREVIEW_RUN)

    def test_render_second_positional_is_deploy_dir_not_deployment_yml(self) -> None:
        """
        The CLI requires: render <fragment-id> <deploy-dir>
        The deploy-dir is passed as-is; the CLI appends /deployment.yml internally.
        Callers must NOT pass '${deploy_dir}/deployment.yml' as the second positional.
        """
        invocations = _extract_render_fragment_invocations(self.text)
        self.assertGreater(len(invocations), 0, "No render fragment invocations found")
        for inv in invocations:
            self.assertNotIn(
                "deployment.yml",
                inv,
                f"deploy-preview render invocation passes deployment.yml as positional "
                f"(should pass the deploy-dir only): {inv!r}",
            )

    def test_render_uses_context_and_context_path_flags(self) -> None:
        """
        The preview pulls the context package via oras and passes it to render
        with --context <digest-ref> + --context-path <pulled-file> so the
        recorded ref stays the digest-pinned one. Service repos never hold the
        context file, so --context-dir must not be used (it records a
        local:// ref instead of the digest-pinned one).
        """
        invocations = _extract_render_fragment_invocations(self.text)
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            self.assertIn(
                "--context",
                flags,
                f"deploy-preview render invocation missing --context flag: {inv!r}",
            )
            self.assertIn(
                "--context-path",
                flags,
                f"deploy-preview render invocation missing --context-path flag: {inv!r}",
            )
            self.assertNotIn(
                "--context-dir",
                flags,
                f"deploy-preview render must not use --context-dir (loses the digest ref): {inv!r}",
            )


# ---------------------------------------------------------------------------
# T-CLI4: deploy-artifact positional arg — deploy-dir, not deployment.yml
# ---------------------------------------------------------------------------


class DeployArtifactPositionalArgTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = read_script(DEPLOY_ARTIFACT_RUN)

    def test_render_second_positional_is_deploy_dir_not_deployment_yml(self) -> None:
        invocations = _extract_render_fragment_invocations(self.text)
        self.assertGreater(len(invocations), 0, "No render fragment invocations found")
        for inv in invocations:
            self.assertNotIn(
                "deployment.yml",
                inv,
                f"deploy-artifact render invocation passes deployment.yml as positional "
                f"(should pass the deploy-dir only): {inv!r}",
            )

    def test_render_uses_context_and_context_path_flags(self) -> None:
        """
        In deploy-artifact, the context package is pulled once via oras and
        passed to render with --context <digest-ref> + --context-path <file>.
        """
        invocations = _extract_render_fragment_invocations(self.text)
        for inv in invocations:
            flags = _flags_in_invocation(inv)
            self.assertIn(
                "--context",
                flags,
                f"deploy-artifact render invocation missing --context flag: {inv!r}",
            )
            self.assertIn(
                "--context-path",
                flags,
                f"deploy-artifact render invocation missing --context-path flag: {inv!r}",
            )


# ---------------------------------------------------------------------------
# T-CLI5: Silent || true removal — render/emit-contract failures must be captured
# ---------------------------------------------------------------------------


class SilentFailureRemovalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preview_text = read_script(DEPLOY_PREVIEW_RUN)

    def _render_fragment_lines_with_or_true(self, text: str) -> list[str]:
        """
        Return lines that are PART OF a render-fragment invocation AND also
        have an unconditional `|| true` appended (the silent-failure pattern).
        """
        # Join continuations so each invocation is one logical line
        joined = text.replace("\\\n", " ")
        bad = []
        for line in joined.splitlines():
            stripped = line.strip()
            if re.search(r"deploy-config-schema\s+render\s+\S+-fragment", stripped):
                if re.search(r"\|\|\s*true\s*$", stripped):
                    bad.append(stripped)
        return bad

    def _emit_contract_lines_with_or_true(self, text: str) -> list[str]:
        joined = text.replace("\\\n", " ")
        bad = []
        for line in joined.splitlines():
            stripped = line.strip()
            if "artifact emit-contract" in stripped and "deploy-config-schema" in stripped:
                if re.search(r"\|\|\s*true\s*$", stripped):
                    bad.append(stripped)
        return bad

    def test_deploy_preview_render_not_silently_suppressed(self) -> None:
        """
        render invocations must NOT use unconditional `|| true`.
        The fixed script captures exit status and surfaces it in the scorecard.
        """
        bad = self._render_fragment_lines_with_or_true(self.preview_text)
        self.assertFalse(
            bad,
            f"deploy-preview/run.sh silently suppresses render failures with '|| true': {bad}",
        )

    def test_deploy_preview_emit_contract_not_silently_suppressed(self) -> None:
        """
        artifact emit-contract must NOT use unconditional `|| true`.
        The fixed script captures exit status and exits nonzero when rendering is impossible.
        """
        bad = self._emit_contract_lines_with_or_true(self.preview_text)
        self.assertFalse(
            bad,
            f"deploy-preview/run.sh silently suppresses emit-contract failure with '|| true': {bad}",
        )

    def test_deploy_preview_captures_render_exit_status(self) -> None:
        """
        The script must track render exit status (render_exit or similar variable).
        """
        self.assertIn(
            "render_exit",
            self.preview_text,
            "deploy-preview/run.sh must capture render exit status in a variable",
        )

    def test_deploy_preview_captures_emit_exit_status(self) -> None:
        """
        The script must track emit-contract exit status.
        """
        self.assertIn(
            "emit_exit",
            self.preview_text,
            "deploy-preview/run.sh must capture emit-contract exit status in a variable",
        )

    def test_deploy_preview_exits_nonzero_on_emit_failure(self) -> None:
        """
        When emit-contract fails the action must exit nonzero (E_EMIT_CONTRACT_FAILED).
        """
        self.assertIn(
            "E_EMIT_CONTRACT_FAILED",
            self.preview_text,
            "deploy-preview/run.sh must exit nonzero with E_EMIT_CONTRACT_FAILED when rendering is impossible",
        )


# ---------------------------------------------------------------------------
# T-CLI6: Context package handling — oras pull + pulled-layout discovery
# ---------------------------------------------------------------------------


DEPLOY_PREVIEW_ACTION = ROOT / "actions/deploy-preview/action.yml"


class ContextPackagePullTest(unittest.TestCase):
    """
    Service repos never hold the cluster context; the ACTION fetches it by
    digest. The CLI never pulls (--context records the ref, --context-path
    reads a local file), so each action must oras-pull once and pass the
    discovered file path.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.preview_text = read_script(DEPLOY_PREVIEW_RUN)
        cls.artifact_text = read_script(DEPLOY_ARTIFACT_RUN)
        cls.preview_action = DEPLOY_PREVIEW_ACTION.read_text(encoding="utf-8")

    def test_deploy_preview_pulls_context_via_oras(self) -> None:
        self.assertRegex(
            self.preview_text,
            r"oras pull\s+\"\$context_ref\"",
            "deploy-preview/run.sh must oras pull the context package by digest ref",
        )

    def test_deploy_preview_fails_loud_on_pull_failure(self) -> None:
        self.assertIn(
            "E_CONTEXT_PULL_FAILED",
            self.preview_text,
            "deploy-preview/run.sh must fail loud when the oras pull fails",
        )

    def test_deploy_preview_fails_loud_when_context_file_missing(self) -> None:
        self.assertIn(
            "E_CONTEXT_FILE_MISSING",
            self.preview_text,
            "deploy-preview/run.sh must fail loud when cluster-context-public.yml is absent from the pulled tree",
        )

    def test_deploy_artifact_fails_loud_when_context_file_missing(self) -> None:
        self.assertIn(
            "E_CONTEXT_FILE_MISSING",
            self.artifact_text,
            "deploy-artifact/run.sh must fail loud when cluster-context-public.yml is absent from the pulled tree",
        )

    def test_scripts_do_not_hardcode_pulled_context_root_path(self) -> None:
        """
        The pulled layout carries cluster-context-public.yml under
        context/public/; scripts must discover the file rather than hardcode
        <pull-root>/cluster-context-public.yml.
        """
        for name, text in (
            ("deploy-preview", self.preview_text),
            ("deploy-artifact", self.artifact_text),
        ):
            self.assertNotRegex(
                text,
                r"context-pkg/cluster-context-public\.yml",
                f"{name}/run.sh hardcodes the pulled context file at the pull root; "
                "use find_cluster_context instead",
            )

    def test_scripts_define_discovery_helper(self) -> None:
        for name, text in (
            ("deploy-preview", self.preview_text),
            ("deploy-artifact", self.artifact_text),
        ):
            self.assertIn(
                "find_cluster_context()",
                text,
                f"{name}/run.sh must define the find_cluster_context discovery helper",
            )

    def test_preview_action_installs_oras(self) -> None:
        """
        The preview action must install oras (reusing the pinned deploy-artifact
        installer) before run.sh executes, since ubuntu-latest runners do not
        ship oras.
        """
        self.assertIn(
            "install-tooling.sh",
            self.preview_action,
            "deploy-preview/action.yml must run install-tooling.sh before run.sh",
        )
        self.assertIn(
            "oras",
            self.preview_action,
            "deploy-preview/action.yml tooling step must include oras",
        )


class FindClusterContextHelperTest(unittest.TestCase):
    """
    Behavioral tests for the find_cluster_context helper, executed against the
    real run.sh files (sourced; main is guarded behind BASH_SOURCE check).
    """

    def _run_helper(self, script: Path, layout: list[str]) -> subprocess.CompletedProcess[str]:
        """
        Create a temp pulled-tree containing the given relative file paths and
        invoke find_cluster_context from the sourced script against it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "pulled"
            root.mkdir()
            for rel in layout:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("apiVersion: deployment.jorisjonkers.dev/cluster-context\n", encoding="utf-8")
            bash = (
                f'source "{script}" && find_cluster_context "{root}"'
            )
            return subprocess.run(
                ["bash", "-c", bash],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def test_sourcing_run_sh_does_not_execute_main(self) -> None:
        """The BASH_SOURCE guard must prevent main from running when sourced."""
        for script in (DEPLOY_PREVIEW_RUN, DEPLOY_ARTIFACT_RUN):
            result = subprocess.run(
                ["bash", "-c", f'source "{script}" && echo SOURCED_OK'],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # No CONTEXT_REF etc. in env: if main ran, it would fail on
                # the :? parameter expansions instead of printing SOURCED_OK.
                env={"PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(
                result.returncode,
                0,
                f"sourcing {script} must not execute main: {result.stderr}",
            )
            self.assertIn("SOURCED_OK", result.stdout)

    def test_finds_file_under_context_public(self) -> None:
        """The published layout carries the file under context/public/."""
        for script in (DEPLOY_PREVIEW_RUN, DEPLOY_ARTIFACT_RUN):
            result = self._run_helper(script, ["context/public/cluster-context-public.yml"])
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")
            self.assertTrue(
                result.stdout.endswith("context/public/cluster-context-public.yml"),
                f"{script}: unexpected path: {result.stdout!r}",
            )

    def test_prefers_context_public_over_other_matches(self) -> None:
        """When multiple matches exist, the context/public/ one wins."""
        for script in (DEPLOY_PREVIEW_RUN, DEPLOY_ARTIFACT_RUN):
            result = self._run_helper(
                script,
                [
                    "cluster-context-public.yml",
                    "context/public/cluster-context-public.yml",
                    "other/cluster-context-public.yml",
                ],
            )
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")
            self.assertIn(
                "context/public/cluster-context-public.yml",
                result.stdout,
                f"{script}: must prefer the context/public/ match: {result.stdout!r}",
            )

    def test_falls_back_to_any_match_when_no_context_public(self) -> None:
        """A root-level or differently nested file is still found."""
        for script in (DEPLOY_PREVIEW_RUN, DEPLOY_ARTIFACT_RUN):
            result = self._run_helper(script, ["cluster-context-public.yml"])
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")
            self.assertTrue(
                result.stdout.endswith("cluster-context-public.yml"),
                f"{script}: unexpected path: {result.stdout!r}",
            )

    def test_returns_nonzero_when_absent(self) -> None:
        """No match anywhere in the tree -> nonzero so callers can fail loud."""
        for script in (DEPLOY_PREVIEW_RUN, DEPLOY_ARTIFACT_RUN):
            result = self._run_helper(script, ["context/public/other-file.yml"])
            self.assertNotEqual(
                result.returncode,
                0,
                f"{script}: find_cluster_context must return nonzero when the file is absent",
            )
            self.assertEqual(result.stdout, "", f"{script}: no path must be printed on miss")


if __name__ == "__main__":
    unittest.main()
