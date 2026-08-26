#!/usr/bin/env python3
"""
submit_review.py — PR-Piet converter: pr-agent review + /improve -> formele GitHub review.

Zet de pr-agent output om naar een formele pull-request review via de
GitHub REST API (POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews),
zodat de PR eruitziet zoals een Copilot-review:

  - state-badge: REQUEST_CHANGES (key issues) of COMMENT (schoon)
  - inline comment-threads op de diff (path/line/side/body)
  - de samenvattende markdown als review-body
  - /improve code-suggesties: de ```diff```-blokken worden omgezet naar
    ```suggestion```-fences, zodat GitHub een "Apply"-knop toont (zoals
    Copilot-suggesties)

Bronnen (gecombineerd in één formele review):
  1. --review-json <bestand>: JSON zoals pr-agent die naar GITHUB_OUTPUT
     schrijft (github_action_config.enable_output=true) -> {"review": {...}}
     (key-issues -> inline comments[])
  2. de nieuwste issue-comment met een gegeven heading (--heading) ->
     review-body ("PR Reviewer Guide"-markdown)
  3. de nieuwste issue-comment met --suggestions-heading (default
     "PR Code Suggestions") -> /improve suggesties -> ```suggestion```-fences

Gebruik (in de review-job):
  GITHUB_TOKEN / GITHUB_REPOSITORY zijn env-verplicht.
  python3 submit_review.py \
    --pr-number <n> \
    [--review-json <file>] \
    [--heading "PR Reviewer Guide"] \
    [--since <iso-timestamp>] \
    [--suggestions-heading "PR Code Suggestions"] \
    [--event auto|COMMENT|REQUEST_CHANGES|APPROVE] \
    [--commit-sha <sha>] \
    [--no-body]

Uitvoer op stdout:
  review-URL (https://github.com/.../pull/N#pullrequestreview-<id>) of
  "(geen review geplaatst)" met reden.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-piet",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"GitHub API {exc.code} op {url}: {detail}") from exc


def fetch_issue_comments(repo: str, pr_number: str, token: str) -> list:
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


def get_pr_head_sha(repo: str, pr_number: str, token: str) -> str:
    pr = _request("GET", f"{API}/repos/{repo}/pulls/{pr_number}", token)
    return (pr.get("head") or {}).get("sha", "")


# ---------------------------------------------------------------------------
# Review-data
# ---------------------------------------------------------------------------

def load_review_json(path: str) -> dict:
    """Lees de pr-agent output-JSON ({"review": {...}})."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def extract_key_issues(review_data: dict) -> list:
    """Haal key_issues_to_review uit de JSON (met fallback op nested keys)."""
    review = review_data.get("review") or review_data
    issues = review.get("key_issues_to_review")
    if not isinstance(issues, list):
        return []
    return issues


def build_inline_comments(review_data: dict) -> list:
    """Vertaal key-issues naar comments[] voor de reviews API.

    Gebruikt de nieuwe-lijn-nummering (line/side RIGHT) — robuuster dan
    diff-position en wat Copilot/nieuwe API ook gebruikt.
    """
    comments = []
    for issue in extract_key_issues(review_data):
        if not isinstance(issue, dict):
            continue
        path = (issue.get("relevant_file") or "").strip()
        try:
            start_line = int(str(issue.get("start_line", 0)).strip())
            end_line = int(str(issue.get("end_line", 0)).strip())
        except (TypeError, ValueError):
            start_line, end_line = 0, 0
        content = (issue.get("issue_content") or "").strip()
        header = (issue.get("issue_header") or "Issue").strip()
        if not path or not content or start_line < 1 or end_line < start_line:
            continue
        body = f"**{header}**\n\n{content}" if header != "Issue" else content
        comments.append(
            {
                "path": path,
                "line": end_line,
                "side": "RIGHT",
                "body": body,
            }
        )
    return comments


# ---------------------------------------------------------------------------
# /improve (code suggestions): ```diff``` in de "PR Code Suggestions"-comment
# omzetten naar ```suggestion```-fences (GitHub "Apply"-knop).
# ---------------------------------------------------------------------------

