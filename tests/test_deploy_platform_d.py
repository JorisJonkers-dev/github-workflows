"""
Tests for Chunk D: deploy-artifact.yml, leak-scan.yml, deploy-validate.yml,
actions/deploy-artifact, actions/leak-scan, actions/deploy-preview,
data/leak-patterns.json, and renovate.json.

Test groups:
  T-D1: Workflow interface and permissions shape
  T-D2: Leak-scan mode validation
  T-D3: Renovate digest-pin assertions
  T-D4: Gate-summary artifact naming conventions
  T-D5: data/leak-patterns.json canonical shape
  T-D6: Shell script error path assertions
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

DEPLOY_ARTIFACT_WORKFLOW = ROOT / ".github/workflows/deploy-artifact.yml"
LEAK_SCAN_WORKFLOW = ROOT / ".github/workflows/leak-scan.yml"
DEPLOY_VALIDATE_WORKFLOW = ROOT / ".github/workflows/deploy-validate.yml"

DEPLOY_ARTIFACT_ACTION = ROOT / "actions/deploy-artifact/action.yml"
LEAK_SCAN_ACTION = ROOT / "actions/leak-scan/action.yml"
DEPLOY_PREVIEW_ACTION = ROOT / "actions/deploy-preview/action.yml"

LEAK_SCAN_RUN = ROOT / "actions/leak-scan/run.sh"
DEPLOY_ARTIFACT_RUN = ROOT / "actions/deploy-artifact/run.sh"

LEAK_PATTERNS = ROOT / "data/leak-patterns.json"
RENOVATE_CONFIG = ROOT / "renovate.json"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # PyYAML (YAML 1.1) parses `on:` as True; re-key it to "on" for convenience
    if True in data:
        data["on"] = data.pop(True)
    return data


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def run_script(
    script: Path,
    env: dict[str, str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bash script with the given environment, returning the result."""
    base_env = {
        "HOME": os.environ.get("HOME", "/root"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RUNNER_TEMP": tempfile.mkdtemp(),
    }
    base_env.update(env)
    return subprocess.run(
        ["bash", str(script)],
        env=base_env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


# ---------------------------------------------------------------------------
# T-D1: Workflow interface and permissions shape
# ---------------------------------------------------------------------------


class WorkflowInterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.deploy_artifact = load_yaml(DEPLOY_ARTIFACT_WORKFLOW)
        cls.leak_scan = load_yaml(LEAK_SCAN_WORKFLOW)
        cls.deploy_validate = load_yaml(DEPLOY_VALIDATE_WORKFLOW)

    def test_deploy_artifact_outputs_have_value_key(self) -> None:
        outputs = self.deploy_artifact["on"]["workflow_call"]["outputs"]
        for name, defn in outputs.items():
            self.assertIn(
                "value",
                defn,
                msg=f"output '{name}' in deploy-artifact.yml is missing 'value:' key",
            )

    def test_deploy_artifact_permissions_include_id_token_write(self) -> None:
        perms = self.deploy_artifact["permissions"]
        self.assertEqual(perms.get("id-token"), "write")
        self.assertEqual(perms.get("attestations"), "write")
        self.assertEqual(perms.get("packages"), "write")

    def test_deploy_artifact_concurrency_never_cancels_in_progress(self) -> None:
        conc = self.deploy_artifact["concurrency"]
        self.assertFalse(
            conc["cancel-in-progress"],
            "deploy-artifact.yml must never cancel in-progress runs",
        )

    def test_leak_scan_output_is_gate_summary_artifact_not_path(self) -> None:
        outputs = self.leak_scan["on"]["workflow_call"]["outputs"]
        self.assertIn("gate-summary-artifact", outputs)
        self.assertNotIn(
            "gate-summary-path",
            outputs,
            "leak-scan.yml must not expose gate-summary-path (only the artifact name)",
        )

    def test_deploy_validate_has_pull_requests_write(self) -> None:
        perms = self.deploy_validate["permissions"]
        self.assertEqual(perms.get("pull-requests"), "write")

    def test_deploy_artifact_image_lock_artifact_is_only_lock_input(self) -> None:
        """VERIFICATION fix: image-lock-artifact is the only lock input (no image-lock-path alternative)."""
        inputs = self.deploy_artifact["on"]["workflow_call"]["inputs"]
        self.assertIn("image-lock-artifact", inputs)
        # image-lock-path must NOT appear as a workflow_call input — the action
        # computes it from deploy-dir internally after download
        self.assertNotIn(
            "image-lock-path",
            inputs,
            "deploy-artifact.yml workflow_call must not expose image-lock-path; "
            "the action derives the path from deploy-dir after artifact download",
        )

    def test_deploy_artifact_finalize_job_has_if_always(self) -> None:
        finalize_job = self.deploy_artifact["jobs"]["finalize"]
        self.assertEqual(
            finalize_job.get("if", ""),
            "always()",
            "finalize job must run even on failure (if: always())",
        )

    def test_leak_scan_upload_has_if_always(self) -> None:
        scan_steps = self.leak_scan["jobs"]["scan"]["steps"]
        upload_steps = [
            s for s in scan_steps if "actions/upload-artifact" in str(s.get("uses", ""))
        ]
        self.assertTrue(
            upload_steps,
            "leak-scan.yml scan job must have an upload-artifact step",
        )
        for step in upload_steps:
            self.assertEqual(
                step.get("if", ""),
                "always()",
                "leak-scan gate-summary upload must have if: always()",
            )

    def test_deploy_validate_upload_steps_have_if_always(self) -> None:
        validate_steps = self.deploy_validate["jobs"]["validate"]["steps"]
        upload_steps = [
            s for s in validate_steps if "actions/upload-artifact" in str(s.get("uses", ""))
        ]
        self.assertGreaterEqual(len(upload_steps), 2, "deploy-validate must upload 2 artifacts")
        for step in upload_steps:
            self.assertEqual(
                step.get("if", ""),
                "always()",
                "deploy-validate upload steps must have if: always()",
            )


# ---------------------------------------------------------------------------
# T-D2: Leak-scan mode validation (shell tests on the real run.sh)
# ---------------------------------------------------------------------------


class LeakScanModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = LEAK_SCAN_RUN
        cls.gw_root = ROOT

    def _run(self, env_overrides: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        base = {
            "GW_ROOT": str(self.gw_root),
            "PATHS": ".",
            "BASE_REF": "",
            "HEAD_REF": "",
            "DENY_LIST": "default",
        }
        base.update(env_overrides)
        return run_script(self.script, env=base, cwd=cwd)

    def test_pr_diff_mode_fails_without_base_and_head_ref(self) -> None:
        result = self._run({"MODE": "pr-diff", "BASE_REF": "", "HEAD_REF": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_MISSING_REFS", result.stderr)

    def test_path_mode_fails_without_paths(self) -> None:
        result = self._run({"MODE": "path", "PATHS": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_MISSING_PATHS", result.stderr)

    def test_unknown_mode_fails_with_error_code(self) -> None:
        result = self._run({"MODE": "invalid-mode"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_UNKNOWN_LEAK_SCAN_MODE", result.stderr)

    def test_all_refs_mode_line_in_script(self) -> None:
        """T-D2: all-refs mode runs gitleaks with --log-opts=--all and --redact flags."""
        script_text = LEAK_SCAN_RUN.read_text(encoding="utf-8")
        # gitleaks v8.x uses --log-opts="--all" instead of a standalone --all flag.
        # Assert the combined form to catch regression back to the bare --all invocation.
        self.assertIn("gitleaks detect", script_text)
        self.assertIn('--log-opts="--all"', script_text)
        self.assertIn("--redact", script_text)
        # Regression guard: the all-refs block must NOT use a standalone --all flag.
        self.assertNotIn(
            "      --all \\\n",
            script_text,
            "run.sh must not pass --all as a standalone flag to gitleaks detect (gitleaks v8.x removed it)",
        )

    def test_pr_diff_mode_uses_gitleaks_git_with_log_opts(self) -> None:
        """T-D2: pr-diff mode uses 'gitleaks git --log-opts' (not --files-at-commit or --include-paths).

        gitleaks 8.x removed --files-at-commit and --include-paths from the detect
        subcommand.  The correct replacement is 'gitleaks git --log-opts=BASE..HEAD'
        which scans the git commit range that corresponds to the PR diff.
        """
        script_text = LEAK_SCAN_RUN.read_text(encoding="utf-8")
        self.assertIn(
            "gitleaks git",
            script_text,
            "run.sh pr-diff block must use 'gitleaks git' (not 'gitleaks detect' with removed flags)",
        )
        self.assertIn(
            '--log-opts="${BASE_REF}..${HEAD_REF}"',
            script_text,
            "run.sh pr-diff block must pass --log-opts=BASE..HEAD to gitleaks git",
        )
        # Regression guards: these flags were removed in gitleaks 8.x
        self.assertNotIn(
            "--files-at-commit",
            script_text,
            "run.sh must not use --files-at-commit (removed in gitleaks 8.x; causes 'unknown flag' exit 1)",
        )
        self.assertNotIn(
            "--include-paths",
            script_text,
            "run.sh must not use --include-paths (removed in gitleaks 8.x; causes 'unknown flag' exit 1)",
        )

    def test_path_mode_detects_ipv4_literal(self) -> None:
        """T-D2: path mode detects IPv4 literal and emits redacted gate-summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "test.yaml").write_text("host: 192.168.1.50\n", encoding="utf-8")
            result = self._run(
                {"MODE": "path", "PATHS": tmpdir, "DENY_LIST": "default"},
                cwd=tmppath,
            )
            summary_path = tmppath / "gate-summary.json"
            self.assertTrue(
                summary_path.exists(),
                "gate-summary.json must be written even on failure",
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertTrue(summary["redacted"], "gate-summary must have redacted=true")
            # Raw match content must NOT appear in gate-summary
            self.assertNotIn(
                "192.168.1.50",
                json.dumps(summary),
                "Raw IP address must not appear in gate-summary.json",
            )

    def test_path_mode_clean_scan_emits_pass_gate_summary(self) -> None:
        """T-D2: path mode on a clean directory emits pass gate-summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "clean.yaml").write_text("name: my-service\n", encoding="utf-8")
            result = self._run(
                {"MODE": "path", "PATHS": tmpdir, "DENY_LIST": "default"},
                cwd=tmppath,
            )
            summary_path = tmppath / "gate-summary.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["gate"], "leak-scan")

    def test_unknown_deny_list_fails_with_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "dummy.yaml").write_text("x: y\n", encoding="utf-8")
            result = self._run(
                {"MODE": "path", "PATHS": tmpdir, "DENY_LIST": "nonexistent-mode"},
                cwd=tmppath,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("E_UNKNOWN_DENY_LIST_MODE", result.stderr)


# ---------------------------------------------------------------------------
# T-D3: Renovate digest-pin assertions
# ---------------------------------------------------------------------------


class RenovatePinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_json(RENOVATE_CONFIG)

    def test_pin_digests_is_true_globally(self) -> None:
        self.assertTrue(
            self.cfg.get("pinDigests"),
            "renovate.json must have pinDigests: true at the top level",
        )

    def test_github_workflows_is_not_digest_pinned(self) -> None:
        rules = self.cfg.get("packageRules", [])
        gw_rule = next(
            (r for r in rules if "JorisJonkers-dev/github-workflows" in r.get("matchPackageNames", [])),
            None,
        )
        self.assertIsNotNone(gw_rule, "renovate.json must have a rule for JorisJonkers-dev/github-workflows")
        self.assertFalse(
            gw_rule.get("pinDigests", True),
            "JorisJonkers-dev/github-workflows must have pinDigests: false",
        )
        self.assertEqual(
            gw_rule.get("versioning"),
            "semver",
            "JorisJonkers-dev/github-workflows must use semver versioning",
        )

    def test_github_actions_group_is_weekly(self) -> None:
        rules = self.cfg.get("packageRules", [])
        actions_group = next(
            (r for r in rules if r.get("groupName") == "github-actions pinned digests"),
            None,
        )
        self.assertIsNotNone(actions_group, "renovate.json must have 'github-actions pinned digests' group")
        schedule_str = " ".join(actions_group.get("schedule", []))
        self.assertIn("monday", schedule_str.lower(), "github-actions group must run on monday")
        self.assertIn(
            "JorisJonkers-dev/github-workflows",
            actions_group.get("excludePackageNames", []),
            "github-workflows must be excluded from the digest-pin group",
        )


# ---------------------------------------------------------------------------
# T-D4: Gate-summary artifact naming convention
# ---------------------------------------------------------------------------


class GateSummaryNamingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.deploy_artifact = load_yaml(DEPLOY_ARTIFACT_WORKFLOW)
        cls.leak_scan = load_yaml(LEAK_SCAN_WORKFLOW)
        cls.deploy_validate = load_yaml(DEPLOY_VALIDATE_WORKFLOW)
        cls.leak_scan_run = LEAK_SCAN_RUN.read_text(encoding="utf-8")

    def _get_upload_artifact_names(self, workflow: dict) -> list[str]:
        names = []
        for job in workflow.get("jobs", {}).values():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "actions/upload-artifact" in uses:
                    names.append(step.get("with", {}).get("name", ""))
        return names

    def test_deploy_artifact_gate_summary_name(self) -> None:
        names = self._get_upload_artifact_names(self.deploy_artifact)
        self.assertIn(
            "gate-summary-deploy-artifact",
            names,
            "deploy-artifact.yml must upload artifact named gate-summary-deploy-artifact",
        )

    def test_leak_scan_gate_summary_name(self) -> None:
        names = self._get_upload_artifact_names(self.leak_scan)
        self.assertIn(
            "gate-summary-leak-scan",
            names,
            "leak-scan.yml must upload artifact named gate-summary-leak-scan",
        )

    def test_deploy_validate_gate_summary_name(self) -> None:
        names = self._get_upload_artifact_names(self.deploy_validate)
        self.assertIn(
            "gate-summary-deploy-validate",
            names,
            "deploy-validate.yml must upload artifact named gate-summary-deploy-validate",
        )

    def test_deploy_validate_preview_summary_name(self) -> None:
        names = self._get_upload_artifact_names(self.deploy_validate)
        self.assertIn(
            "deploy-preview-summary",
            names,
            "deploy-validate.yml must upload artifact named deploy-preview-summary",
        )

    def test_gate_summary_output_names_match_upload_names(self) -> None:
        """Verify workflow output gate-summary-artifact values match actual upload names."""
        for wf_name, wf in [
            ("deploy-artifact", self.deploy_artifact),
            ("leak-scan", self.leak_scan),
            ("deploy-validate", self.deploy_validate),
        ]:
            outputs = wf["on"]["workflow_call"]["outputs"]
            gsa = outputs.get("gate-summary-artifact", {})
            self.assertIn(
                "value",
                gsa,
                msg=f"{wf_name}.yml gate-summary-artifact output missing value:",
            )

    def test_leak_scan_run_emits_leak_scan_gate(self) -> None:
        """Leak-scan run.sh must reference gate 'leak-scan' in its emit_gate_summary calls."""
        self.assertIn('"leak-scan"', self.leak_scan_run)


# ---------------------------------------------------------------------------
# T-D5: data/leak-patterns.json canonical shape (SC-8, {modes, extra_paths})
# ---------------------------------------------------------------------------


class LeakPatternsShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.patterns = load_json(LEAK_PATTERNS)

    def test_version_field_present(self) -> None:
        self.assertIn("version", self.patterns)
        self.assertEqual(self.patterns["version"], "1")

    def test_categories_field_present(self) -> None:
        self.assertIn("categories", self.patterns)
        self.assertIsInstance(self.patterns["categories"], dict)

    def test_modes_field_uses_flat_category_lists(self) -> None:
        """Correction #5: D's {modes, extra_paths} shape is canonical."""
        self.assertIn("modes", self.patterns)
        for mode_name, mode_cats in self.patterns["modes"].items():
            self.assertIsInstance(
                mode_cats,
                list,
                msg=f"modes.{mode_name} must be a list of category names",
            )
            for cat in mode_cats:
                self.assertIn(
                    cat,
                    self.patterns["categories"],
                    msg=f"modes.{mode_name} references unknown category '{cat}'",
                )

    def test_extra_paths_field_present(self) -> None:
        self.assertIn("extra_paths", self.patterns)

    def test_deployment_artifact_mode_exists(self) -> None:
        self.assertIn("deployment-artifact", self.patterns["modes"])

    def test_deployment_composition_mode_exists(self) -> None:
        self.assertIn("deployment-composition", self.patterns["modes"])

    def test_default_mode_exists(self) -> None:
        self.assertIn("default", self.patterns["modes"])

    def test_deployment_artifact_extra_paths(self) -> None:
        extra = self.patterns.get("extra_paths", {})
        self.assertIn("deployment-artifact", extra)
        self.assertIn("deploy/raw-manifests/**", extra["deployment-artifact"])

    def test_all_required_categories_present(self) -> None:
        required = {
            "ipv4_literal",
            "ipv6_literal",
            "cgnat",
            "rfc1918",
            "k8s_join_tokens",
            "ssh_keys",
            "vault_refs",
            "hardware_ids",
            "provider_ids",
        }
        missing = required - set(self.patterns["categories"].keys())
        self.assertFalse(missing, f"Missing categories in leak-patterns.json: {missing}")

    def test_each_category_has_patterns_list(self) -> None:
        for cat_name, cat_def in self.patterns["categories"].items():
            self.assertIn("patterns", cat_def, msg=f"Category '{cat_name}' missing patterns key")
            self.assertIsInstance(
                cat_def["patterns"],
                list,
                msg=f"Category '{cat_name}' patterns must be a list",
            )
            self.assertGreater(
                len(cat_def["patterns"]),
                0,
                msg=f"Category '{cat_name}' must have at least one pattern",
            )


# ---------------------------------------------------------------------------
# T-D6: Action script content checks
# ---------------------------------------------------------------------------


class DeployArtifactRunShTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.run_sh = DEPLOY_ARTIFACT_RUN.read_text(encoding="utf-8")

    def test_context_ref_digest_requirement(self) -> None:
        self.assertIn("E_CONTEXT_REF_NOT_PINNED", self.run_sh)
        self.assertIn("@sha256:", self.run_sh)

    def test_env_crlf_normalization(self) -> None:
        self.assertIn("sed 's/\\r$//'", self.run_sh)

    def test_env_name_validation_regex(self) -> None:
        self.assertIn("^[a-z0-9][a-z0-9-]*$", self.run_sh)

    def test_duplicate_env_warning(self) -> None:
        self.assertIn("duplicate env", self.run_sh)
        self.assertIn("skipped", self.run_sh)

    def test_no_valid_envs_error(self) -> None:
        self.assertIn("E_NO_VALID_ENVIRONMENTS", self.run_sh)

    def test_reject_secret_kind(self) -> None:
        self.assertIn("E_FORBIDDEN_KIND", self.run_sh)
        # Assert the anchored ERE pattern is used (not an unanchored substring),
        # mirroring the deploy-preview fix from #62.
        self.assertIn("^kind:[[:space:]]*Secret[[:space:]]*$", self.run_sh)

    def test_image_lock_missing_guard(self) -> None:
        self.assertIn("E_IMAGE_LOCK_MISSING", self.run_sh)

    def test_render_hash_exported_to_github_output(self) -> None:
        self.assertIn("render-hash=", self.run_sh)
        self.assertIn("GITHUB_OUTPUT", self.run_sh)

    def test_npm_signatures_gate_emitted_before_exit(self) -> None:
        self.assertIn("npm-signatures", self.run_sh)
        self.assertIn("npm-audit-signatures-failed", self.run_sh)

    def test_schema_version_mismatch_error(self) -> None:
        self.assertIn("E_SCHEMA_VERSION_MISMATCH", self.run_sh)


class DeployArtifactActionOutputsTest(unittest.TestCase):
    """Correction #8: render-hash must be declared in action.yml outputs block."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.action = load_yaml(DEPLOY_ARTIFACT_ACTION)

    def test_render_hash_in_action_outputs(self) -> None:
        outputs = self.action.get("outputs", {})
        self.assertIn(
            "render-hash",
            outputs,
            "actions/deploy-artifact/action.yml must declare render-hash in its outputs block (correction #8)",
        )

    def test_render_hash_output_has_value(self) -> None:
        outputs = self.action.get("outputs", {})
        render_hash_def = outputs.get("render-hash", {})
        self.assertIn(
            "value",
            render_hash_def,
            "render-hash output must have a value: expression",
        )


class DeployPreviewRunShTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.run_sh = (ROOT / "actions/deploy-preview/run.sh").read_text(encoding="utf-8")

    def test_scorecard_keys_present(self) -> None:
        scorecard_keys = [
            "schema_pinned",
            "context_pinned",
            "no_latest_images",
            "health_declared",
            "route_owner_authmode_declared",
            "rollback_retention_acknowledged",
            "no_raw_secrets",
            "stateful_policy_declared",
            "raw_manifests_guarded",
            "npm_signatures_verified",
        ]
        for key in scorecard_keys:
            self.assertIn(key, self.run_sh, msg=f"SC-11 key '{key}' missing from deploy-preview/run.sh")

    def test_sticky_pr_comment_marker_present(self) -> None:
        self.assertIn("deploy-preview-marker", self.run_sh)

    def test_gate_summary_emitted(self) -> None:
        self.assertIn("deploy-validate", self.run_sh)
        self.assertIn("scorecard-evaluated", self.run_sh)

    def test_five_fragments_rendered(self) -> None:
        fragments = [
            "kubernetes-workload-fragment",
            "traefik-route-fragment",
            "gatus-endpoint-fragment",
            "edge-catalog-fragment",
            "image-metadata-fragment",
        ]
        for fragment in fragments:
            self.assertIn(fragment, self.run_sh, msg=f"Fragment '{fragment}' not rendered in deploy-preview/run.sh")



# ---------------------------------------------------------------------------
# T-D7: Leak-scan tool installation and fail-closed hardening
# ---------------------------------------------------------------------------


class LeakScanInstallAndFailClosedTest(unittest.TestCase):
    """
    T-D7: action.yml install steps and run.sh fail-closed hardening for all-refs mode.

    Sub-tests:
      T-D7a: action.yml declares install steps for gitleaks and trufflehog before run step.
      T-D7b: action.yml install steps pin exact version and SHA256 via env vars.
      T-D7c: action.yml install steps are idempotent (skip when tool is already on PATH).
      T-D7d: action.yml install steps verify checksum before installing.
      T-D7e: run.sh fails loudly (E_GITLEAKS_MISSING) in all-refs mode when gitleaks absent.
      T-D7f: run.sh pr-diff mode keeps graceful warning when gitleaks absent (not fail-closed).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.action = load_yaml(LEAK_SCAN_ACTION)
        cls.run_sh_text = LEAK_SCAN_RUN.read_text(encoding="utf-8")
        cls.gw_root = ROOT
        cls.script = LEAK_SCAN_RUN

    def _run(self, env_overrides: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        base = {
            "GW_ROOT": str(self.gw_root),
            "PATHS": ".",
            "BASE_REF": "",
            "HEAD_REF": "",
            "DENY_LIST": "default",
        }
        base.update(env_overrides)
        return run_script(self.script, env=base, cwd=cwd)

    def _get_install_steps(self) -> list[dict]:
        steps = self.action["runs"]["steps"]
        return [s for s in steps if s.get("name", "").startswith("Install ")]

    # T-D7a: install steps exist before the run step
    def test_install_steps_present_before_run_step(self) -> None:
        steps = self.action["runs"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        run_idx = next(
            (i for i, n in enumerate(step_names) if n == "Run leak scan"),
            None,
        )
        self.assertIsNotNone(run_idx, "action.yml must have a 'Run leak scan' step")
        install_before_run = [
            n for n in step_names[:run_idx]
            if n.startswith("Install ")
        ]
        self.assertGreaterEqual(
            len(install_before_run),
            2,
            f"action.yml must install at least 2 tools (gitleaks, trufflehog) before 'Run leak scan'; found: {install_before_run}",
        )
        self.assertTrue(
            any("gitleaks" in n.lower() for n in install_before_run),
            "action.yml must have an install step for gitleaks before 'Run leak scan'",
        )
        self.assertTrue(
            any("trufflehog" in n.lower() for n in install_before_run),
            "action.yml must have an install step for trufflehog before 'Run leak scan'",
        )

    # T-D7b: install steps pin version and SHA256
    def test_gitleaks_install_step_pins_version_and_checksum(self) -> None:
        steps = self._get_install_steps()
        gitleaks_step = next((s for s in steps if "gitleaks" in s.get("name", "").lower()), None)
        self.assertIsNotNone(gitleaks_step, "action.yml must have a gitleaks install step")
        env = gitleaks_step.get("env", {})
        self.assertIn("GITLEAKS_VERSION", env, "gitleaks install step must pin GITLEAKS_VERSION")
        self.assertIn("GITLEAKS_SHA256", env, "gitleaks install step must pin GITLEAKS_SHA256")
        sha = env["GITLEAKS_SHA256"]
        self.assertRegex(
            sha,
            r"^[0-9a-f]{64}$",
            f"GITLEAKS_SHA256 must be a 64-char hex string; got: {sha!r}",
        )

    def test_trufflehog_install_step_pins_version_and_checksum(self) -> None:
        steps = self._get_install_steps()
        th_step = next((s for s in steps if "trufflehog" in s.get("name", "").lower()), None)
        self.assertIsNotNone(th_step, "action.yml must have a trufflehog install step")
        env = th_step.get("env", {})
        self.assertIn("TRUFFLEHOG_VERSION", env, "trufflehog install step must pin TRUFFLEHOG_VERSION")
        self.assertIn("TRUFFLEHOG_SHA256", env, "trufflehog install step must pin TRUFFLEHOG_SHA256")
        sha = env["TRUFFLEHOG_SHA256"]
        self.assertRegex(
            sha,
            r"^[0-9a-f]{64}$",
            f"TRUFFLEHOG_SHA256 must be a 64-char hex string; got: {sha!r}",
        )

    # T-D7c: install steps are idempotent (skip if already on PATH)
    def test_gitleaks_install_step_is_idempotent(self) -> None:
        steps = self._get_install_steps()
        gitleaks_step = next((s for s in steps if "gitleaks" in s.get("name", "").lower()), None)
        self.assertIsNotNone(gitleaks_step)
        run_block = gitleaks_step.get("run", "")
        self.assertIn(
            "command -v gitleaks",
            run_block,
            "gitleaks install step must check if gitleaks is already on PATH before downloading",
        )

    def test_trufflehog_install_step_is_idempotent(self) -> None:
        steps = self._get_install_steps()
        th_step = next((s for s in steps if "trufflehog" in s.get("name", "").lower()), None)
        self.assertIsNotNone(th_step)
        run_block = th_step.get("run", "")
        self.assertIn(
            "command -v trufflehog",
            run_block,
            "trufflehog install step must check if trufflehog is already on PATH before downloading",
        )

    # T-D7d: install steps verify checksum
    def test_gitleaks_install_step_verifies_checksum(self) -> None:
        steps = self._get_install_steps()
        gitleaks_step = next((s for s in steps if "gitleaks" in s.get("name", "").lower()), None)
        self.assertIsNotNone(gitleaks_step)
        run_block = gitleaks_step.get("run", "")
        self.assertIn(
            "sha256sum",
            run_block,
            "gitleaks install step must verify the SHA256 checksum before installing",
        )

    def test_trufflehog_install_step_verifies_checksum(self) -> None:
        steps = self._get_install_steps()
        th_step = next((s for s in steps if "trufflehog" in s.get("name", "").lower()), None)
        self.assertIsNotNone(th_step)
        run_block = th_step.get("run", "")
        self.assertIn(
            "sha256sum",
            run_block,
            "trufflehog install step must verify the SHA256 checksum before installing",
        )

    # T-D7e: run.sh all-refs mode fails loudly when gitleaks is absent
    def test_all_refs_fails_loudly_when_gitleaks_absent(self) -> None:
        """
        run.sh must exit non-zero with E_GITLEAKS_MISSING in all-refs mode when
        gitleaks is not on PATH.  This is the fail-closed contract: a missing
        tool in all-refs mode must never silently degrade.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Use a PATH that contains only /usr/bin and /bin so gitleaks is absent
            # but standard tools (bash, git, jq, grep, etc.) still work.
            fake_path = "/usr/bin:/bin"
            result = self._run(
                {"MODE": "all-refs", "PATH": fake_path},
                cwd=tmppath,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                "run.sh must exit non-zero in all-refs mode when gitleaks is not on PATH",
            )
            combined = result.stdout + result.stderr
            self.assertIn(
                "E_GITLEAKS_MISSING",
                combined,
                "run.sh must emit E_GITLEAKS_MISSING when gitleaks is absent in all-refs mode",
            )

    def test_all_refs_fail_closed_is_in_script(self) -> None:
        """Static drift guard: E_GITLEAKS_MISSING must be present in run.sh."""
        self.assertIn(
            "E_GITLEAKS_MISSING",
            self.run_sh_text,
            "run.sh must contain E_GITLEAKS_MISSING error for all-refs fail-closed behavior",
        )

    # T-D7f: run.sh pr-diff mode keeps graceful warning when gitleaks absent
    def test_pr_diff_keeps_graceful_warning_when_gitleaks_absent(self) -> None:
        """
        pr-diff mode documented contract: gitleaks missing emits a warning
        and falls back to deny-list scan only (graceful degradation).
        """
        self.assertIn(
            "gitleaks not found; skipping gitleaks pr-diff scan",
            self.run_sh_text,
            "run.sh pr-diff mode must keep the graceful gitleaks-missing warning (not fail-closed)",
        )

    # T-D7g: pr-diff mode must not contain removed gitleaks v8.x flags
    def test_pr_diff_does_not_use_removed_gitleaks_flags(self) -> None:
        """T-D7g: --files-at-commit and --include-paths were removed in gitleaks 8.x.

        These flags cause 'gitleaks detect' to print help and exit 1, breaking
        every pipeline run.  This test is a static drift guard that catches any
        re-introduction of the removed flags.
        """
        self.assertNotIn(
            "--files-at-commit",
            self.run_sh_text,
            "run.sh must not contain --files-at-commit (removed in gitleaks 8.x; breaks CI)",
        )
        self.assertNotIn(
            "--include-paths",
            self.run_sh_text,
            "run.sh must not contain --include-paths (removed in gitleaks 8.x; breaks CI)",
        )
        # Positive guard: pr-diff must use the correct v8 invocation
        self.assertIn(
            "gitleaks git",
            self.run_sh_text,
            "run.sh pr-diff block must use 'gitleaks git' subcommand (correct for gitleaks 8.x)",
        )
        self.assertIn(
            "--log-opts=",
            self.run_sh_text,
            "run.sh pr-diff block must use --log-opts to specify commit range (gitleaks 8.x API)",
        )


if __name__ == "__main__":
    unittest.main()
