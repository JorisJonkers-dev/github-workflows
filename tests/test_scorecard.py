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

# The overall gate filter from run.sh step (7). Kept verbatim here; a drift
# guard below asserts this exact string appears in run.sh so the two cannot
# diverge silently. startswith("fail") (not == "fail") is load-bearing:
# no_raw_secrets returns detail-suffixed values like "fail:raw-secret-in:<path>".
OVERALL_GATE_JQ_FILTER = (
    '[.[] | select(type == "string" and startswith("fail"))]'
    ' | length == 0 | if . then "pass" else "fail" end'
)

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
    overall=fail. The jq filter `[.[] | select(type == "string" and startswith("fail"))] | length == 0`
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
        fail_count = sum(1 for v in scorecard.values() if v.startswith("fail"))
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

    def test_no_raw_secrets_pass_for_secretstore_kinds(self) -> None:
        # 'kind: SecretStore' / 'kind: ClusterSecretStore' are not raw Secrets
        # and must not trip the anchored kind match.
        setup = (
            "mkdir -p out/manifests/preview/production && "
            "printf 'apiVersion: external-secrets.io/v1\\nkind: SecretStore\\n' "
            "> out/manifests/preview/production/store.yaml && "
            "printf 'apiVersion: external-secrets.io/v1\\nkind: ClusterSecretStore\\n' "
            "> out/manifests/preview/production/cluster-store.yaml"
        )
        result = _run_compute_scorecard(
            MINIMAL_DEPLOYMENT_YML, MINIMAL_CONTRACT_YAML, extra_setup=setup
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scorecard = json.loads(result.stdout)
        self.assertEqual(
            scorecard["no_raw_secrets"],
            "pass",
            f"SecretStore kinds must not count as raw Secrets, got: {scorecard['no_raw_secrets']}",
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
            ["jq", "-r", OVERALL_GATE_JQ_FILTER],
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
            ["jq", "-r", OVERALL_GATE_JQ_FILTER],
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
        fail_count = sum(1 for v in scorecard.values() if v.startswith("fail"))
        self.assertEqual(
            fail_count,
            0,
            f"Expected all checks to be pass or not_applicable on minimal fixture: {scorecard}",
        )

    def test_overall_fail_when_no_raw_secrets_has_detail_suffix(self) -> None:
        """
        Regression: a detail-suffixed fail value ("fail:raw-secret-in:<path>")
        must count as a failure in the overall gate. The old exact-match filter
        (. == "fail") missed it and wrongly passed the gate.
        """
        scorecard = {
            "schema_pinned": "pass",
            "context_pinned": "pass",
            "no_latest_images": "pass",
            "health_declared": "pass",
            "route_owner_authmode_declared": "not_applicable",
            "rollback_retention_acknowledged": "pass",
            "no_raw_secrets": "fail:raw-secret-in:out/manifests/x.yaml",
            "stateful_policy_declared": "not_applicable",
            "raw_manifests_guarded": "not_applicable",
            "npm_signatures_verified": "not_applicable",
        }
        result = subprocess.run(
            ["jq", "-r", OVERALL_GATE_JQ_FILTER],
            input=json.dumps(scorecard),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"jq failed: {result.stderr}")
        self.assertEqual(
            result.stdout.strip(),
            "fail",
            "Overall must be 'fail' when no_raw_secrets carries a "
            f"fail:raw-secret-in: detail suffix; got: {result.stdout.strip()!r}",
        )

    def test_overall_gate_filter_matches_run_sh_verbatim(self) -> None:
        """
        Drift guard: the jq filter used by these tests must appear verbatim in
        run.sh, so the tests cannot silently diverge from the real gate.
        """
        text = DEPLOY_PREVIEW_RUN.read_text(encoding="utf-8")
        self.assertIn(
            OVERALL_GATE_JQ_FILTER,
            text,
            "run.sh overall gate filter differs from OVERALL_GATE_JQ_FILTER in tests",
        )


# ---------------------------------------------------------------------------
# T-SC7: detail-suffixed fail values and filter drift protection
# ---------------------------------------------------------------------------

OVERALL_GATE_JQ_FILTER = (
    '[.[] | select(type == "string" and startswith("fail"))] '
    '| length == 0 | if . then "pass" else "fail" end'
)


class DetailSuffixedFailTest(unittest.TestCase):
    """
    no_raw_secrets reports failures as `fail:raw-secret-in:<path>` so the
    message names the offending file. The overall gate filter must count such
    detail-suffixed values as failures (startswith, not exact match).
    """

    def test_detail_suffixed_fail_counts_as_fail(self) -> None:
        scorecard = {
            "schema_pinned": "pass",
            "context_pinned": "pass",
            "no_latest_images": "pass",
            "health_declared": "pass",
            "route_owner_authmode_declared": "not_applicable",
            "rollback_retention_acknowledged": "pass",
            "no_raw_secrets": "fail:raw-secret-in:out/manifests/x.yaml",
            "stateful_policy_declared": "not_applicable",
            "raw_manifests_guarded": "not_applicable",
            "npm_signatures_verified": "not_applicable",
        }
        result = subprocess.run(
            ["jq", "-r", OVERALL_GATE_JQ_FILTER],
            input=json.dumps(scorecard),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"jq failed: {result.stderr}")
        self.assertEqual(
            result.stdout.strip(),
            "fail",
            "A detail-suffixed fail value (fail:raw-secret-in:...) must fail the gate",
        )

    def test_render_failures_fail_action_with_named_fragments(self) -> None:
        """
        Render failures must fail the action via E_RENDER_FAILED, naming the
        failed fragments — never by overriding a scorecard check.
        """
        run_sh_text = DEPLOY_PREVIEW_RUN.read_text(encoding="utf-8")
        self.assertIn(
            "E_RENDER_FAILED",
            run_sh_text,
            "run.sh must fail with E_RENDER_FAILED when any fragment render fails",
        )
        self.assertIn(
            "render-failed:",
            run_sh_text,
            "gate-summary reason must name the failed fragments (render-failed:<list>)",
        )
        self.assertNotIn(
            "'.no_raw_secrets = \"fail\"'",
            run_sh_text,
            "render failures must not be misattributed to no_raw_secrets",
        )

    def test_gate_filter_in_tests_matches_run_sh(self) -> None:
        """
        The jq filter used by these tests must appear verbatim in run.sh so the
        two cannot drift silently.
        """
        run_sh_text = DEPLOY_PREVIEW_RUN.read_text(encoding="utf-8")
        self.assertIn(
            OVERALL_GATE_JQ_FILTER,
            run_sh_text,
            "run.sh overall gate jq filter drifted from the one asserted in tests",
        )


# ---------------------------------------------------------------------------
# T-SC8: executable render-failure path — main() must exit via E_RENDER_FAILED
# ---------------------------------------------------------------------------


class RenderFailureEndToEndTest(unittest.TestCase):
    """
    Run main() with stubbed CLI tooling (oras, npm, deploy-config-schema) so
    every fragment render fails while emit-contract succeeds. The action must
    exit nonzero via E_RENDER_FAILED naming the failed env/fragment pairs, and
    the gate summary must carry a render-failed:<list> reason — proving the
    failure path is reachable and ordered after the sticky-comment/summary
    steps, not just present in the source text.
    """

    STUB_ORAS = """#!/usr/bin/env bash
# Stub oras: create the expected pulled-context layout under --output <dir>.
out=""
prev=""
for a in "$@"; do
  if [[ "$prev" == "--output" ]]; then out="$a"; fi
  prev="$a"
done
mkdir -p "${out}/context/public"
printf 'cluster: stub\n' > "${out}/context/public/cluster-context-public.yml"
exit 0
"""

    STUB_NPM = """#!/usr/bin/env bash
exit 0
"""

    STUB_SCHEMA_CLI = """#!/usr/bin/env bash
# Stub deploy-config-schema: every fragment render fails; emit-contract
# succeeds and writes a minimal contract to --out.
if [[ "$1" == "render" ]]; then
  printf '{"error":"stub render failure"}\n' >&2
  exit 1
fi
if [[ "$1" == "artifact" ]]; then
  out=""
  prev=""
  for a in "$@"; do
    if [[ "$prev" == "--out" ]]; then out="$a"; fi
    prev="$a"
  done
  printf 'spec:\n  contextRef: ghcr.io/stub/context@sha256:abc\n' > "$out"
  exit 0
fi
exit 0
"""

    def test_render_failure_exits_via_e_render_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            stub_bin = tmppath / "stub-bin"
            stub_bin.mkdir()
            for name, content in (
                ("oras", self.STUB_ORAS),
                ("npm", self.STUB_NPM),
                ("deploy-config-schema", self.STUB_SCHEMA_CLI),
            ):
                stub = stub_bin / name
                stub.write_text(content, encoding="utf-8")
                stub.chmod(0o755)

            workdir = tmppath / "work"
            (workdir / "deploy").mkdir(parents=True)
            (workdir / "deploy" / "deployment.yml").write_text(
                MINIMAL_DEPLOYMENT_YML, encoding="utf-8"
            )

            env = {
                "PATH": f"{stub_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "HOME": os.environ.get("HOME", "/root"),
                "RUNNER_TEMP": str(tmppath / "runner-temp"),
                "DEPLOY_DIR": "deploy",
                "SCHEMA_VERSION": "0.0.0",
                "IMAGE_LOCK_PATH": "deploy/images.lock.json",
                "CONTEXT_REF": "ghcr.io/stub/context@sha256:abc",
                "ENVIRONMENTS": "production",
                "COMMENT": "false",
            }
            result = subprocess.run(
                ["bash", str(DEPLOY_PREVIEW_RUN)],
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(workdir),
            )

            self.assertNotEqual(
                result.returncode,
                0,
                f"main() must exit nonzero when fragment renders fail: {result.stdout}",
            )
            self.assertIn(
                "E_RENDER_FAILED",
                result.stderr,
                f"stderr must carry E_RENDER_FAILED: {result.stderr}",
            )
            self.assertIn(
                "production/kubernetes-workload-fragment",
                result.stderr,
                f"E_RENDER_FAILED must name the failed env/fragment pairs: {result.stderr}",
            )

            gate_summary = json.loads(
                (workdir / "gate-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(gate_summary["status"], "fail")
            self.assertTrue(
                gate_summary["reason"].startswith("render-failed:"),
                f"gate reason must be render-failed:<list>, got: {gate_summary['reason']!r}",
            )
            self.assertIn(
                "production/kubernetes-workload-fragment",
                gate_summary["reason"],
                f"gate reason must name the failed fragments: {gate_summary['reason']!r}",
            )


if __name__ == "__main__":
    unittest.main()