# Link-formaat in de suggestie-comment:
#   [src/foo.py [12-18]](https://github.com/.../pull/4/files#diff-<sha>R12-R18)
_DIFF_ANCHOR_RE = re.compile(
    r"\[(?P<file>[^\]]+?)\s*\[?(?P<start>\d+)(?:-(?P<end>\d+))?\]?"
    r"\]\((?P<url>[^)]*#diff-[0-9a-f]+R(?P<rstart>\d+)(?:-R(?P<rend>\d+))?)"
)

_DIFF_BLOCK_RE = re.compile(r"```diff\n(?P<body>.*?)```", re.DOTALL)


def _parse_suggestion_diff(diff_body: str) -> str:
    """Haal de voorgestelde code (+ regels) uit een ```diff```-blok.

    De verbeterde code bestaat uit de '+ ' regels (zonder het '+' prefix).
    Contextregels (spatie) horen bij beide; we gebruiken de '+' regels als de
    nieuwe code voor de ```suggestion```-fence.
    """
    lines = diff_body.splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
        elif line.startswith(" ") or line.startswith("\\"):
            out.append(line[1:])
        # '- ' verwijderde regels: niet opnemen (het is de NIEUWE code)
    return "\n".join(out)


def build_suggestion_comments(suggestions_body: str) -> list:
    """Zet de 'PR Code Suggestions ✨'-comment om naar suggestion-comments[].

    Elke ```diff```-blok met een diff-anchor ([file [start-end]](...#diff-...R...))
    wordt een inline comment met een ```suggestion```-fence (de voorgestelde
    code) -> GitHub toont een "Apply"-knop, zoals Copilot-suggesties.
    """
    if not suggestions_body:
        return []
    comments = []
    # Per suggestie: een anchor-geparaf meteen gevolgd door een ```diff```-blok.
    # We scannen op het patroon anchor ... ```diff ... ```.
    idx = 0
    while True:
        m = _DIFF_ANCHOR_RE.search(suggestions_body, idx)
        if not m:
            break
        start = m.start()
        rel_file = (m.group("file") or "").strip()
        a_start = int(m.group("start"))
        a_end = int(m.group("end") or m.group("start"))
        # Zoek het dichtstbijzijnde ```diff```-blok NA deze anchor
        blk = _DIFF_BLOCK_RE.search(suggestions_body, m.end())
        if blk:
            new_code = _parse_suggestion_diff(blk.group("body"))
            line = a_end
            # tussen de anchor en het diff-blok kan 'suggestion_content' staan;
            # gewoon de fence geven is voldoende voor de Apply-knop.
            comment_body = (
                f"**Suggestion:**\n\n"
                f"[{rel_file} {a_start}-{a_end}]({m.group('url')})\n\n"
                f"```suggestion\n{new_code.rstrip()}\n```"
            )
            comments.append(
                {
                    "path": rel_file,
                    "line": line,
                    "side": "RIGHT",
                    "body": comment_body,
                }
            )
            idx = blk.end()
        else:
            idx = m.end()
    return comments


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--review-json", help="pr-agent output-JSON bestand")
    parser.add_argument("--heading", default="PR Reviewer Guide",
                        help="heading van de pr-agent review-comment (fallback body)")
    parser.add_argument("--since", help="ISO-timestamp: alleen comments hierna")
    parser.add_argument("--suggestions-heading", default="PR Code Suggestions",
                        help="heading van de pr-agent /improve-comment (code suggestions)")
    parser.add_argument("--event", default="auto",
                        choices=["auto", "COMMENT", "REQUEST_CHANGES", "APPROVE"])
    parser.add_argument("--commit-sha", help="head commit-sha (default: via API)")
    parser.add_argument("--no-body", action="store_true",
                        help="post alleen comments[], geen review-body")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN en GITHUB_REPOSITORY zijn verplicht", file=sys.stderr)
        return 2

    # 1. Review-data: JSON of issue-comment (fallback)
    review_data: dict | None = None
    body = ""
    if args.review_json:
        try:
            review_data = load_review_json(args.review_json)
        except Exception as exc:  # noqa: BLE001
            print(f"kon review-JSON niet laden ({args.review_json}): {exc}",
                  file=sys.stderr)

    if not args.no_body:
        # Body ALTIJD uit de zojuist geplaatste issue-comment halen (pr-agent
        # schrijft de "PR Reviewer Guide"-markdown daar), ongeacht of we ook de
        # JSON hebben. De JSON levert de gestructureerde key-issues (comments[]);
        # de markdown-body komt uit de issue-comment.
        try:
            comments_ = fetch_issue_comments(repo, args.pr_number, token)
            candidates = [
                c for c in comments_
                if (not args.since or c.get("updated_at", "") >= args.since)
                and args.heading in c.get("body", "")
            ]
            if candidates:
                latest = max(candidates, key=lambda c: c.get("updated_at", ""))
                body = latest.get("body") or ""
        except Exception as exc:  # noqa: BLE001
            print(f"kon review-body niet ophalen: {exc}", file=sys.stderr)

    # 2a. Code-suggesties (/improve): haal de "PR Code Suggestions"-comment op
    # en zet de ```diff```-blokken om naar ```suggestion```-fences. Deze en de
    # key-issues samen vormen de inline comments[] van de formele review.
    suggestions_body = ""
    try:
        comments_ = fetch_issue_comments(repo, args.pr_number, token)
        s_candidates = [
            c for c in comments_
            if (not args.since or c.get("updated_at", "") >= args.since)
            and args.suggestions_heading in c.get("body", "")
        ]
        if s_candidates:
            suggestions_body = max(
                s_candidates, key=lambda c: c.get("updated_at", "")
            ).get("body") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"kon /improve-comment niet ophalen: {exc}", file=sys.stderr)

    if not review_data and not body:
        print("(geen review geplaatst: geen review-JSON én geen review-comment gevonden)")
        return 0

    # 2. Inline comments uit de JSON (key-issues) + /improve-suggesties
    comments = build_inline_comments(review_data) if review_data else []
    suggestion_comments = build_suggestion_comments(suggestions_body)
    comments.extend(suggestion_comments)

    # 3. Event bepalen
    event = args.event
    if event == "auto":
        if comments:
            event = "REQUEST_CHANGES"
        elif review_data is None and body:
            # Fallback-route (geen JSON): leid het event af uit de body.
            event = (
                "COMMENT"
                if "no major issues detected" in body.lower()
                else "REQUEST_CHANGES"
            )
        else:
            event = "COMMENT"

    # Harde regel 8: NOOIT APPROVE. Altijd human merge — PR-Piet mag een PR
    # nooit goedkeuren, alleen (informatief) COMMENT of (blokkerend)
    # REQUEST_CHANGES posten. Defense-in-depth mocht iemand --event APPROVE
    # doorgeven.
    if event == "APPROVE":
        print("geweigerd: event APPROVE is verboden (harde regel: altijd human merge)")
        return 1

    # 4. Head commit-sha
    commit_sha = args.commit_sha or ""
    if not commit_sha:
        try:
            commit_sha = get_pr_head_sha(repo, args.pr_number, token)
        except Exception as exc:  # noqa: BLE001
            print(f"kon head-sha niet ophalen: {exc}", file=sys.stderr)

    # 5. POST review
    payload: dict = {"event": event}
    if commit_sha:
        payload["commit_id"] = commit_sha
    if body:
        payload["body"] = body
    if comments:
        payload["comments"] = comments

    try:
        result = _request(
            "POST",
            f"{API}/repos/{repo}/pulls/{args.pr_number}/reviews",
            token,
            payload,
        )
    except RuntimeError as exc:
        # Bij 422 (bv. een inline-locatie niet in de diff) retry zonder
        # comments — de samenvatting blijft dan als formele review staan.
        print(f"review met inline comments mislukt: {exc}", file=sys.stderr)
        if comments and body:
            try:
                result = _request(
                    "POST",
                    f"{API}/repos/{repo}/pulls/{args.pr_number}/reviews",
                    token,
                    {"event": event, "commit_id": commit_sha or None, "body": body},
                )
                print(result.get("html_url", "(geen url)"))
                print("(inline comments overgeslagen: locaties niet in diff)", file=sys.stderr)
                return 0
            except RuntimeError as exc2:  # noqa: BLE001
                print(f"review zonder comments mislukt ook: {exc2}", file=sys.stderr)
        return 1

    print(result.get("html_url", "(geen url in response)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
