"""Unit-tests voor de rc-3 faal-safe in submit_review.main() — het pad
"review-commando draaide maar pr-agent postte gar niets".

Aanleiding (journal 2026-11-02/03 + voorstel 2, goedgekeurd 2026-11-18): bij
een parse-faal op een éérste review bestond een pad waarin submit_review
stil exittte met 0 → groene run zónder review (schending harde regel 2).

main() heeft netwerkafhankelijkheden (GITHUB_TOKEN check komt vóór het pad),
dus deze tests roepen main() met afwezige env en vangen de return vóór het
API-deel: het doel-pad (leeg signaal) ligt ná de env-check, dus we mocken
sys.argv en de env via unittest.mock.patch en patchen de netwerkfuncties
stub.

Run: python3 -m unittest tests.test_submit_review_silent_degradation -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import submit_review  # noqa: E402


def _run_main(argv_extra: list[str]) -> int:
    """Roep submit_review.main() aan met minimale (gestubde) omgeving."""
    base_argv = [
        "submit_review.py",
        "--pr-number", "42",
        "--since", "2026-11-18T00:00:00Z",
    ] + argv_extra
    env = {
        "GITHUB_TOKEN": "stub-token",
        "GITHUB_REPOSITORY": "m0nklabs/pr-piet-test",
    }
    with mock.patch.object(sys, "argv", base_argv), \
         mock.patch.dict(os.environ, env, clear=False), \
         mock.patch.object(submit_review, "fetch_issue_comments", return_value=[]), \
         mock.patch.object(submit_review, "get_pr_head_sha", return_value="a" * 40):
        return submit_review.main()


class TestSilentDegradationFailsClosed(unittest.TestCase):
    def test_no_signal_at_all_is_rc3(self):
        """Geen JSON, geen body, geen stale, geen failure-comment → rc 3."""
        rc = _run_main([])
        self.assertEqual(rc, 3)

    def test_no_body_mode_stays_rc0(self):
        """--no-body zonder JSON is by design een no-op (rc 0)."""
        rc = _run_main(["--no-body"])
        self.assertEqual(rc, 0)

    def test_with_review_json_still_proceeds(self):
        """Met verse JSON doorloopt main() de normale route — geen rc 3 en
        geen rc 0-haar; de env-mock voorkomt de echte POST."""
        json_path = Path(__file__).resolve().parents[1] / ".scratch" / "test_review.json"
        json_path.parent.mkdir(exist_ok=True)
        json_path.write_text(
            '{"review": {"key_issues_to_review": [{"issue_header": '
            '"[backlog] Ok", "issue_content": "c", "relevant_file": "a.py", '
            '"start_line": 1, "end_line": 1}]}}',
            encoding="utf-8",
        )
        with mock.patch.object(submit_review, "get_added_lines_per_file",
                               return_value={}), \
             mock.patch.object(submit_review, "_request",
                               return_value={"id": 1}) as posted, \
             mock.patch.object(submit_review, "find_reviews_for_head",
                               return_value=[]), \
             mock.patch.object(submit_review, "latest_findings_state",
                               return_value=[]):
            rc = _run_main(["--review-json", str(json_path)])
        self.assertEqual(rc, 0)
        posted.assert_called_once()


if __name__ == "__main__":
    unittest.main()
