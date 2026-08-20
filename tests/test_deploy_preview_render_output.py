"""Guards on actions/deploy-preview/run.sh.

Both cases here failed in production between 2026-07-08 and 2026-08-20: every
preview render exited 1 with EISDIR because a directory was passed where the
toolkit expects a file, and the raw-secret gate reported "pass" off the empty
output directory that resulted.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "actions" / "deploy-preview" / "run.sh"
ARTIFACT = ROOT / "actions" / "deploy-artifact" / "run.sh"


class RenderOutputIsAFile(unittest.TestCase):
    def test_preview_render_output_names_a_file_not_a_directory(self):
        outputs = re.findall(
            r'--output "(out/manifests/preview/[^"]+)"', PREVIEW.read_text()
        )
        self.assertTrue(outputs, "no preview --output argument found")
        for arg in outputs:
            self.assertTrue(
                arg.endswith(".yaml"),
                f"--output {arg!r} is a directory; the toolkit opens --output as a "
                f"file and exits EISDIR. Append /${{fragment}}.yaml.",
            )

    def test_preview_matches_the_artifact_action_that_works(self):
        pattern = r'--output "out/manifests/(?:preview/)?\$\{env\}/\$\{fragment\}\.yaml"'
        for path in (PREVIEW, ARTIFACT):
            self.assertRegex(
                path.read_text(),
                pattern,
                f"{path.name} does not render one file per fragment",
            )


class RawSecretGateIsNotVacuous(unittest.TestCase):
    """The gate must distinguish 'nothing rendered' from 'nothing found'."""

    def _gate(self, manifests):
        """Run the extracted gate logic against a manifest tree."""
        body = re.search(
            r"(  # no_raw_secrets:.*?\n  fi\n)", PREVIEW.read_text(), re.S
        )
        self.assertIsNotNone(body, "could not extract the no_raw_secrets block")
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in manifests.items():
                p = Path(tmp) / "out" / "manifests" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            script = (
                "set -uo pipefail\ngate() {\n"
                + body.group(1)
                + '\nprintf "%s" "$no_raw_secrets"\n}\ngate\n'
            )
            return subprocess.run(
                ["bash", "-c", script], cwd=tmp, capture_output=True, text=True
            ).stdout.strip()

    def test_nothing_rendered_is_not_applicable_not_pass(self):
        self.assertEqual(self._gate({}), "not_applicable")

    def test_clean_manifests_pass(self):
        self.assertEqual(
            self._gate({"production/app.yaml": "kind: Deployment\n"}), "pass"
        )

    def test_raw_secret_fails(self):
        self.assertTrue(
            self._gate({"production/app.yaml": "kind: Secret\n"}).startswith("fail:")
        )

    def test_secret_as_a_substring_does_not_trip_the_gate(self):
        self.assertEqual(
            self._gate(
                {"production/app.yaml": "kind: VaultStaticSecret\n"}
            ),
            "pass",
        )


if __name__ == "__main__":
    unittest.main()
