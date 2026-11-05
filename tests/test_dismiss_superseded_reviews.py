"""Unit-tests voor submit_review.dismiss_superseded_changes_requested.

Achtergrond: GitHub's review-decision is plakkerig. Een latere COMMENT-review
wist een eerdere CHANGES_REQUESTED niet, en PR-Piet post nooit APPROVE (harde
regel 8), dus een blokkade bleef eeuwig staan nadat de bevinding was gefixt.
Deze functie laat de bot zijn eigen vervallen oordeel intrekken — en moet dat
vooral NIET doen waar het bezwaar nog leeft.

Run: python3 -m unittest tests.test_dismiss_superseded_reviews -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import submit_review  # noqa: E402

CURRENT = "aaaa1111"
OLDER = "bbbb2222"
BOT = "github-actions[bot]"


def review(rid: int, state: str, commit: str, login: str = BOT) -> dict:
    return {"id": rid, "state": state, "commit_id": commit, "user": {"login": login}}


class FakeApi:
    """Vervangt submit_review._request en houdt de dismissals bij."""

    def __init__(self, pages: list[list[dict]]):
        self.pages = pages
        self.dismissed: list[int] = []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, token: str, payload: dict | None = None):
        self.calls.append((method, url))
        if method == "GET" and url.endswith("/user"):
            return {"login": BOT}
        if method == "GET":
            page = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
            return self.pages[page - 1] if page <= len(self.pages) else []
        if method == "PUT" and "/dismissals" in url:
            self.dismissed.append(int(url.split("/reviews/")[1].split("/")[0]))
            return {"state": "DISMISSED"}
        raise AssertionError(f"onverwachte call: {method} {url}")


class DismissSupersededTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = submit_review._request

    def tearDown(self) -> None:
        submit_review._request = self._orig

    def run_with(self, pages: list[list[dict]], current: str = CURRENT) -> FakeApi:
        api = FakeApi(pages)
        submit_review._request = api
        result = submit_review.dismiss_superseded_changes_requested(
            "o/r", "1", "token", current
        )
        self.assertEqual(result, api.dismissed)
        return api

    def test_dismisses_own_changes_requested_on_an_older_head(self):
        api = self.run_with([[review(11, "CHANGES_REQUESTED", OLDER)]])
        self.assertEqual(api.dismissed, [11])

    def test_keeps_a_live_objection_on_the_current_head(self):
        api = self.run_with([[review(12, "CHANGES_REQUESTED", CURRENT)]])
        self.assertEqual(api.dismissed, [])

    def test_never_touches_a_human_review(self):
        api = self.run_with([[review(13, "CHANGES_REQUESTED", OLDER, login="m0nk111")]])
        self.assertEqual(api.dismissed, [])

    def test_ignores_commented_and_approved_reviews(self):
        api = self.run_with(
            [
                [
                    review(14, "COMMENTED", OLDER),
                    review(15, "APPROVED", OLDER),
                ]
            ]
        )
        self.assertEqual(api.dismissed, [])

    def test_no_head_means_no_action(self):
        api = self.run_with([[review(16, "CHANGES_REQUESTED", OLDER)]], current="")
        self.assertEqual(api.dismissed, [])

    def test_reads_every_page(self):
        first = [review(100 + i, "COMMENTED", OLDER) for i in range(100)]
        second = [review(17, "CHANGES_REQUESTED", OLDER)]
        api = self.run_with([first, second])
        self.assertEqual(api.dismissed, [17])

    def test_dismissal_message_explains_itself(self):
        api = FakeApi([[review(18, "CHANGES_REQUESTED", OLDER)]])
        submit_review._request = api
        submit_review.dismiss_superseded_changes_requested("o/r", "1", "token", CURRENT)
        self.assertEqual(api.dismissed, [18])


if __name__ == "__main__":
    unittest.main()
