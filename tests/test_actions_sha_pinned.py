"""Every external `uses:` in the reusable workflows and composite actions is SHA-pinned.

Repositories that require actions to be pinned by full commit SHA also check the
`uses:` lines of a reusable workflow they call, so one tag-pinned step makes the
caller's whole pipeline fail at startup. Local (`./`) and `docker://` references
are exempt, as is the self-referencing caller template add-to-project-caller.yml.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USES = re.compile(r"""^\s*(?:-\s+)?['"]?uses['"]?\s*:\s*['"]?([^\s'"#]+)""")
SHA = re.compile(r"@[0-9a-f]{40}$")
EXEMPT_FILES = {"add-to-project-caller.yml"}


class ActionsAreShaPinned(unittest.TestCase):
    def test_no_external_uses_is_a_moving_ref(self) -> None:
        files = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        files += sorted((ROOT / "actions").glob("*/action.y*ml"))
        offenders = []
        for path in files:
            if path.name in EXEMPT_FILES:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = USES.match(line)
                if not match:
                    continue
                ref = match.group(1)
                if ref.startswith(("./", "docker://")) or SHA.search(ref):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {ref}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
