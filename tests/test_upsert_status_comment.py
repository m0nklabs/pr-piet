"""Unit-tests voor upsert_status_comment (één persistente eindbeoordeling
per PR — operator-besluit 2026-10-03, caretaker PR #12).

Run: python3 -m unittest tests.test_upsert_status_comment -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from upsert_status_comment import (  # noqa: E402
    STATUS_MARKER,
    find_status_comment,
    render_status,
)

HEAD = "abcdef1234567890abcdef1234567890abcdef12"


class TestRenderStatus(unittest.TestCase):
    def test_clean_contains_marker_and_verdict(self):
        body = render_status("clean", HEAD)
        self.assertIn(STATUS_MARKER, body)
        self.assertIn("geen bevindingen", body)
        self.assertIn("`abcdef12`", body)

    def test_findings_points_at_formal_review(self):
        body = render_status("findings", HEAD)
        self.assertIn(STATUS_MARKER, body)
        self.assertIn("bevindingen gevonden", body)
        self.assertIn("formele review", body)

    def test_detail_is_appended(self):
        body = render_status("clean", HEAD, "12 bestanden")
        self.assertIn("12 bestanden", body)

    def test_body_never_matches_sweep_delete_filter(self):
        """Kritiek: de opschoon-step verwijdert comments die op
        'PR Reviewer Guide' of '[Persistent review]' matchen — de
        statuscomment mag daar nooit onder vallen, anders wist elke run
        zijn eigen eindbeoordeling."""
        for state in ("clean", "findings"):
            body = render_status(state, HEAD)
            self.assertNotIn("PR Reviewer Guide", body)
            self.assertNotIn("[Persistent review]", body)

    def test_missing_head_is_safe(self):
        body = render_status("clean", "")
        self.assertIn("`?`", body)


class TestFindStatusComment(unittest.TestCase):
    def test_none_when_absent(self):
        comments = [{"id": 1, "body": "PR Reviewer Guide", "updated_at": "x"}]
        self.assertIsNone(find_status_comment(comments))

    def test_newest_marker_wins(self):
        comments = [
            {"id": 1, "body": STATUS_MARKER + " oud", "updated_at": "2026-01-01"},
            {"id": 2, "body": STATUS_MARKER + " nieuw", "updated_at": "2026-02-01"},
            {"id": 3, "body": "iets anders", "updated_at": "2026-03-01"},
        ]
        found = find_status_comment(comments)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], 2)


if __name__ == "__main__":
    unittest.main()
