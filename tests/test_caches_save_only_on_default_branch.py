"""Only a push to the default branch saves a cache; pull requests restore and never save.

A repository holds 10 GB of Actions cache, and GitHub evicts the least recently used past
that. Every pull request saving its own Gradle, Node, Docker and mise caches filled it within
days and evicted the default branch's, so pull requests that could only restore from the
default branch started cold. On 2026-10-21 the estate held 944 pull-request caches, about 61 GB.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

ON_DEFAULT_BRANCH_PUSH = (
    "github.event_name == 'push' && github.ref_name == github.event.repository.default_branch"
)


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def steps_using(doc, action_prefix):
    if "runs" in doc:
        steps = doc["runs"]["steps"]
    else:
        steps = [s for job in doc["jobs"].values() for s in job.get("steps", [])]
    return [s for s in steps if str(s.get("uses", "")).startswith(action_prefix)]


class GradleCacheIsReadOnlyOffTheDefaultBranch(unittest.TestCase):
    def setUp(self):
        self.action = load("actions/setup-java-gradle/action.yml")

    def test_the_read_only_input_defaults_to_deciding_from_the_event(self):
        self.assertEqual(self.action["inputs"]["gradle-cache-read-only"]["default"], "")

    def test_setup_gradle_is_read_only_unless_a_default_branch_push(self):
        (step,) = steps_using(self.action, "gradle/actions/setup-gradle")
        self.assertIn(ON_DEFAULT_BRANCH_PUSH, step["with"]["cache-read-only"])


class NodeCacheIsSavedOnlyOnADefaultBranchPush(unittest.TestCase):
    def setUp(self):
        self.action = load("actions/setup-node/action.yml")

    def test_setup_node_caches_only_on_a_default_branch_push(self):
        (step,) = steps_using(self.action, "actions/setup-node")
        self.assertIn(ON_DEFAULT_BRANCH_PUSH, step["with"]["cache"])
        self.assertIn(ON_DEFAULT_BRANCH_PUSH, step["with"]["package-manager-cache"])

    def test_other_runs_restore_the_default_branch_cache_without_saving(self):
        (step,) = steps_using(self.action, "actions/cache/restore")
        self.assertIn("!(" + ON_DEFAULT_BRANCH_PUSH + ")", step["if"])
        self.assertEqual(steps_using(self.action, "actions/cache/save"), [])


class DockerCiBuildWritesItsCacheOnlyOnADefaultBranchPush(unittest.TestCase):
    def test_cache_to_is_empty_off_a_default_branch_push(self):
        (step,) = steps_using(load(".github/workflows/docker-image-ci.yml"), "docker/build-push-action")
        self.assertIn(ON_DEFAULT_BRANCH_PUSH, step["with"]["cache-to"])
        self.assertIn("type=gha", step["with"]["cache-from"])


class MutationReportNeverSavesAMiseCache(unittest.TestCase):
    def test_mise_runs_with_cache_save_off(self):
        (step,) = steps_using(load(".github/workflows/mutation-report.yml"), "jdx/mise-action")
        self.assertIs(step["with"]["cache_save"], False)


if __name__ == "__main__":
    unittest.main()
