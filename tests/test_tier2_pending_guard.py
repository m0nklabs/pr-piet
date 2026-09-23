"""Unit-tests voor tier2_pending_guard.is_pending (pure functie).

Run: python3 -m unittest tests.test_tier2_pending_guard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from tier2_pending_guard import MARKER, is_pending  # noqa: E402

HEAD = "a" * 40
OLD = "b" * 40
MARKER_BODY = f"## PR Reviewer Guide\n<!-- {MARKER} head={HEAD} -->"


def rev(state: str, commit_id: str | None, body: str, at: str) -> dict:
    return {
        "state": state,
        "commit_id": commit_id,
        "body": body,
        "submitted_at": at,
    }


class TestIsPending(unittest.TestCase):
    def test_cr_on_current_head_is_pending(self):
        reviews = [rev("CHANGES_REQUESTED", HEAD, MARKER_BODY, "2026-09-23T06:00:00Z")]
        self.assertTrue(is_pending(reviews, HEAD))

    def test_cr_on_older_head_is_not_pending(self):
        reviews = [rev("CHANGES_REQUESTED", OLD, MARKER_BODY, "2026-09-23T06:00:00Z")]
        self.assertFalse(is_pending(reviews, HEAD))

    def test_intermediate_commented_does_not_clear_cr(self):
        """Operator-semantiek: een tussenliggende clean-review heft een CR
        niet op — alleen een push (nieuwe head) doet dat."""
        reviews = [
            rev("CHANGES_REQUESTED", HEAD, MARKER_BODY, "2026-09-23T06:00:00Z"),
            rev("COMMENTED", HEAD, MARKER_BODY, "2026-09-23T06:10:00Z"),
        ]
        self.assertTrue(is_pending(reviews, HEAD))

    def test_only_foreign_reviews_is_not_pending(self):
        reviews = [
            rev("CHANGES_REQUESTED", HEAD, "human review without marker", "2026-09-23T06:00:00Z"),
            rev("CHANGES_REQUESTED", HEAD, "<!-- other-bot:v9 -->", "2026-09-23T06:05:00Z"),
        ]
        self.assertFalse(is_pending(reviews, HEAD))

    def test_only_commented_our_reviews_is_not_pending(self):
        reviews = [rev("COMMENTED", HEAD, MARKER_BODY, "2026-09-23T06:00:00Z")]
        self.assertFalse(is_pending(reviews, HEAD))

    def test_cr_without_commit_id_is_ignored(self):
        """CR zonder pin (commit_id null) blokkeert niet — niet aantoonbaar
        op deze head."""
        reviews = [
            rev("CHANGES_REQUESTED", None, MARKER_BODY, "2026-09-23T06:00:00Z"),
            rev("COMMENTED", HEAD, MARKER_BODY, "2026-09-23T06:10:00Z"),
        ]
        self.assertFalse(is_pending(reviews, HEAD))

    def test_latest_cr_wins_over_older_cr(self):
        """Nieuwste CR bepaalt: oude CR op huidige head + nieuwe CR op
        oudere head (na push) -> niet pending."""
        reviews = [
            rev("CHANGES_REQUESTED", HEAD, MARKER_BODY, "2026-09-23T06:00:00Z"),
            rev("CHANGES_REQUESTED", OLD, MARKER_BODY, "2026-09-23T07:00:00Z"),
        ]
        self.assertFalse(is_pending(reviews, HEAD))

    def test_order_independence(self):
        """API-volgorde mag niet uitmaken; sorteren gebeurt op submitted_at."""
        base = [
            rev("COMMENTED", HEAD, MARKER_BODY, "2026-09-23T06:00:00Z"),
            rev("CHANGES_REQUESTED", HEAD, MARKER_BODY, "2026-09-23T06:30:00Z"),
            rev("COMMENTED", HEAD, MARKER_BODY, "2026-09-23T06:10:00Z"),
        ]
        self.assertTrue(is_pending(base, HEAD))
        self.assertTrue(is_pending(list(reversed(base)), HEAD))

    def test_marker_is_the_expected_string(self):
        """De marker is een contract met submit_review.py — wijkt die, dan
        moet dát bewust gebeuren (dubbele review-stijlen)."""
        self.assertEqual(MARKER, "pr-piet-review:v1")


if __name__ == "__main__":
    unittest.main()
