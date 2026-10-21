#!/usr/bin/env python3
"""Tests voor detect_review_clean.py (JSON-modus, sinds publish_output=false)."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "detect_review_clean.py"


def run_detect(payload: str, tmp_path: Path) -> str:
    review_file = tmp_path / "review.json"
    review_file.write_text(payload, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--review-json", str(review_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def run_detect_missing_file(tmp_path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--review-json", str(tmp_path / "bestaat-niet.json")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    return result.stdout.strip()


def test_clean_when_no_key_issues(tmp_path):
    payload = json.dumps({"review": {"key_issues_to_review": [], "estimate_effort_to_review": "1"}})
    assert run_detect(payload, tmp_path) == "true"


def test_findings_when_key_issues_present(tmp_path):
    payload = json.dumps(
        {"review": {"key_issues_to_review": [{"relevant_file": "a.py", "issue_header": "Possible Bug"}]}}
    )
    assert run_detect(payload, tmp_path) == "false"


def test_fail_closed_on_missing_file(tmp_path):
    assert run_detect_missing_file(tmp_path) == "false"


def test_fail_closed_on_invalid_json(tmp_path):
    assert run_detect("{ geen geldige json", tmp_path) == "false"


def test_fail_closed_on_missing_key_issues_key(tmp_path):
    # Een review-JSON zonder key_issues_to_review is geen geldige review-vorm:
    # nooit clean (fail-closed), anders zou tier-2 op een deviatie draaien.
    payload = json.dumps({"review": {"estimate_effort_to_review": "2"}})
    assert run_detect(payload, tmp_path) == "false"


def test_fail_closed_on_non_object_root(tmp_path):
    assert run_detect(json.dumps(["review"]), tmp_path) == "false"


def test_accepts_flat_review_shape(tmp_path):
    # De step-output bevat de BINNENSTE review-dict (github_action_output
    # schrijft data.get("review", {})) — de vlakke vorm is de normale vorm.
    payload = json.dumps({"key_issues_to_review": [], "estimate_effort_to_review": "1"})
    assert run_detect(payload, tmp_path) == "true"


def test_flat_shape_with_issues_is_findings(tmp_path):
    payload = json.dumps({"key_issues_to_review": [{"relevant_file": "a.py"}]})
    assert run_detect(payload, tmp_path) == "false"
