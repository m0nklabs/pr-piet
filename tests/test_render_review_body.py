"""Unit-tests voor submit_review.render_review_body — zakelijke review-body
zonder de "PR Reviewer Guide"-candy (operator-wens 2026-10-03, caretaker
PR #12).

Run: python3 -m unittest tests.test_render_review_body -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from submit_review import render_review_body  # noqa: E402

HEAD = "abcdef1234567890abcdef1234567890abcdef12"

CANDY = [
    "PR Reviewer Guide",
    "🔍",
    "key observations",
    "Estimated effort",
    "PR contains tests",
    "No security concerns",
    "No major issues detected",
    "🔵",
]

BLOCKING = {
    "issue_header": "Off-by-one in chunked() step",
    "issue_content": "range(0, len(items), size + 1) skips the last chunk.",
    "relevant_file": "text_utils.py",
    "start_line": 25,
    "end_line": 25,
}
NONBLOCKING = {
    "issue_header": "Missing size guard",
    "issue_content": "UNCERTAIN: size 0 would loop forever.",
    "relevant_file": "text_utils.py",
    "start_line": 18,
    "end_line": 18,
}


def data(*issues: dict) -> dict:
    return {"review": {"key_issues_to_review": list(issues)}}


class TestRenderReviewBody(unittest.TestCase):
    def test_blocking_findings_heading_and_detail(self):
        body = render_review_body(data(BLOCKING), HEAD)
        self.assertIn("## PR-Piet review — changes requested", body)
        self.assertIn("`abcdef12`", body)
        self.assertIn("1 blocking", body)
        self.assertIn("Off-by-one in chunked() step", body)
        self.assertIn("`text_utils.py:25`", body)
        self.assertIn("skips the last chunk", body)

    def test_clean_body_is_short_and_sober(self):
        body = render_review_body(data(), HEAD)
        self.assertIn("## PR-Piet review — no findings", body)
        self.assertIn("No blocking issues were found", body)

    def test_nonblocking_only_is_labelled(self):
        body = render_review_body(data(NONBLOCKING), HEAD)
        self.assertIn("non-blocking findings", body)
        self.assertIn("(non-blocking)", body)

    def test_no_candy_phrases_or_emoji(self):
        """Kernklacht van de operator: geen 'candy ass'-look meer."""
        for payload in (data(BLOCKING, NONBLOCKING), data(), data(NONBLOCKING)):
            body = render_review_body(payload, HEAD)
            for needle in CANDY:
                self.assertNotIn(needle, body, f"candy gevonden: {needle}")

    def test_findings_are_numbered_in_order(self):
        body = render_review_body(data(BLOCKING, NONBLOCKING), HEAD)
        self.assertLess(body.index("1. **"), body.index("2. **"))

    def test_missing_head_is_handled(self):
        body = render_review_body(data(BLOCKING), "")
        self.assertNotIn("head `", body)


if __name__ == "__main__":
    unittest.main()
