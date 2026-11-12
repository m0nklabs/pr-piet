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
NEWER = "cccc3333"
BOT = "github-actions[bot]"


def review(rid: int, state: str, commit: str, login: str = BOT) -> dict:
    return {"id": rid, "state": state, "commit_id": commit, "user": {"login": login}}


class FakeApi:
    """Vervangt submit_review._request en houdt dismissals + payloads bij."""

    def __init__(self, pages: list[list[dict]], ancestors: set[str] | None = None):
        self.pages = pages
        # welke review-commits zijn voorouder van de huidige head
        self.ancestors = ancestors if ancestors is not None else {OLDER}
        self.dismissed: list[int] = []
        self.payloads: list[dict] = []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, token: str, payload: dict | None = None):
        self.calls.append((method, url))
        if method == "GET" and url.endswith("/user"):
            return {"login": BOT}
        if method == "GET" and "/compare/" in url:
            older = url.split("/compare/")[1].split("...")[0]
            return {"status": "ahead" if older in self.ancestors else "diverged"}
        if method == "GET":
            page = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
            return self.pages[page - 1] if page <= len(self.pages) else []
        if method == "PUT" and "/dismissals" in url:
            self.dismissed.append(int(url.split("/reviews/")[1].split("/")[0]))
            self.payloads.append(payload or {})
            return {"state": "DISMISSED"}
        raise AssertionError(f"onverwachte call: {method} {url}")


class DismissSupersededTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = submit_review._request

    def tearDown(self) -> None:
        submit_review._request = self._orig

    def run_with(
        self, pages: list[list[dict]], current: str = CURRENT, ancestors=None
    ) -> FakeApi:
        api = FakeApi(pages, ancestors=ancestors)
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

    def test_keeps_an_objection_on_a_newer_head(self):
        """Een parallelle run op een nieuwere head is geen vervallen oordeel."""
        api = self.run_with(
            [[review(19, "CHANGES_REQUESTED", NEWER)]], ancestors={OLDER}
        )
        self.assertEqual(api.dismissed, [])

    def test_never_touches_a_human_review(self):
        api = self.run_with([[review(13, "CHANGES_REQUESTED", OLDER, login="m0nk111")]])
        self.assertEqual(api.dismissed, [])

    def test_ignores_commented_and_approved_reviews(self):
        api = self.run_with(
            [[review(14, "COMMENTED", OLDER), review(15, "APPROVED", OLDER)]]
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
        api = self.run_with([[review(18, "CHANGES_REQUESTED", OLDER)]])
        self.assertEqual(len(api.payloads), 1)
        message = api.payloads[0].get("message", "")
        self.assertIn("Superseded", message)
        self.assertIn("later head", message)
        self.assertEqual(api.payloads[0].get("event"), "DISMISS")


class CleanVerdictGateTests(unittest.TestCase):
    """De opruiming mag alleen bij een bewezen schone, volledige review."""

    def test_incremental_review_does_not_clean_up(self):
        # --since gezet => incrementele run => geen opruiming
        self.assertFalse(
            submit_review.cleanup_allowed(
                review_data={"key_issues_to_review": []}, n_block=0, since="2026-11-05T00:00:00Z"
            )
        )

    def test_missing_review_json_does_not_clean_up(self):
        self.assertFalse(
            submit_review.cleanup_allowed(review_data=None, n_block=0, since=None)
        )

    def test_blocking_finding_does_not_clean_up(self):
        self.assertFalse(
            submit_review.cleanup_allowed(
                review_data={"key_issues_to_review": [{"x": 1}]}, n_block=1, since=None
            )
        )

    def test_full_clean_review_cleans_up(self):
        self.assertTrue(
            submit_review.cleanup_allowed(
                review_data={"key_issues_to_review": []}, n_block=0, since=None
            )
        )


class MaybeCleanupTests(unittest.TestCase):
    """Eén ingang voor beide succes-paden (normale POST en de 422-retry)."""

    def setUp(self) -> None:
        self._orig = submit_review._request

    def tearDown(self) -> None:
        submit_review._request = self._orig

    def test_retry_path_uses_the_same_gate(self):
        """De 422-retry mag geen variabele lezen die later pas gezet wordt."""
        api = FakeApi([[review(21, "CHANGES_REQUESTED", OLDER)]])
        submit_review._request = api
        # precies de argumenten die het retry-pad doorgeeft
        result = submit_review.maybe_cleanup(
            {"key_issues_to_review": []}, 0, None, "o/r", "1", "token", CURRENT
        )
        self.assertEqual(result, [21])

    def test_blocking_finding_blocks_cleanup_even_with_explicit_comment_event(self):
        api = FakeApi([[review(22, "CHANGES_REQUESTED", OLDER)]])
        submit_review._request = api
        result = submit_review.maybe_cleanup(
            {"key_issues_to_review": [{"x": 1}]}, 1, None, "o/r", "1", "token", CURRENT
        )
        self.assertEqual(result, [])
        self.assertEqual(api.dismissed, [])

    def test_incremental_run_blocks_cleanup(self):
        api = FakeApi([[review(23, "CHANGES_REQUESTED", OLDER)]])
        submit_review._request = api
        result = submit_review.maybe_cleanup(
            {"key_issues_to_review": []}, 0, "2026-11-05T00:00:00Z", "o/r", "1", "token", CURRENT
        )
        self.assertEqual(result, [])

    def test_api_failure_warns_and_returns_empty(self):
        class Boom(FakeApi):
            def __call__(self, method, url, token, payload=None):
                if method == "PUT":
                    raise RuntimeError("403")
                return super().__call__(method, url, token, payload)

        submit_review._request = Boom([[review(24, "CHANGES_REQUESTED", OLDER)]])
        result = submit_review.maybe_cleanup(
            {"key_issues_to_review": []}, 0, None, "o/r", "1", "token", CURRENT
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
