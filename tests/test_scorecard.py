"""
Tests for the compute_scorecard helper in actions/deploy-preview/run.sh.

Test groups:
  T-SC1: not_applicable values are treated as pass for the overall gate.
  T-SC2: npm_signatures_verified is always not_applicable in preview mode.
  T-SC3: no_raw_secrets passes when out/manifests/ is missing or empty.
  T-SC4: no_raw_secrets fails with file name when a raw Secret is found.
  T-SC5: no_raw_secrets is NOT overridden by render_failures (override removed).
  T-SC6: overall=pass when all checks are not_applicable or pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PREVIEW_RUN = ROOT / "actions/deploy-preview/run.sh"

# ---------------------------------------------------------------------------
# Minimal YAML fixtures
# ---------------------------------------------------------------------------

# A minimal deployment.yml that satisfies all yq lookups in compute_scorecard:
#   - schemaVersion present -> schema_pinned=pass
#   - no routes -> route_owner_authmode_declared=not_applicable
#   - rollbackTargetRetention.acknowledged=true, minimumDays=90 -> pass
#   - no stateful workloads -> stateful_policy_declared=not_applicable
#   - no rawManifests -> raw_manifests_guarded=not_applicable
#   - health.path present -> health_declared=pass
MINIMAL_DEPLOYMENT_YML = """\
metadata:
  name: test-service
spec:
  schemaVersion: "1.0.0"
  workloads:
    - name: app
      health:
        path: /health
      rollbackTargetRetention:
        acknowledged: true
        minimumDays: 90
"""

# A minimal artifact-contract.yaml. contextRef has @sha256: -> context_pinned=pass.
# No :latest in imageDigests -> no_latest_images=pass.
# provenance_verified is explicitly false (what the action always emits in preview).
MINIMAL_CONTRACT_YAML = """\
spec:
  contextRef: "registry.example.com/context@sha256:abc123"
  imageDigests:
    app: "registry.example.com/app@sha256:def456"
  provenance_verified: false
"""


# ---------------------------------------------------------------------------
# Helper: source run.sh and call compute_scorecard in a temp working directory
# ---------------------------------------------------------------------------

def _run_compute_scorecard(
    deployment_yml_content: str,
    contract_yaml_content: str,
    extra_setup: str = "",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Write YAML fixtures to a temp dir, run compute_scorecard via bash,
    and return the CompletedProcess.

    extra_setup: additional bash lines to run before calling compute_scorecard
    (e.g. mkdir -p out/manifests/ && echo '...' > out/manifests/secret.yaml).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        deploy_yml = os.path.join(tmpdir, "deployment.yml")
        contract_yaml = os.path.join(tmpdir, "contract.yaml")
        Path(deploy_yml).write_text(deployment_yml_content, encoding="utf-8")
        Path(contract_yaml).write_text(contract_yaml_content, encoding="utf-8")

        script = f"""\
set -euo pipefail
cd {tmpdir!r}
source {str(DEPLOY_PREVIEW_RUN)!r}
{extra_setup}
compute_scorecard {deploy_yml!r} {contract_yaml!r}
"""
        env = os.environ.copy()
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", "-c", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )


# ---------------------------------------------------------------------------
# T-SC1: not_applicable values do not cause overall=fail
# ---------------------------------------------------------------------------


class NotApplicableIsPassTest(unittest.TestCase):
    """
    Verify that not_applicable values in route_owner_authmode_declared,
    stateful_policy_declared, and raw_manifests_guarded do not make
    overall=fail. The jq filter `[.[] | select(. == "fail")] | length == 0`
    must return true when all values are pass or not_applicable.
    """

    def test_not_applicable_does_not_count_as_fail(self) -> None:
        result = _run_compute_scorecard(MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML)
        self.assertEqual(
            result.returncode,
            0,
            f"compute_scorecard should exit 0: {result.stderr}",
        )
        scorecard = json.loads(result.stdout)
        # These three must be not_applicable in the minimal fixture
        self.assertEqual(scorecard["route_owner_authmode_declared"], "not_applicable")
        self.assertEqual(scorecard["stateful_policy_declared"], "not_applicable")
        self.assertEqual(scorecard["raw_manifests_guarded"], "not_applicable")

        # Apply the overall gate filter: no "fail" values -> pass
        fail_count = sum(1 for v in scorecard.values() if v == "fail")
        self.assertEqual(
            fail_count,
            0,
            f"Expected no 'fail' values in scorecard: {scorecard}",
        )


# ---------------------------------------------------------------------------
# T-SC2: npm_signatures_verified is always not_applicable in preview mode
# ---------------------------------------------------------------------------


class NpmSignaturesNotApplicableTest(unittest.TestCase):
    """
    In preview mode the action always passes --provenance-verified false,
    so npm_signatures_verified must be not_applicable regardless of contract.
    """

    def test_npm_signatures_verified_is_not_applicable_when_contract_has_false(
        self,
    ) -> None:
        # Contract explicitly has provenance_verified: false
        result = _run_compute_scorecard(MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML)
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["npm_signatures_verified"],
            "not_applicable",
            f"Expected not_applicable, got: {scorecard['npm_signatures_verified']}",
        )

    def test_npm_signatures_verified_is_not_applicable_when_contract_has_true(
        self,
    ) -> None:
        # Even if provenance_verified is true in the contract, still not_applicable
        contract_with_true = MINIMAL_CONTRACT_YAML.replace(
            "provenance_verified: false", "provenance_verified: true"
        )
        result = _run_compute_scorecard(MINIMAL_DEPLOYMENT_YML, contract_with_true)
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["npm_signatures_verified"],
            "not_applicable",
            f"Expected not_applicable regardless of contract, got: {scorecard['npm_signatures_verified']}",
        )

    def test_npm_signatures_verified_is_not_applicable_when_contract_missing(
        self,
    ) -> None:
        # Contract file is empty / missing provenance_verified key
        empty_contract = """\
