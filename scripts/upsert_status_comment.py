#!/usr/bin/env python3
"""
upsert_status_comment.py — één persistente PR-Piet-statuscomment per PR.

Operator-besluit 2026-10-03 (caretaker PR #12): het "clean = niets posten"-
beleid maakte de eindbeoordeling onzichtbaar — er was geen uitslag te zien.
De oplossing is één comment per PR die bij ELKE review wordt BIJGEWERKT
(niet telkens een nieuwe): de vaste plek waar de eindbeoordeling staat.

  clean     -> "✅ PR-Piet — geen bevindingen"  (+ head)
  findings  -> "⚠️ PR-Piet — bevindingen"       (+ verwijzing naar de
               formele review met de inline suggesties)

De comment wordt herkend aan de marker `<!-- pr-piet-status:v1 -->`; bestaat
hij al, dan wordt de body ge-PATCHt (edit), anders wordt hij aangemaakt.

Uitvoer: `posted <id>` / `updated <id>` op stdout.

Gebruik (in de review_tier1-job, na een geslaagde submit):
  GITHUB_TOKEN / GITHUB_REPOSITORY zijn env-verplicht.
  python3 upsert_status_comment.py --pr-number N --state clean|findings \
      --head <sha> [--detail "12 bestanden"]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

STATUS_MARKER = "<!-- pr-piet-status:v1 -->"

API = "https://api.github.com"


def render_status(state: str, head: str, detail: str = "") -> str:
    """Bouw de statuscomment-body voor de gegeven review-fase.

    Één post per PR, in de tijd geëdit: running (start) -> clean/findings
    (eindconclusie) — nooit drie losse posts.
    """
    head_ref = f"`{head[:8]}`" if head else "`?`"
    extra = f" — {detail}" if detail else ""
    if state == "running":
        return (
            f"{STATUS_MARKER}\n"
            f"**PR-Piet — review in progress**\n\n"
            f"Head: {head_ref}{extra}\n\n"
            f"_This comment is updated with the outcome._"
        )
    if state == "clean":
        headline = "**PR-Piet — no findings**"
        tail = "All changed files were reviewed."
    elif state == "failed":
        headline = "**PR-Piet — review failed**"
        tail = "No review was posted; see the workflow run for the error."
    else:
        headline = "**PR-Piet — findings**"
        tail = "See the review with inline suggestions under Reviews."
    return (
        f"{STATUS_MARKER}\n"
        f"{headline}\n\n"
        f"Head: {head_ref}{extra}\n\n"
        f"{tail}\n\n"
        f"_This comment is updated on every review._"
    )


def find_status_comment(comments: list) -> dict | None:
    """Zoek de bestaande statuscomment (nieuwste met onze marker)."""
    matches = [c for c in comments if STATUS_MARKER in (c.get("body") or "")]
    if not matches:
        return None
    return max(matches, key=lambda c: c.get("updated_at") or "")


def _request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "pr-piet",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def fetch_comments(repo: str, pr_number: str, token: str) -> list:
    comments: list = []
    page = 1
    while True:
        url = f"{API}/repos/{repo}/issues/{pr_number}/comments?per_page=100&page={page}"
        batch = _request("GET", url, token)
        comments.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return comments


def fetch_head_sha(repo: str, pr_number: str, token: str) -> str:
    """Head-sha via de API (werkt ook bij issue_comment-events, waar de
    event-payload geen pull_request-object heeft)."""
    pr = _request("GET", f"{API}/repos/{repo}/pulls/{pr_number}", token)
    return (pr.get("head") or {}).get("sha") or ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-number", required=True)
    parser.add_argument(
        "--state", required=True, choices=["running", "clean", "findings", "failed"]
    )
    parser.add_argument("--head", default="")
    parser.add_argument("--detail", default="")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN en GITHUB_REPOSITORY zijn verplicht", file=sys.stderr)
        return 2

    head = args.head
    if not head:
        try:
            head = fetch_head_sha(repo, args.pr_number, token)
        except Exception as exc:  # noqa: BLE001 - alleen cosmetisch
            print(f"kon head-sha niet ophalen: {exc}", file=sys.stderr)

    body = render_status(args.state, head, args.detail)
    try:
        existing = find_status_comment(fetch_comments(repo, args.pr_number, token))
        if existing:
            _request(
                "PATCH",
                f"{API}/repos/{repo}/issues/comments/{existing['id']}",
                token,
                {"body": body},
            )
            print(f"updated {existing['id']}")
        else:
            created = _request(
                "POST",
                f"{API}/repos/{repo}/issues/{args.pr_number}/comments",
                token,
                {"body": body},
            )
            print(f"posted {created.get('id')}")
    except Exception as exc:  # noqa: BLE001 - statuscomment is niet fataal
        print(f"kon statuscomment niet bijwerken: {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
