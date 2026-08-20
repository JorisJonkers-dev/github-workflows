"""No action may invoke a deploy-config-schema subcommand that does not exist.

`artifact` publishes only emit-apply-bundle, emit-contract and
emit-kustomization-health. deploy-artifact called `artifact
validate-raw-manifests`, which exits E_USAGE, and reported the result as a
forbidden-kind violation -- naming a policy breach that had not occurred.

The refusal itself now lives in tools/deploy-check, keyed on a workload
declaring rawManifests rather than on a directory existing, so what remains
here is the invariant that no action invokes an unpublished subcommand.
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


if __name__ == "__main__":
    unittest.main()
