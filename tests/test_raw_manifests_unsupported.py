"""No action may invoke a deploy-config-schema subcommand that does not exist.

`artifact` publishes only emit-apply-bundle, emit-contract and
emit-kustomization-health. deploy-artifact called `artifact
validate-raw-manifests`, which exits E_USAGE, and reported the result as
E_RAW_MANIFESTS_VIOLATIONS -- naming a policy breach that had not occurred.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ROOT / "actions"

# Subcommands the published CLI exposes under `artifact`, per its own usage line.
PUBLISHED_ARTIFACT_SUBCOMMANDS = {
    "emit-apply-bundle",
    "emit-contract",
    "emit-kustomization-health",
}


def shell_sources():
    return sorted(ACTIONS.rglob("*.sh"))


class NoUnpublishedSubcommands(unittest.TestCase):
    def test_no_action_calls_an_unpublished_artifact_subcommand(self):
        offenders = []
        for path in shell_sources():
            for match in re.finditer(
                r"deploy-config-schema\s+artifact\s+([a-z][a-z0-9-]*)", path.read_text()
            ):
                sub = match.group(1)
                if sub not in PUBLISHED_ARTIFACT_SUBCOMMANDS:
                    line = path.read_text()[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(ROOT)}:{line} -> artifact {sub}")
        self.assertEqual(
            offenders,
            [],
            "these calls exit E_USAGE because the subcommand is not published: "
            + "; ".join(offenders),
        )

    def test_raw_manifests_failure_names_the_real_cause(self):
        text = (ACTIONS / "deploy-artifact" / "run.sh").read_text()
        self.assertIn("raw-manifests", text, "the raw-manifests branch disappeared entirely")
        self.assertIn(
            "E_RAW_MANIFESTS_UNSUPPORTED",
            text,
            "a raw-manifests directory must fail as unsupported, not as a violation",
        )
        self.assertNotIn(
            "E_RAW_MANIFESTS_VIOLATIONS",
            text,
            "reporting a violation implies a policy breach that was never evaluated",
        )


if __name__ == "__main__":
    unittest.main()
