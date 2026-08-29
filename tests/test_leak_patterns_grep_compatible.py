"""Every deny-list pattern must work under the engine that actually runs it.

run.sh matches with `grep -E` (POSIX ERE). The rest of the suite matches with
Python's `re` (Perl-flavoured). Those engines disagree, and the disagreement is
silent: `\\d`, `\\s`, `\\w` and `(?:...)` are all valid in Python and meaningless
in ERE, so a pattern using them passes every Python test while matching nothing
in production.

That is not hypothetical. Before this module existed, ipv4_literal, both
ipv6_literal patterns, two cgnat patterns and one rfc1918 pattern were dead in
the shell path, and the ssh_keys private-key pattern made grep exit with a usage
error because it begins with '-'. The deny-list reported "no match" for a
private key in a diff.

These tests therefore shell out to grep rather than trusting `re`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = json.loads((ROOT / "data" / "leak-patterns.json").read_text(encoding="utf-8"))

PERL_ONLY = ("\\d", "\\w", "\\s", "\\D", "\\W", "\\S", "(?:", "(?=", "(?!", "(?<")

# One string per category that the category exists to catch. If grep does not
# match these, the guard is not guarding.
MUST_MATCH = {
    "ipv4_literal": ["10.42.0.0/16", "addr 192.0.2.7", "  - 169.254.0.0/16"],
    "ipv6_literal": ["fd7a:115c:a1e0::1", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"],
    "cgnat": ["100.64.1.2", "100.75.0.1", "100.115.3.4", "100.127.0.1"],
    "rfc1918": ["192.168.0.99", "10.0.0.1", "172.16.0.0/12", "172.25.1.1", "172.31.9.9"],
    "ssh_keys": [
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "ssh-ed25519 AAAAC3Nz",
    ],
    "k8s_join_tokens": ["  apiServerEndpoint: 1.2.3.4:6443", "node-token: abc"],
    "vault_refs": ["vaultPolicy: agents", "approle login"],
    "hardware_ids": ["00:1a:2b:3c:4d:5e", "/dev/sda1", "by-id/wwn-0x5000"],
    "provider_ids": ["zone-id: Z123", "tailscale-device: foo"],
}


def all_patterns():
    for category, body in PATTERNS["categories"].items():
        for pattern in body["patterns"]:
            yield category, pattern


def grep_matches(pattern: str, text: str) -> bool:
    """Match exactly as run.sh does: grep -E -e, so a leading '-' is a pattern."""
    result = subprocess.run(
        ["grep", "-c", "-E", "-e", pattern],
        input=f"{text}\n",
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"grep failed for {pattern!r}: rc={result.returncode} {result.stderr.strip()}"
        )
    return result.returncode == 0


@unittest.skipIf(shutil.which("grep") is None, "grep unavailable")
class TestPatternsAreEreCompatible(unittest.TestCase):
    def test_no_perl_only_constructs(self) -> None:
        for category, pattern in all_patterns():
            with self.subTest(category=category, pattern=pattern):
                for token in PERL_ONLY:
                    self.assertNotIn(
                        token,
                        pattern,
                        f"{token} is Perl-only and matches nothing under grep -E",
                    )

    def test_grep_accepts_every_pattern_without_error(self) -> None:
        """A pattern grep rejects is a rule that can never fire."""
        for category, pattern in all_patterns():
            with self.subTest(category=category, pattern=pattern):
                result = subprocess.run(
                    ["grep", "-c", "-E", "-e", pattern],
                    input="harmless\n",
                    capture_output=True,
                    text=True,
                )
                self.assertIn(
                    result.returncode,
                    (0, 1),
                    f"grep rejected {pattern!r}: {result.stderr.strip()}",
                )
                self.assertEqual("", result.stderr.strip())

    def test_each_category_matches_what_it_exists_to_catch(self) -> None:
        for category, samples in MUST_MATCH.items():
            patterns = PATTERNS["categories"][category]["patterns"]
            for sample in samples:
                with self.subTest(category=category, sample=sample):
                    self.assertTrue(
                        any(grep_matches(p, sample) for p in patterns),
                        f"{category} did not match {sample!r} under grep -E",
                    )


if __name__ == "__main__":
    unittest.main()
