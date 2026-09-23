#!/usr/bin/env python3
"""
tier2_pending_guard.py — tweede tier-2 poortwachter: openstaande
CHANGES_REQUESTED.

Bepaalt of er een onopgeloste pr-piet CHANGES_REQUESTED-review op de
HUIDIGE head staat. Semantiek (operator-besluit 2026-09-23): tier 2 (de
second opinion) skipt zolang de laatste pr-piet CHANGES_REQUESTED-review
gepind is op precies de head die nu gereviewd wordt; een nieuwe push
(nieuwe head) opent de gate weer — dán is de second opinion de
verificatie van de fixes, niet een bevestiging van een reeds geblokkeerde
stand.

Alle formele pr-piet reviews zijn herkenbaar aan de body-marker
"pr-piet-review:v1" (submit_review.py). Tussenliggende COMMENTED-reviews
van onszelf heffen een CHANGES_REQUESTED NIET op — alleen een push (nieuwe
head) doet dat. Daarmee sluit de gate aan op de operator-perceptie:
"suggesties zijn gedaan" blijft staan tot er echt nieuwe code staat.

Uitvoer op stdout:
  true  -> openstaande CR op deze head  -> tier 2 moet skipp-en
  false -> geen CR op deze head         -> tier 2 mag draaien

Bij een API-fout faalt de poortwachter dicht ("true"): geen modeltijd
spenden op onbekende staat (zelfde richting als detect_review_clean, dat
bij een fout "false" teruggeeft en tier 2 daarmee ook skipt).

Gebruik (in de review_tier1-job, na de pr-piet clone):
  GITHUB_TOKEN / GITHUB_REPOSITORY zijn env-verplicht.
  python3 tier2_pending_guard.py <pr_number>
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# Marker die submit_review.py in élke formele pr-piet review zet.
MARKER = "pr-piet-review:v1"

API = "https://api.github.com"


def is_pending(reviews: list[dict], head_sha: str) -> bool:
    """Pure kern: True als de laatste pr-piet CR op deze head staat.

    `reviews` mag elke volgorde hebben (API-volgorde is niet gegarandeerd);
    er wordt expliciet op submitted_at gesorteerd.
    """
    ours = [r for r in reviews if MARKER in (r.get("body") or "")]
    crs = [
        r for r in ours
        if r.get("state") == "CHANGES_REQUESTED" and r.get("commit_id")
    ]
    if not crs:
        return False
    latest_cr = max(crs, key=lambda r: r.get("submitted_at") or "")
    return latest_cr["commit_id"] == head_sha


def _gh_json(repo: str, path: str, token: str) -> list | dict:
    url = f"{API}/repos/{repo}/{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "pr-piet",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_reviews(repo: str, pr_number: str, token: str) -> list:
    """Haal alle reviews van de PR op (met pagination)."""
    reviews: list = []
    page = 1
    while True:
        batch = _gh_json(
            repo,
            f"pulls/{pr_number}/reviews?per_page=100&page={page}",
            token,
        )
        reviews.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return reviews


def fetch_head_sha(repo: str, pr_number: str, token: str) -> str:
    return _gh_json(repo, f"pulls/{pr_number}", token).get("head", {}).get("sha") or ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    pr_number = sys.argv[1]
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN en GITHUB_REPOSITORY zijn verplicht", file=sys.stderr)
        return 2

    try:
        head_sha = fetch_head_sha(repo, pr_number, token)
        reviews = fetch_reviews(repo, pr_number, token)
    except Exception as exc:  # noqa: BLE001 - poortwachter faalt dicht
        print(f"kon PR-state niet ophalen: {exc}", file=sys.stderr)
        print("true")
        return 0

    print("true" if is_pending(reviews, head_sha) else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
