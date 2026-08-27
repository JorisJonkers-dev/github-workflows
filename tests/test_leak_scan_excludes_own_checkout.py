from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "actions" / "leak-scan" / "run.sh"
PATTERNS = json.loads((ROOT / "data" / "leak-patterns.json").read_text(encoding="utf-8"))

# A *literal* pattern, which is what actually self-matches. The regex patterns
# (e.g. the IPv4 one) do not: JSON-encoding writes them as "\\d", not digits.
# Of the deny-list's patterns, 20 match their own definition file this way.
SELF_MATCHING = "ssh-rsa"


def _grep(pattern: str, path: str, exclude_own: bool) -> list[str]:
    """The same grep the path deny-list scan runs, with and without the fix."""
    cmd = ["grep", "-rn"]
    if exclude_own:
        cmd.append("--exclude-dir=.github-workflows")
    cmd += [
        "--include=*.yaml", "--include=*.yml", "--include=*.json",
        "--include=*.sh", "--include=*.md",
        "-l", "-E", pattern, path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


class LeakScanExcludesOwnCheckout(unittest.TestCase):
    def test_flag_is_present_in_run_sh(self) -> None:
        """The path deny-list grep must exclude the action's own checkout."""
        body = RUN_SH.read_text(encoding="utf-8")
        self.assertIn("--exclude-dir='.github-workflows'", body)

    def test_own_pattern_file_no_longer_matches_itself(self) -> None:
        """
        The consumer clones github-workflows into .github-workflows/, so the
        scanned tree contains the deny-list patterns as literal JSON. Without
        the exclusion the scan fails on its own configuration.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            own = root / ".github-workflows" / "data"
            own.mkdir(parents=True)
            (own / "leak-patterns.json").write_text(
                json.dumps(PATTERNS), encoding="utf-8"
            )

            # Without the fix the action's own pattern file is a match.
            self.assertTrue(
                _grep(SELF_MATCHING, str(root), exclude_own=False),
                "expected the unfixed scan to match its own pattern file",
            )
            # With the fix it is not.
            self.assertEqual(
                _grep(SELF_MATCHING, str(root), exclude_own=True), [],
                "the action's own checkout must not be scanned",
            )

    def test_a_real_leak_still_fails(self) -> None:
        """
        A gate that cannot fail is not a gate. The exclusion must not stop the
        scan catching a leak in the consumer's own files.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            own = root / ".github-workflows" / "data"
            own.mkdir(parents=True)
            (own / "leak-patterns.json").write_text(
                json.dumps(PATTERNS), encoding="utf-8"
            )
            app = root / "cluster"
            app.mkdir()
            (app / "config.yml").write_text(
                "authorized_key: ssh-rsa AAAAB3NzaC1yc2E\n", encoding="utf-8"
            )

            hits = _grep(SELF_MATCHING, str(root), exclude_own=True)
            self.assertEqual(len(hits), 1, f"expected exactly the planted leak, got {hits}")
            self.assertTrue(hits[0].endswith("cluster/config.yml"))


if __name__ == "__main__":
    unittest.main()
