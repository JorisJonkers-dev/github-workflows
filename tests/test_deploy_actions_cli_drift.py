"""
Regression tests for deploy-config-schema 0.16.0 CLI interface drift.

Test groups:
  T-CLI1: Recorded-interface tests — parse run.sh invocations and compare the
          flag sets used against the checked-in interface spec
          (tests/fixtures/deploy-artifact/cli-interface.json).
  T-CLI2: Count-parsing helper — verify the count_lines pattern (grep -c with
          || true instead of || echo 0) does not produce a two-line value.
  T-CLI3: deploy-preview positional args — second positional must be the
          deploy-dir, not the deployment.yml path.
  T-CLI4: deploy-artifact positional args — same constraint.
  T-CLI5: Silent || true removal — render and emit-contract in deploy-preview
          must not use unconditional `|| true`; failures must be captured.
  T-CLI6: Context package handling — both actions pull the context by digest
          via oras (service repos never hold the context), discover
          cluster-context-public.yml in the pulled tree via the
          find_cluster_context helper, and pass the digest ref to --context
          with the pulled file as --context-path.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEPLOY_PREVIEW_RUN = ROOT / "actions/deploy-preview/run.sh"
DEPLOY_ARTIFACT_RUN = ROOT / "actions/deploy-artifact/run.sh"
DEPLOY_PREVIEW_ACTION = ROOT / "actions/deploy-preview/action.yml"
CLI_INTERFACE_SPEC = (
    ROOT / "tests/fixtures/deploy-artifact/cli-interface.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_script(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_spec() -> dict:
    with CLI_INTERFACE_SPEC.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _extract_render_fragment_invocations(text: str) -> list[str]:
    """
    Return lines (with continuations joined) that contain
    'deploy-config-schema render <fragment-id-or-variable>' patterns.

    The fragment-id may be a literal like 'kubernetes-workload-fragment' or a
    shell variable like '"$fragment"' (loop variable iterating over the list).
    """
    # Join line continuations so each logical invocation is one string
    joined = text.replace("\\\n", " ")
    lines = []
    for line in joined.splitlines():
        stripped = line.strip()
        # Matches both literal fragment ids and loop variables
        if re.search(
            r'deploy-config-schema\s+render\s+(\S+-fragment|"\$\w+"|\'?\$\w+)',
            stripped,
        ):
            lines.append(stripped)
    return lines


def _extract_emit_contract_invocations(text: str) -> list[str]:
    """Return logical lines that contain 'artifact emit-contract'."""
    joined = text.replace("\\\n", " ")
    lines = []
    for line in joined.splitlines():
        stripped = line.strip()
        if "artifact emit-contract" in stripped and "deploy-config-schema" in stripped:
            lines.append(stripped)
    return lines


def _invoked_subcommands(text: str) -> set[str]:
    """
    Return the deploy-config-schema subcommand phrases a script actually runs.

    Comment lines are skipped: run.sh documents a command it deliberately does
    not call, and counting that as an invocation would demand a spec entry for
    something the script warns against.
    """
    joined = text.replace("\\\n", " ")
    phrases: set[str] = set()
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        stripped = re.sub(r"\s+#.*$", "", stripped)
        for match in re.finditer(r"deploy-config-schema\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?", stripped):
            head, nested = match.group(1), match.group(2) or ""
            phrases.add(f"{head} {nested}".strip() if head == "artifact" else head)
    return phrases


def _enclosing_condition(lines: list[str], index: int) -> str | None:
    """
    Walk upward from a line to the `if` that encloses it, stopping at the `fi`
    that would close an earlier block, and return the condition if it tests
    something. `if true; then` tests nothing and does not count as a guard.
    """
    depth = 0
    for line in reversed(lines[:index]):
        stripped = line.strip()
        if stripped == "fi" or stripped.startswith("fi "):
            depth += 1
            continue
        match = re.match(r"if\s+(.*?);\s*then$", stripped)
        if not match:
            continue
        if depth:
            depth -= 1
            continue
        condition = match.group(1).strip()
        if condition in {"true", ":"}:
            return None
        return condition
    return None


def _spec_key(phrase: str) -> str:
    if phrase == "render":
        return "render_fragment"
    return phrase.replace("-", "_").replace(" ", "_")


def _flags_in_invocation(invocation: str) -> set[str]:
    """Extract all --flag tokens from an invocation string."""
    return set(re.findall(r"--[a-z][a-z0-9-]*", invocation))


if __name__ == "__main__":
    unittest.main()
