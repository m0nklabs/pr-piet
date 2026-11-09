"""Unit-tests voor het weggooien van spook-bevindingen uit de finding-state.

Een bevinding op een bestand dat de PR niet wijzigt, kan geen geldige bevinding
zijn (de ANCHOR-regel in de prompt). Op lastsync #7 (2026-11-09) produceerde de
reviewer een verzonnen blokkerende bevinding op `AGENTS.md:86` — een bestand dat
de PR nooit aanraakt — en die reisde daarna als "resolved" mee in elke prompt.

Run: python3 -m unittest tests.test_findings_state_filter -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from submit_review import build_findings_state  # noqa: E402

ISSUE = {
    "relevant_file": ".agents/skills/unreal-execution-routing/SKILL.md",
    "start_line": 82,
    "end_line": 87,
    "issue_header": "[verified-bug] Cook validation",
    "issue_content": "Cook validation accepts a nonzero exit status.",
}

PHANTOM = {
    "id": "AGENTS.md::[verified-bug] Outstanding reviews",
    "state": "active",
    "path": "AGENTS.md",
    "start_line": 81,
    "end_line": 86,
    "header": "[verified-bug] Outstanding reviews",
    "content": "Invented merge rule.",
}

PR_PATHS = {".agents/skills/unreal-execution-routing/SKILL.md"}


class PhantomFindingsAreDropped(unittest.TestCase):
    def test_finding_outside_the_pr_is_dropped(self):
        state = build_findings_state([ISSUE], [PHANTOM], PR_PATHS)
        paths = {entry["path"] for entry in state}
        self.assertNotIn("AGENTS.md", paths)
        self.assertIn(".agents/skills/unreal-execution-routing/SKILL.md", paths)

    def test_state_keeps_the_legitimate_finding_active(self):
        state = build_findings_state([ISSUE], [PHANTOM], PR_PATHS)
        active = [entry for entry in state if entry["state"] == "active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["path"], ISSUE["relevant_file"])

    def test_resolved_finding_inside_the_pr_survives(self):
        """Een eerder gemelde bevinding in een bestand dat de PR wél raakt blijft."""
        earlier = dict(PHANTOM, path=ISSUE["relevant_file"],
                       id="SKILL.md::[verified-bug] Earlier")
        state = build_findings_state([ISSUE], [earlier], PR_PATHS)
        # niet opnieuw gemeld? dan resolved; wel gemeld? dan active
        self.assertTrue(all(entry["path"] in PR_PATHS for entry in state))

    def test_fail_open_without_pr_paths(self):
        for paths in (None, set()):
            state = build_findings_state([ISSUE], [PHANTOM], paths)
            self.assertIn("AGENTS.md", {entry["path"] for entry in state})

    def test_empty_previous_state(self):
        self.assertEqual(len(build_findings_state([ISSUE], [], PR_PATHS)), 1)


if __name__ == "__main__":
    unittest.main()
