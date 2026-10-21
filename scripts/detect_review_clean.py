#!/usr/bin/env python3
"""
detect_review_clean.py — tier-2 poortwachter voor PR-Piet (JSON-modus).

Sinds 2026-10-21 draait pr-agent met `config.publish_output=false`: pr-agent
post géén review-comment meer (geen guide, geen persistent comment, geen
progress-post) en dus is er niets om te grep'en. De bron van de uitslag is de
review-JSON uit de action step-output (`github_action_config.enable_output`),
die onafhankelijk van `publish_output` wordt geëmitteerd.

Uitvoer op stdout:
  true   -> de review-JSON bevat geen key issues -> tier 2 mag draaien
  false  -> key issues aanwezig, óf geen/ongeldige review-JSON (faalt dicht)

Gebruik (in de review-job):
  python3 detect_review_clean.py --review-json /tmp/pr-agent-review.json
"""

from __future__ import annotations

import argparse
import json
import sys


def extract_key_issues(review_data: dict) -> list | None:
    """Geef key_issues_to_review terug, of None bij een ongeldige review-vorm.

    De step-output bevat de BINNENSTE review-dict (`github_action_output`
    schrijft `data.get("review", {})`), maar accepteer voor robustheid ook de
    gewikkelde vorm — zelfde fallback als submit_review.extract_key_issues.
    """
    review = review_data.get("review")
    if not isinstance(review, dict):
        review = review_data
    issues = review.get("key_issues_to_review")
    if not isinstance(issues, list):
        return None
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bepaal 'clean' uit de pr-agent review-JSON (geen API-calls)."
    )
    parser.add_argument(
        "--review-json",
        required=True,
        help="pad naar het bestand met de review-JSON uit de action step-output",
    )
    args = parser.parse_args()

    try:
        with open(args.review_json, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - poortwachter faalt dicht
        print(f"kon review-JSON niet lezen: {exc}", file=sys.stderr)
        print("false")
        return 0

    if not isinstance(data, dict):
        print("review-JSON is geen object", file=sys.stderr)
        print("false")
        return 0

    issues = extract_key_issues(data)
    if issues is None:
        # Geen geldige review-vorm (bijv. schema-deviatie): niet clean.
        print("review-JSON mist review.key_issues_to_review (lijst)", file=sys.stderr)
        print("false")
        return 0

    print("true" if not issues else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
