from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = json.loads((ROOT / "data" / "leak-patterns.json").read_text(encoding="utf-8"))

# Addresses that must never slip through an ipv6_literal scan.
IPV6_LITERALS = [
    "::1",
    "fe80::1ff:fe23:4567:890a",
    "fd7a:115c:a1e0::1",
    "fd7a:115c:a1e0:ab12:4843:cd96:6259:a1b2",
    "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
    "2606:4700:4700::1111",
    "addr: 2a02:1234:5678:9abc::5",
]

# Colon-separated strings that are not addresses. The deny-list scan reads whole
# files in path mode, so a pattern that matches these makes the gate fire on any
# file carrying a timestamp and blocks unrelated edits.
NOT_IPV6 = [
    "generatedAt: 2026-06-29T00:00:00Z",
    "duration 01:02:03",
    "12:34:56",
    "time=09:46:04.470",
    "00:00:00",
    "ratio 3:2:1",
    "sha256:d5ca100e6e8054a58d8c059d2e5cf1e1aa8c2923",
]

MAC_ADDRESSES = ["00:1a:2b:3c:4d:5e", "aa:bb:cc:dd:ee:ff"]


def matches(category: str, text: str) -> bool:
    return any(
        re.search(pattern, text)
        for pattern in PATTERNS["categories"][category]["patterns"]
    )


class TestIpv6Literal(unittest.TestCase):
    def test_detects_every_ipv6_literal(self) -> None:
        for literal in IPV6_LITERALS:
            with self.subTest(literal=literal):
                self.assertTrue(matches("ipv6_literal", literal))

    def test_does_not_fire_on_timestamps_or_digests(self) -> None:
        for text in NOT_IPV6:
            with self.subTest(text=text):
                self.assertFalse(matches("ipv6_literal", text))

    def test_mac_addresses_stay_covered_by_hardware_ids(self) -> None:
        for mac in MAC_ADDRESSES:
            with self.subTest(mac=mac):
                self.assertTrue(matches("hardware_ids", mac))


class TestPatternsCompile(unittest.TestCase):
    def test_every_pattern_is_a_valid_regex(self) -> None:
        for category, body in PATTERNS["categories"].items():
            for pattern in body["patterns"]:
                with self.subTest(category=category, pattern=pattern):
                    re.compile(pattern)

    def test_every_mode_references_known_categories(self) -> None:
        known = set(PATTERNS["categories"])
        for mode, categories in PATTERNS["modes"].items():
            for category in categories:
                with self.subTest(mode=mode, category=category):
                    self.assertIn(category, known)


if __name__ == "__main__":
    unittest.main()
