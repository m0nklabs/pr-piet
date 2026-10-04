"""Tests voor de pr-agent faaldetector (PR #28: stille groene runs).

pr-agent vangt model-fouten zelf af, post "Failed to review PR" en exit 0 —
zonder deze detectie eindigt een model-faal als groene run (harde regel 2).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from submit_review import find_review_failure  # noqa: E402

BOT = "github-actions[bot]"
HUMAN = "m0nk111"


def _comment(login, body, created):
    return {"user": {"login": login}, "body": body, "created_at": created}


class TestFindReviewFailure(unittest.TestCase):
    def test_fresh_bot_failure_comment_is_detected(self):
        comments = [
            _comment(BOT, "Failed to review PR\n", "2026-10-04T14:00:44Z"),
        ]
        self.assertEqual(
            find_review_failure(comments, "2026-10-04T13:50:00Z"),
            "2026-10-04T14:00:44Z",
        )

    def test_human_comment_quoting_the_phrase_is_ignored(self):
        # guardian-agent statuscomments citeren de faaltekst letterlijk
        comments = [
            _comment(
                HUMAN,
                "## Review status — de logs tonen 'Failed to review PR'",
                "2026-10-04T14:02:00Z",
            ),
        ]
        self.assertIsNone(find_review_failure(comments, "2026-10-04T13:50:00Z"))

    def test_stale_bot_failure_comment_is_ignored(self):
        comments = [
            _comment(BOT, "Failed to review PR\n", "2026-10-04T13:30:00Z"),
        ]
        self.assertIsNone(find_review_failure(comments, "2026-10-04T13:50:00Z"))

    def test_newest_of_multiple_failures_wins(self):
        comments = [
            _comment(BOT, "Failed to review PR\n", "2026-10-04T13:47:25Z"),
            _comment(BOT, "Failed to review PR\n", "2026-10-04T14:00:44Z"),
            _comment(HUMAN, "Failed to review PR", "2026-10-04T14:02:00Z"),
        ]
        self.assertEqual(
            find_review_failure(comments, "2026-10-04T13:40:00Z"),
            "2026-10-04T14:00:44Z",
        )

    def test_empty_input_is_none(self):
        self.assertIsNone(find_review_failure([], ""))
        self.assertIsNone(find_review_failure(None, ""))


if __name__ == "__main__":
    unittest.main()
