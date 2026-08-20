"""Every job defined in this repository must declare a timeout.

Without timeout-minutes a job inherits GitHub's six-hour default, so a step
that hangs rather than fails holds a runner and blocks the required-check set
for the rest of the day. The reusable workflows here already carry timeouts;
this repository's own CI and release jobs did not.

Jobs that call a reusable workflow are excluded: the timeout belongs to the
job that runs the steps.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# Every job observed in recent runs completes in well under a minute; the cap
# only has to distinguish "slow" from "wedged".
MAX_REASONABLE_TIMEOUT = 60


def own_jobs():
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, spec in (doc.get("jobs") or {}).items():
            if isinstance(spec, dict) and "steps" in spec:
                yield path.name, job_id, spec


class EveryOwnJobHasATimeout(unittest.TestCase):
    def test_there_are_jobs_to_check(self):
        self.assertGreater(len(list(own_jobs())), 0)

    def test_every_job_declares_a_timeout(self):
        missing = [f"{f}:{j}" for f, j, s in own_jobs() if "timeout-minutes" not in s]
        self.assertEqual(
            missing,
            [],
            "these jobs inherit GitHub's six-hour default, so a hang blocks a runner "
            f"for the rest of the day: {missing}",
        )

    def test_no_timeout_is_absurdly_large(self):
        # A reusable workflow may take its cap from an input; the caller is then
        # responsible for the value, and there is nothing numeric to compare.
        offenders = []
        for f, j, s in own_jobs():
            raw = s.get("timeout-minutes")
            if isinstance(raw, str) and "${{" in raw:
                continue
            if raw is not None and int(raw) > MAX_REASONABLE_TIMEOUT:
                offenders.append(f"{f}:{j}={raw}")
        self.assertEqual(
            offenders,
            [],
            f"a cap above {MAX_REASONABLE_TIMEOUT} minutes stops distinguishing slow "
            f"from wedged: {offenders}",
        )

    def test_no_workflow_declares_a_duplicate_timeout_key(self):
        # A duplicated key parses -- last one wins -- so it silently overrides
        # a deliberate value.
        offenders = []
        for path in sorted(WORKFLOW_DIR.glob("*.yml")):
            prev = ""
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "timeout-minutes" in line and "timeout-minutes" in prev:
                    offenders.append(f"{path.name}:{n}")
                prev = line
        self.assertEqual(offenders, [], f"duplicate timeout-minutes keys: {offenders}")


if __name__ == "__main__":
    unittest.main()
