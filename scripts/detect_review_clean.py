#!/usr/bin/env python3
"""
detect_review_clean.py — tier-2 poortwachter voor PR-Piet.

Zoekt de nieuwste PR-comment met een gegeven review-heading die NA een
bepaald tijdstip is aangemaakt/geüpdatet, en bepaalt of tier 1 "geen
problemen" meldde (marker: "No major issues detected").

Uitvoer op stdout:
  true   -> tier 1 is schoon (geen major issues) -> tier 2 mag draaien
  false  -> tier 1 vond problemen, óf er is geen verse review-comment

Gebruik (in de review-job):
  GITHUB_TOKEN / GITHUB_REPOSITORY zijn env-verplicht.
  python3 detect_review_clean.py <pr_number> <heading> <since-iso-timestamp>
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# Marker die pr-agent in de review-comment zet wanneer er geen key issues zijn.
CLEAN_MARKER = "no major issues detected"

API = "https://api.github.com"


def fetch_comments(repo: str, pr_number: str, token: str) -> list:
    """Haal alle issue-comments van de PR op (met pagination)."""
    comments: list = []
    page = 1
    while True:
        url = f"{API}/repos/{repo}/issues/{pr_number}/comments?per_page=100&page={page}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "pr-piet",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.load(resp)
        comments.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return comments


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    pr_number, heading, since = sys.argv[1], sys.argv[2], sys.argv[3]
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN en GITHUB_REPOSITORY zijn verplicht", file=sys.stderr)
        return 2

    try:
        comments = fetch_comments(repo, pr_number, token)
    except Exception as exc:  # noqa: BLE001 - poortwachter faalt dicht
        print(f"kon comments niet ophalen: {exc}", file=sys.stderr)
        print("false")
        return 0

    candidates = [
        c for c in comments
        if c.get("updated_at", "") >= since and heading in c.get("body", "")
    ]
    if not candidates:
        # Geen verse review-comment (bijv. commando was /describe of /improve).
        print("false")
        return 0

    latest = max(candidates, key=lambda c: c.get("updated_at", ""))
    body = (latest.get("body") or "").lower()
    print("true" if CLEAN_MARKER in body else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
