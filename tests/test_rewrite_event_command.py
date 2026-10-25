#!/usr/bin/env python3
"""Tests voor rewrite_event_command.py (in-place payload-rewrite)."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rewrite_event_command.py"


def run(payload, tmp_path, *extra):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "GITHUB_EVENT_PATH": str(event_file),
            "PATH": "/usr/bin:/bin",
        },
    )
    written = json.loads(event_file.read_text(encoding="utf-8"))
    return result, written


def test_issue_comment_body_is_replaced(tmp_path):
    payload = {"comment": {"body": "/review=z-ai/glm-5.3", "id": 1}, "issue": {"number": 26}}
    result, written = run(payload, tmp_path)
    assert result.returncode == 0
    assert written["comment"]["body"] == "/review"
    # De rest van de payload blijft intact.
    assert written["comment"]["id"] == 1
    assert written["issue"]["number"] == 26
    assert "rewritten=true" in result.stdout


def test_pull_request_payload_is_untouched(tmp_path):
    payload = {"action": "opened", "pull_request": {"number": 5}}
    result, written = run(payload, tmp_path)
    assert result.returncode == 0
    assert written == payload
    assert "rewritten=false" in result.stdout


def test_custom_command(tmp_path):
    payload = {"comment": {"body": "/review2=x"}}
    _, written = run(payload, tmp_path, "--command", "/describe")
    assert written["comment"]["body"] == "/describe"


def test_missing_payload_fails_closed(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        env={"GITHUB_EVENT_PATH": str(tmp_path / "nope.json"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert "rewritten=false" in result.stdout


def test_no_tmp_file_left_behind(tmp_path):
    payload = {"comment": {"body": "/review=x"}}
    run(payload, tmp_path)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
