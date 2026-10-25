#!/usr/bin/env python3
"""Tests voor de tijdvenster-dedupe in submit_review.py (pure helpers)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from submit_review import _review_marker, minutes_since  # noqa: E402


class TestMinutesSince(unittest.TestCase):
    def test_recent_timestamp_is_small(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = minutes_since(now)
        self.assertIsNotNone(age)
        self.assertLess(age, 1.0)

    def test_hour_old_is_about_sixty(self):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        age = minutes_since(stamp)
        self.assertGreater(age, 59.0)
        self.assertLess(age, 61.0)

    def test_empty_and_garbage_are_none(self):
        self.assertIsNone(minutes_since(""))
        self.assertIsNone(minutes_since("not-a-timestamp"))

    def test_naive_timestamp_is_treated_as_utc(self):
        # GitHub levert altijd 'Z', maar een naive stamp mag geen crash geven.
        naive = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        age = minutes_since(naive)
        self.assertGreater(age, 4.0)
        self.assertLess(age, 6.0)


class TestReviewMarker(unittest.TestCase):
    def test_marker_contains_head(self):
        sha = "5ce8364b" + "0" * 32
        marker = _review_marker(sha)
        self.assertIn(sha, marker)
        self.assertTrue(marker.startswith("<!--"))
        self.assertTrue(marker.endswith("-->"))


if __name__ == "__main__":
    unittest.main()
