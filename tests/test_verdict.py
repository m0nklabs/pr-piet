"""Verdict grading: only [verified-bug] may block a merge.

Operator decision 2026-11-02, following the Codex review rubric ("P0 only for
universal issues that do not depend on any assumptions"): an unverified or
unclassified finding must not block, because the reviewer cannot prove it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from submit_review import verdict_from_review  # noqa: E402


def _review(*issues):
    return {"review": {"key_issues_to_review": list(issues)}}


def _issue(header, content="A concrete trigger exists."):
    return {
        "relevant_file": "pkg/mod.py",
        "issue_header": header,
        "issue_content": content,
        "start_line": 1,
        "end_line": 2,
    }


def test_verified_bug_blocks():
    event, blocking, nonblocking, untagged = verdict_from_review(
        _review(_issue("[verified-bug] Null deref"))
    )
    assert event == "REQUEST_CHANGES"
    assert (blocking, nonblocking, untagged) == (1, 0, 0)


def test_hypothetical_does_not_block():
    event, blocking, nonblocking, untagged = verdict_from_review(
        _review(_issue("[hypothetical] Possible race"))
    )
    assert event == "COMMENT"
    assert (blocking, nonblocking, untagged) == (0, 1, 0)


def test_backlog_does_not_block():
    event, blocking, _, _ = verdict_from_review(_review(_issue("[backlog] Refactor")))
    assert event == "COMMENT"


def test_untagged_does_not_block_but_is_counted():
    """The old conservative default blocked on untagged findings; that is gone."""
    event, blocking, nonblocking, untagged = verdict_from_review(
        _review(_issue("Possible Bug"))
    )
    assert event == "COMMENT"
    assert blocking == 0
    assert nonblocking == 1
    assert untagged == 1


def test_verified_tag_with_uncertain_marker_does_not_block():
    """A low-confidence marker overrides the tag: it is not verified."""
    event, blocking, _, _ = verdict_from_review(
        _review(_issue("[verified-bug] Maybe broken", "UNCERTAIN: could not trace it"))
    )
    assert event == "COMMENT"
    assert blocking == 0


def test_mixed_findings_block_on_the_verified_one():
    event, blocking, nonblocking, untagged = verdict_from_review(
        _review(
            _issue("[hypothetical] Maybe"),
            _issue("[verified-bug] Real"),
            _issue("Unlabelled"),
        )
    )
    assert event == "REQUEST_CHANGES"
    assert (blocking, nonblocking, untagged) == (1, 2, 1)


def test_no_findings_is_comment():
    event, blocking, nonblocking, untagged = verdict_from_review(_review())
    assert event == "COMMENT"
    assert (blocking, nonblocking, untagged) == (0, 0, 0)


def test_tag_matching_is_case_insensitive():
    event, blocking, _, _ = verdict_from_review(_review(_issue("[VERIFIED-BUG] Shouty")))
    assert event == "REQUEST_CHANGES"
    assert blocking == 1
