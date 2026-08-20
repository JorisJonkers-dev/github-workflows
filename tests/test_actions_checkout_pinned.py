"""Reusable workflows must check out this repository at a pinned, maintained ref.

Every reusable workflow here checks out github-workflows to run an action or
script from it. That checkout used `ref: ${{ github.job_workflow_sha }}`, which
is empty inside a reusable workflow -- verified by echoing the context in a real
run:

    job_workflow_sha=[]
    job_workflow_ref=[]
    workflow_ref=[.../_diag-caller.yml@refs/pull/97/merge]   # the caller's

With an empty ref, actions/checkout falls back to the default branch, so every
consumer ran main's action code no matter which tag it pinned. The failure was
invisible: a tag pin looked authoritative while a merge to main reached all
consumers at once.

The ref is now a literal kept in step with the release tag by release-please,
so the workflow file shipped in a tag references that same tag.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CONFIG = ROOT / "release-please-config.json"

SELF_CHECKOUT = re.compile(r"repository:\s*'?JorisJonkers-dev/github-workflows'?")
# `ref: v1.2.3 # x-release-please-version`, quoted or not.
PINNED_REF = re.compile(
    r"^\s*'?ref'?:\s*'?v\d+\.\d+\.\d+'?\s*#.*x-release-please-version", re.MULTILINE
)
ANY_REF = re.compile(r"^\s*'?ref'?:\s*(.+?)\s*$", re.MULTILINE)


def workflows():
    return sorted(p for p in WORKFLOW_DIR.glob("*.yml"))


def self_checkout_workflows():
    return [p for p in workflows() if SELF_CHECKOUT.search(p.read_text(encoding="utf-8"))]


class JobWorkflowShaIsNotUsed(unittest.TestCase):
    def test_no_workflow_references_job_workflow_sha_as_a_ref(self):
        offenders = []
        for p in workflows():
            for line in p.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # the explanatory comments name it deliberately
                if "job_workflow_sha" in stripped:
                    offenders.append(f"{p.name}: {stripped}")
        self.assertEqual(
            offenders,
            [],
            "github.job_workflow_sha is empty inside a reusable workflow; a checkout "
            "using it silently falls back to the default branch:\n  "
            + "\n  ".join(offenders),
        )


class SelfCheckoutIsPinned(unittest.TestCase):
    def test_at_least_one_workflow_checks_this_repository_out(self):
        self.assertGreater(
            len(self_checkout_workflows()), 0, "expected reusable workflows that check out this repo"
        )

    def test_every_self_checkout_uses_a_maintained_literal_ref(self):
        offenders = []
        for p in self_checkout_workflows():
            if not PINNED_REF.search(p.read_text(encoding="utf-8")):
                offenders.append(p.name)
        self.assertEqual(
            offenders,
            [],
            "these workflows check out github-workflows without a literal ref carrying "
            f"the x-release-please-version annotation: {offenders}",
        )

    def test_every_pinned_ref_agrees_within_a_workflow(self):
        for p in self_checkout_workflows():
            versions = {
                m.group(0).split("#")[0].split(":")[1].strip().strip("'")
                for m in PINNED_REF.finditer(p.read_text(encoding="utf-8"))
            }
            self.assertLessEqual(
                len(versions), 1, f"{p.name} pins more than one version: {sorted(versions)}"
            )


class ReleasePleaseMaintainsThePins(unittest.TestCase):
    """A literal that nothing bumps is a stale pin waiting to happen."""

    def setUp(self):
        self.extra = [
            e.get("path")
            for e in json.loads(CONFIG.read_text(encoding="utf-8"))["packages"]["."].get(
                "extra-files", []
            )
        ]

    def test_every_annotated_workflow_is_listed_in_extra_files(self):
        missing = []
        for p in workflows():
            if "x-release-please-version" in p.read_text(encoding="utf-8"):
                rel = str(p.relative_to(ROOT))
                if rel not in self.extra:
                    missing.append(rel)
        self.assertEqual(
            missing,
            [],
            f"annotated but not in release-please extra-files, so never bumped: {missing}",
        )

    def test_extra_files_do_not_list_absent_files(self):
        stale = [f for f in self.extra if not (ROOT / f).exists()]
        self.assertEqual(stale, [], f"extra-files lists files that do not exist: {stale}")

    def test_extra_files_only_lists_annotated_workflows(self):
        unannotated = [
            f
            for f in self.extra
            if f.startswith(".github/workflows/")
            and "x-release-please-version" not in (ROOT / f).read_text(encoding="utf-8")
        ]
        self.assertEqual(
            unannotated, [], f"listed for bumping but carries no annotation: {unannotated}"
        )


if __name__ == "__main__":
    unittest.main()
