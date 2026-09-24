"""Unit-tests voor submit_review.is_contentless_review (operator-beleid
2026-09-24: een review zonder bevindingen post niets — lege reviews zijn
spam, guardian PR #27).

Run: python3 -m unittest tests.test_submit_review_clean_skip -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from submit_review import extract_key_issues, is_contentless_review  # noqa: E402

CLEAN_BODY = (
    "## PR Reviewer Guide 🔍\n\n⚡ **No major issues detected**\n\n"
    "<!-- pr-piet-review:v1 head=deadbeef -->"
)
FINDINGS_BODY = "## PR Reviewer Guide 🔍\n\n1. Possible Bug: off-by-one\n"


def review_data_with_issues(n: int) -> dict:
    issues = [
        {"issue_header": f"Possible Bug {i}", "issue_content": "x"} for i in range(n)
    ]
    return {"review": {"key_issues_to_review": issues}}


class TestIsContentlessReview(unittest.TestCase):
    def test_clean_body_no_comments_no_json(self):
        self.assertTrue(is_contentless_review(CLEAN_BODY, [], None))

    def test_clean_body_json_without_issues(self):
        self.assertTrue(is_contentless_review(CLEAN_BODY, [], {"review": {}}))

    def test_case_insensitive_marker(self):
        body = CLEAN_BODY.replace("No major issues", "NO MAJOR ISSUES")
        self.assertTrue(is_contentless_review(body, [], None))

    def test_clean_body_but_json_has_issues_is_not_contentless(self):
        """Tegenstrijdige output: body zegt clean, JSON heeft bevindingen —
        de JSON wint, we posten de findings."""
        rd = review_data_with_issues(1)
        self.assertFalse(is_contentless_review(CLEAN_BODY, [], rd))
        self.assertEqual(len(extract_key_issues(rd)), 1)

    def test_findings_body_is_not_contentless(self):
        self.assertFalse(is_contentless_review(FINDINGS_BODY, [], None))

    def test_suggestions_present_is_not_contentless(self):
        self.assertFalse(is_contentless_review(CLEAN_BODY, [{"body": "x"}], None))

    def test_empty_body_is_not_contentless(self):
        self.assertFalse(is_contentless_review("", [], None))


if __name__ == "__main__":
    unittest.main()
