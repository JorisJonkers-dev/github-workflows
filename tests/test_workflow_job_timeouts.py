"""
Every reusable-workflow job must bound its own runtime.

GitHub's default job timeout is 360 minutes. With no timeout-minutes set, a
step that hangs rather than fails holds a runner for six hours and the calling
pull request simply never settles. That happened repeatedly to the browser E2E
job in the UI repositories: runs sat in_progress for over four hours against a
normal duration under two minutes, and the only way out was cancelling by hand.

Not one of the 21 reusable workflows in this repository set a timeout before
this test existed, so the gap applied to every consumer of every workflow.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))

# Builds and image publishes legitimately run longer than lint/test/validate work.
LONGER_LIMIT_FILES = {
    "container-publish.yml",
    "crac-train.yml",
    "deploy-bundle.yml",
    "docker-image-ci.yml",
    "jvm-ci.yml",
}
STANDARD_LIMIT = 30
LONGER_LIMIT = 60


def _reusable_jobs(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    trigger = doc.get("on", doc.get(True))
    if not isinstance(trigger, dict) or "workflow_call" not in trigger:
        return {}
    # A job that only delegates via `uses:` cannot carry timeout-minutes.
    return {
        name: job
        for name, job in (doc.get("jobs") or {}).items()
        if isinstance(job, dict) and "uses" not in job
    }


class WorkflowJobTimeoutTest(unittest.TestCase):
    def test_at_least_one_reusable_workflow_is_discovered(self) -> None:
        found = [p.name for p in WORKFLOWS if _reusable_jobs(p)]
        self.assertGreater(len(found), 10, f"discovery looks broken; only found {found}")

    def test_every_reusable_job_sets_a_timeout(self) -> None:
        for path in WORKFLOWS:
            for name, job in _reusable_jobs(path).items():
                with self.subTest(workflow=path.name, job=name):
                    self.assertIn(
                        "timeout-minutes",
                        job,
                        f"{path.name} job '{name}' has no timeout-minutes, so a hang holds a "
                        f"runner for GitHub's 360-minute default",
                    )

    def test_timeouts_are_bounded_and_match_the_declared_tier(self) -> None:
        for path in WORKFLOWS:
            expected = LONGER_LIMIT if path.name in LONGER_LIMIT_FILES else STANDARD_LIMIT
            for name, job in _reusable_jobs(path).items():
                with self.subTest(workflow=path.name, job=name):
                    value = job.get("timeout-minutes")
                    if isinstance(value, str) and value.strip().startswith("${{"):
                        # A caller-configurable timeout is fine: the bound still
                        # exists, it is just chosen by the calling workflow. Assert
                        # the referenced input declares a default, so omitting it
                        # cannot resolve to an empty value.
                        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                        trigger = doc.get("on", doc.get(True))
                        declared = trigger["workflow_call"].get("inputs", {}).get("timeout-minutes", {})
                        self.assertIn(
                            "default",
                            declared,
                            f"{path.name}/{name} takes its timeout from an input with no default",
                        )
                        self.assertLessEqual(int(declared["default"]), expected)
                        continue
                    self.assertIsInstance(value, int, f"{path.name}/{name} timeout must be an integer")
                    self.assertGreater(value, 0)
                    self.assertLessEqual(
                        value,
                        expected,
                        f"{path.name}/{name} allows {value}m; the tier for this workflow is {expected}m",
                    )


if __name__ == "__main__":
    unittest.main()
