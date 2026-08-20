"""The forbidden-kind guard in actions/deploy-artifact/run.sh.

The SC-11 scorecard that the rest of this file used to cover moved to
tools/deploy-check, where it is a pure function with its own unit tests;
the bash implementation it asserted against no longer exists. What remains
here is reject_secret_kind, which is still bash and still in
deploy-artifact.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ARTIFACT_RUN = ROOT / "actions/deploy-artifact/run.sh"


def _run_reject_secret_kind(
    dir_setup: str,
) -> subprocess.CompletedProcess[str]:
    """
    Source deploy-artifact/run.sh, create a temp dir with the given setup,
    and call reject_secret_kind on it.  Returns the CompletedProcess.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        script = f"""
set -euo pipefail
cd {tmpdir!r}
source {str(DEPLOY_ARTIFACT_RUN)!r}
mkdir -p manifests
{dir_setup}
reject_secret_kind manifests
"""
        return subprocess.run(
            ["bash", "-c", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


class RejectSecretKindTest(unittest.TestCase):
    """
    T-DA1: Verify that reject_secret_kind in deploy-artifact/run.sh uses an
    anchored pattern so SecretStore / ClusterSecretStore are not over-matched,
    while a bare 'kind: Secret' still fails with the file named.
    """

    def test_secretstore_passes(self) -> None:
        """kind: SecretStore must not trip the forbidden-kind guard."""
        setup = (
            "printf 'apiVersion: external-secrets.io/v1beta1\nkind: SecretStore\n' "
            "> manifests/store.yaml"
        )
        result = _run_reject_secret_kind(setup)
        self.assertEqual(
            result.returncode,
            0,
            f"SecretStore must not be rejected (E_FORBIDDEN_KIND): {result.stderr}",
        )
        self.assertNotIn(
            "E_FORBIDDEN_KIND",
            result.stderr,
            f"SecretStore must not trigger E_FORBIDDEN_KIND: {result.stderr}",
        )

    def test_clustersecretstore_passes(self) -> None:
        """kind: ClusterSecretStore must not trip the forbidden-kind guard."""
        setup = (
            "printf 'apiVersion: external-secrets.io/v1beta1\nkind: ClusterSecretStore\n' "
            "> manifests/cluster-store.yaml"
        )
        result = _run_reject_secret_kind(setup)
        self.assertEqual(
            result.returncode,
            0,
            f"ClusterSecretStore must not be rejected (E_FORBIDDEN_KIND): {result.stderr}",
        )
        self.assertNotIn(
            "E_FORBIDDEN_KIND",
            result.stderr,
            f"ClusterSecretStore must not trigger E_FORBIDDEN_KIND: {result.stderr}",
        )

    def test_raw_secret_fails_and_names_file(self) -> None:
        """kind: Secret must fail with E_FORBIDDEN_KIND and name the file."""
        setup = (
            "printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: db-creds\n' "
            "> manifests/db-secret.yaml"
        )
        result = _run_reject_secret_kind(setup)
        self.assertNotEqual(
            result.returncode,
            0,
            f"kind: Secret must be rejected (E_FORBIDDEN_KIND): {result.stderr}",
        )
        self.assertIn(
            "E_FORBIDDEN_KIND",
            result.stderr,
            f"Rejection error must mention E_FORBIDDEN_KIND: {result.stderr}",
        )
        self.assertIn(
            "db-secret.yaml",
            result.stderr,
            f"E_FORBIDDEN_KIND must name the offending file: {result.stderr}",
        )

    def test_anchored_pattern_in_run_sh(self) -> None:
        """
        Drift guard: the anchored regex must appear verbatim in
        deploy-artifact/run.sh so this test cannot silently diverge.
        """
        text = DEPLOY_ARTIFACT_RUN.read_text(encoding="utf-8")
        self.assertIn(
            "^kind:[[:space:]]*Secret[[:space:]]*$",
            text,
            "deploy-artifact/run.sh must use anchored pattern ^kind:[[:space:]]*Secret[[:space:]]*$ in reject_secret_kind",
        )


if __name__ == "__main__":
    unittest.main()
