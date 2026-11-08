"""Unit-tests voor de "niets te reviewen"-route (2026-11-08, lastsync #6).

Een PR die alleen gefilterde bestanden raakt (`.gitignore`, lockfiles,
gegenereerde code) houdt na de filters van pr-agent een lege diff over. Dat is
geen model- of reviewfout: de fork meldt het met `no_reviewable_files` en de
review/post moet dat als "nothing to review" tonen in plaats van als falen.

Run: python3 -m unittest tests.test_no_reviewable_files -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from submit_review import (  # noqa: E402
    _no_reviewable_files,
    render_review_body,
    verdict_from_review,
)

HEAD = "abcdef1234567890abcdef1234567890abcdef12"
IDENTITY = "<!-- pr-agent:review:full -->"

EMPTY_DIFF = {
    "review": {
        "key_issues_to_review": [],
        "no_reviewable_files": True,
        "security_concerns": "No reviewable files in this PR.",
    }
}


class NoReviewableDetection(unittest.TestCase):
    def test_flag_detected(self):
        self.assertTrue(_no_reviewable_files(EMPTY_DIFF))

    def test_missing_flag_is_false(self):
        self.assertFalse(_no_reviewable_files({"review": {"key_issues_to_review": []}}))

    def test_garbage_input_is_false(self):
        for value in (None, {}, {"review": None}, {"review": "text"}, []):
            self.assertFalse(_no_reviewable_files(value))

    def test_flag_false_is_false(self):
        data = {"review": {"key_issues_to_review": [], "no_reviewable_files": False}}
        self.assertFalse(_no_reviewable_files(data))


class NoReviewableRendering(unittest.TestCase):
    def test_body_says_nothing_to_review(self):
        body = render_review_body(EMPTY_DIFF, commit_sha=HEAD, identity=IDENTITY)
        self.assertIn("## PR-Piet review — nothing to review", body)
        self.assertIn("filtered out before review", body)
        self.assertIn("This is not a failure", body)

    def test_body_does_not_claim_a_clean_review(self):
        body = render_review_body(EMPTY_DIFF, commit_sha=HEAD, identity=IDENTITY)
        self.assertNotIn("no findings", body.lower())
        self.assertNotIn("No blocking issues were found", body)

    def test_identity_marker_directly_under_the_heading(self):
        body = render_review_body(EMPTY_DIFF, commit_sha=HEAD, identity=IDENTITY)
        lines = body.splitlines()
        self.assertEqual(lines[0], "## PR-Piet review — nothing to review")
        self.assertEqual(lines[1], IDENTITY)

    def test_verdict_is_comment_never_approve(self):
        event, blocking, nonblocking, untagged = verdict_from_review(EMPTY_DIFF)
        self.assertEqual(event, "COMMENT")
        self.assertEqual((blocking, nonblocking, untagged), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