spec:
  contextRef: "registry.example.com/context@sha256:abc123"
  imageDigests:
    app: "registry.example.com/app@sha256:def456"
"""
        result = _run_compute_scorecard(MINIMAL_DEPLOYMENT_YML, empty_contract)
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["npm_signatures_verified"],
            "not_applicable",
            f"Expected not_applicable when key absent, got: {scorecard['npm_signatures_verified']}",
        )


# ---------------------------------------------------------------------------
# T-SC3: no_raw_secrets passes when out/manifests/ is missing or empty
# ---------------------------------------------------------------------------


class NoRawSecretsPassWhenEmptyTest(unittest.TestCase):
    """
    When out/manifests/ does not exist or is empty, no_raw_secrets must be pass.
    This validates that empty/missing manifests do NOT trigger a fail (Bug 1 fix).
    """

    def test_no_raw_secrets_pass_when_manifests_dir_absent(self) -> None:
        # Default: no out/manifests/ created
        result = _run_compute_scorecard(MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML)
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["no_raw_secrets"],
            "pass",
            f"Expected pass when out/manifests/ is absent, got: {scorecard['no_raw_secrets']}",
        )

    def test_no_raw_secrets_pass_when_manifests_dir_empty(self) -> None:
        # Create empty out/manifests/ directory
        setup = "mkdir -p out/manifests/"
        result = _run_compute_scorecard(
            MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML, extra_setup=setup
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["no_raw_secrets"],
            "pass",
            f"Expected pass when out/manifests/ is empty, got: {scorecard['no_raw_secrets']}",
        )

    def test_no_raw_secrets_pass_when_manifests_have_no_secrets(self) -> None:
        # Manifests exist but contain no Secret resources
        setup = (
            "mkdir -p out/manifests/preview/production && "
            "printf 'apiVersion: apps/v1\\nkind: Deployment\\n' "
            "> out/manifests/preview/production/workload.yaml"
        )
        result = _run_compute_scorecard(
            MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML, extra_setup=setup
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["no_raw_secrets"],
            "pass",
            f"Expected pass when manifests contain no Secret, got: {scorecard['no_raw_secrets']}",
        )


# ---------------------------------------------------------------------------
# T-SC4: no_raw_secrets fails with file name when a raw Secret is found
# ---------------------------------------------------------------------------


class NoRawSecretsFailWithFileNameTest(unittest.TestCase):
    """
    When a manifest contains 'kind: Secret', no_raw_secrets must start with
    'fail:raw-secret-in:' and include a file path (Bug 3 fix).
    """

    def test_no_raw_secrets_fail_contains_file_path(self) -> None:
        setup = (
            "mkdir -p out/manifests/preview/production && "
            "printf 'apiVersion: v1\\nkind: Secret\\nmetadata:\\n  name: mysecret\\n' "
            "> out/manifests/preview/production/secret.yaml"
        )
        result = _run_compute_scorecard(
            MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML, extra_setup=setup
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        value = scorecard["no_raw_secrets"]
        self.assertTrue(
            value.startswith("fail:raw-secret-in:"),
            f"Expected 'fail:raw-secret-in:<path>', got: {value!r}",
        )
        self.assertIn(
            "out/manifests/",
            value,
            f"Expected file path in failure value, got: {value!r}",
        )

    def test_no_raw_secrets_fail_names_the_correct_file(self) -> None:
        setup = (
            "mkdir -p out/manifests/preview/production && "
            "printf 'apiVersion: v1\\nkind: Secret\\nmetadata:\\n  name: creds\\n' "
            "> out/manifests/preview/production/db-secret.yaml"
        )
        result = _run_compute_scorecard(
            MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML, extra_setup=setup
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        value = scorecard["no_raw_secrets"]
        self.assertIn(
            "db-secret.yaml",
            value,
            f"Expected db-secret.yaml in failure value, got: {value!r}",
        )


# ---------------------------------------------------------------------------
# T-SC5: no_raw_secrets NOT overridden when render_failures are present
# ---------------------------------------------------------------------------


class NoRawSecretsNotOverriddenByRenderFailureTest(unittest.TestCase):
    """
    Bug 1 fix: the block that set no_raw_secrets='fail' when render_failures
    is non-empty has been removed. compute_scorecard itself determines the value;
    the main() render failure loop only emits warnings.

    We test this by calling compute_scorecard directly (bypassing main). Since
    compute_scorecard ignores render_failures entirely (they live in main), the
    value must reflect the actual manifest state, not a forced fail.
    """

    def test_no_raw_secrets_is_pass_when_manifests_empty_despite_render_failures(
        self,
    ) -> None:
        # Simulate what happens after render failures: out/manifests/ exists but
        # is empty (renders wrote nothing). The old override would set fail; the
        # fix means compute_scorecard returns pass.
        setup = "mkdir -p out/manifests/"
        result = _run_compute_scorecard(
            MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML, extra_setup=setup
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["no_raw_secrets"],
            "pass",
            f"no_raw_secrets must be pass when manifests empty (render failures do not override it); got: {scorecard['no_raw_secrets']}",
        )

    def test_override_block_is_absent_from_script(self) -> None:
        """
        The jq override '.no_raw_secrets = \"fail\"' inside the render_failures
        block must no longer exist in run.sh (source of truth check).
        """
        text = DEPLOY_PREVIEW_RUN.read_text(encoding="utf-8")
        self.assertNotIn(
            '.no_raw_secrets = "fail"',
            text,
            "The no_raw_secrets='fail' override block must be removed from run.sh",
        )


# ---------------------------------------------------------------------------
# T-SC6: overall=pass when all checks are not_applicable or pass
# ---------------------------------------------------------------------------


class OverallPassWhenAllNotApplicableOrPassTest(unittest.TestCase):
    """
    Construct a scenario where all scorecard checks are pass or not_applicable
    and verify the overall jq gate filter returns 'pass'.
    """

    def test_overall_pass_from_scorecard_json(self) -> None:
        # Build a scorecard JSON where everything is pass or not_applicable
        scorecard = {
            "schema_pinned": "pass",
            "context_pinned": "pass",
            "no_latest_images": "pass",
            "health_declared": "pass",
            "route_owner_authmode_declared": "not_applicable",
            "rollback_retention_acknowledged": "pass",
            "no_raw_secrets": "pass",
            "stateful_policy_declared": "not_applicable",
            "raw_manifests_guarded": "not_applicable",
            "npm_signatures_verified": "not_applicable",
        }
        scorecard_json = json.dumps(scorecard)

        # Apply the exact jq filter from run.sh
        result = subprocess.run(
            ["jq", "-r", '[.[] | select(. == "fail")] | length == 0 | if . then "pass" else "fail" end'],
            input=scorecard_json,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"jq failed: {result.stderr}")
        self.assertEqual(
            result.stdout.strip(),
            "pass",
            f"Overall must be 'pass' when no 'fail' values present; got: {result.stdout.strip()!r}",
        )

    def test_overall_fail_when_one_check_fails(self) -> None:
        # Confirm the inverse: one "fail" -> overall fail
        scorecard = {
            "schema_pinned": "pass",
            "context_pinned": "fail",
            "no_latest_images": "pass",
            "health_declared": "pass",
            "route_owner_authmode_declared": "not_applicable",
            "rollback_retention_acknowledged": "pass",
            "no_raw_secrets": "pass",
            "stateful_policy_declared": "not_applicable",
            "raw_manifests_guarded": "not_applicable",
            "npm_signatures_verified": "not_applicable",
        }
        scorecard_json = json.dumps(scorecard)
        result = subprocess.run(
            ["jq", "-r", '[.[] | select(. == "fail")] | length == 0 | if . then "pass" else "fail" end'],
            input=scorecard_json,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"jq failed: {result.stderr}")
        self.assertEqual(
            result.stdout.strip(),
            "fail",
            f"Overall must be 'fail' when any check is 'fail'; got: {result.stdout.strip()!r}",
        )

    def test_overall_pass_from_minimal_fixture_via_compute_scorecard(self) -> None:
        """End-to-end: compute_scorecard on the minimal fixture must produce overall=pass."""
        result = _run_compute_scorecard(MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML)
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        fail_count = sum(1 for v in scorecard.values() if v == "fail")
        self.assertEqual(
            fail_count,
            0,
            f"Expected all checks to be pass or not_applicable on minimal fixture: {scorecard}",
        )


if __name__ == "__main__":
    unittest.main()
