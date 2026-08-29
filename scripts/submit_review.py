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
# Dedup: per head-SHA maximaal één formele review (review_marker volgens
# reusable-github-pr-review-loop.md: <!-- generic-pr-review:v1 key=... head=... -->)
# ---------------------------------------------------------------------------

def _review_marker(commit_sha: str) -> str:
    return f"<!-- pr-piet-review:v1 head={commit_sha} -->"


def already_reviewed(repo: str, pr_number: str, token: str, commit_sha: str) -> bool:
    """Check of er al een formele review van github-actions[bot] op deze head staat.

    Gebruikt de marker in de review-body. Als de marker ontbreekt (oudere
    reviews), vallen we terug op: zelfde commit_id + zelfde event + body die
    de "PR Reviewer Guide"-heading bevat.
    """
    if not commit_sha:
        return False
    marker = _review_marker(commit_sha)
    page = 1
    while True:
        url = f"{API}/repos/{repo}/pulls/{pr_number}/reviews?per_page=100&page={page}"
        reviews = _request("GET", url, token)
        if not isinstance(reviews, list):
            break
        for rv in reviews:
            if not (rv.get("user") or {}).get("login", "").endswith("[bot]"):
                continue
            body = rv.get("body") or ""
            if marker in body:
                return True
            if (
                (rv.get("commit_id") or "") == commit_sha
                and ("PR Reviewer Guide" in body or "PR Reviewer Guide" in (rv.get("state") or ""))
            ):
                return True
        if len(reviews) < 100:
            break
        page += 1
    return False


def get_added_lines_per_file(repo: str, pr_number: str, token: str) -> dict[str, set[int]]:
    """Bouw {path: set(regelnummers)} van TOEGEVOEGDE regels (nieuwe nummering).

    GitHub's reviews REST API kan alleen inline comments plaatsen op een
    'line' (side RIGHT) die in de PR-diff staat als toegevoegde regel ('+');
    anders geeft het 422 "Line could not be resolved". Deze functie berekent
    uit de patch-hunks per bestand de set van nieuwe regelnummers die '+' zijn.
    """
    added: dict[str, set[int]] = {}
    page = 1
    while True:
        url = f"{API}/repos/{repo}/pulls/{pr_number}/files?per_page=100&page={page}"
        files = _request("GET", url, token)
        if not isinstance(files, list):
            break
        for f in files:
            path = f.get("filename", "")
            patch = f.get("patch", "") or ""
            lines_set = added.setdefault(path, set())
            new_line: int | None = None
            for line in patch.splitlines():
                if line.startswith("@@") and new_line is None:
                    # hunk header: @@ -old,count +new,count @@
                    m = re.search(r"\+(\d+)(?:,\d+)? @@", line)
                    if m:
                        new_line = int(m.group(1))
                    continue
                if new_line is None:
                    continue
                if line.startswith("+") and not line.startswith("+++"):
                    lines_set.add(new_line)
                    new_line += 1
                elif line.startswith("-") and not line.startswith("---"):
                    continue  # verwijderde regel: telt niet in de nieuwe nummering
                elif line.startswith(" "):
                    new_line += 1  # contextregel: wel in nieuwe nummering
                elif line.startswith("@@") and new_line is not None:
                    # nieuwe hunk
                    m = re.search(r"\+(\d+)(?:,\d+)? @@", line)
                    if m:
                        new_line = int(m.group(1))
                # "\ No newline..." etc.: negeer
        if len(files) < 100:
            break
        page += 1
    return added


_SUGGESTION_FENCE_RE = re.compile(r"```suggestion\n.*?\n```", re.DOTALL)


def filter_resolvable_comments(
    comments: list[dict], added_lines: dict[str, set[int]]
) -> tuple[list[dict], int]:
    """Houd alleen comments waarvan (path, line) op een toegevoegde diff-regel ligt.

    Retourneert (resolvable_comments, aantal_overgeslagen). Niet-resolvable
    threads (bv. op een pure-deletie-regel) worden overgeslagen i.p.v. dat de
    héle review faalt met 422.

    Multi-line comments (met start_line, uit een suggested_fix over meerdere
    regels) vereisen dat BEIDE eindpunten op toegevoegde regels liggen; anders
    degraderen we naar een single-line comment zonder suggestion-fence (de
    fence zou bij toepassing anders maar één regel vervangen).
    """
    kept: list[dict] = []
    skipped = 0
    for c in comments:
        path = c.get("path", "")
        line = c.get("line")
        start = c.get("start_line")
        if path not in added_lines or not isinstance(line, int):
            skipped += 1
            continue
        if line not in added_lines[path]:
            skipped += 1
            continue
        if isinstance(start, int) and start < line:
            if start in added_lines[path]:
                kept.append(c)
            else:
                # start_line niet resolvable: degradeer naar tekst-only
                c.pop("start_line", None)
                c["body"] = _SUGGESTION_FENCE_RE.sub("", c.get("body", "")).rstrip()
                kept.append(c)
        else:
            kept.append(c)
    return kept, skipped


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

    Bevat een issue een `suggested_fix` (onze pr-agent-fork vraagt hierom bij
    `pr_reviewer.require_suggested_fix=true`, single-call-modus), dan krijgt de
    inline comment een ```suggestion```-fence met de exacte vervangingscode ->
    GitHub toont een "Apply"-knop. Een fix over meerdere regels
    (start_line < end_line) wordt een multi-line comment (start_line + line),
    zodat de fence bij toepassing het hele bereik vervangt.
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
        comment = {"path": path, "line": end_line, "side": "RIGHT", "body": body}
        fix = (issue.get("suggested_fix") or "").strip()
        if fix:
            comment["body"] = f"{body}\n\n```suggestion\n{fix}\n```"
            if start_line < end_line:
                comment["start_line"] = start_line
        comments.append(comment)
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
    had_fresh_body = False
    had_stale_comment = False
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

            def _pick(cands, key) -> str:
                return max(cands, key=key).get("body") or ""

            # Eerst de recente comment (>=since) — dit is de "verse" body van
            # deze run. Is er GEEN verse comment (pr-agent postte deze run geen
            # review, bv. lege model-output) en ook geen verse review-JSON, dan
            # is er niets nieuws te posten: we gebruiken de stale comment alleen
            # als er WEL een verse review-JSON is (samenvatting erbij; de echte
            # inhoud komt uit de JSON). "Oude reviews herhalen" (stale body op
            # nieuwe head zonder verse analyse) wordt zo voorkomen — zie 1b.
            recent = [
                c for c in comments_
                if (not args.since or c.get("updated_at", "") >= args.since)
                and args.heading in c.get("body", "")
            ]
            if recent:
                body = _pick(recent, lambda c: c.get("updated_at", ""))
                had_fresh_body = True
            else:
                # GEEN verse review-comment ná since. Dit betekent dat pr-agent
                # deze run GEEN review gepost heeft (bv. lege model-output:
                # "Empty content ... finish_reason: length"). Een stale body van
                # een ÓUDE review op een NIEUWE head posten = "oude reviews
                # herhalen" (misleidend: de verbeterde code staat er niet in).
                # Alleen als we WEL een verse review-JSON hebben heeft zo'n
                # fallback zin (samenvatting erbij, comments uit de JSON). Zonder
                # verse JSON posten we dus NIET (zie 1b. hieronder).
                any_c = [
                    c for c in comments_ if args.heading in c.get("body", "")
                ]
                if any_c:
                    had_stale_comment = True
                    if review_data:
                        body = _pick(any_c, lambda c: c.get("updated_at", ""))
                        print(
                            f"(geen review-guide-comment ná since; fallback naar de "
                            f"meest recente ({any_c[0].get('updated_at','?')}))",
                            file=sys.stderr,
                        )
        except Exception as exc:  # noqa: BLE001
            print(f"kon review-body niet ophalen: {exc}", file=sys.stderr)

    # 2a. Code-suggesties. Twee modi:
    #  - Normaal (2 calls): haal de "PR Code Suggestions"-comment (/improve) op
    #    en zet de ```diff```-blokken om naar ```suggestion```-fences (Apply-knop).
    #  - PR_PIET_SINGLE_CALL=1: er is géén aparte /improve-call; de suggesties
    #    komen als `suggested_fix`-velden in de review-JSON (onze pr-agent-fork
    #    vraagt hierom via pr_reviewer.require_suggested_fix). Die worden in
    #    build_inline_comments() tot fences gewikkeld; /improve-comment ophalen
    #    is dan onnodig (en zou ook niets vinden).
    suggestions_body = ""
    single_call = os.environ.get("PR_PIET_SINGLE_CALL", "").strip().lower() in (
        "1", "true", "yes",
    )
    if single_call:
        print("(single-call-modus: suggesties uit suggested_fix-velden in de "
              "review-JSON)", file=sys.stderr)
    else:
        try:
            comments_ = fetch_issue_comments(repo, args.pr_number, token)
            s_recent = [
                c for c in comments_
                if (not args.since or c.get("updated_at", "") >= args.since)
                and args.suggestions_heading in c.get("body", "")
            ]
            if s_recent:
                suggestions_body = max(
                    s_recent, key=lambda c: c.get("updated_at", "")
                ).get("body") or ""
            else:
                s_any = [
                    c for c in comments_ if args.suggestions_heading in c.get("body", "")
                ]
                if s_any:
                    suggestions_body = max(
                        s_any, key=lambda c: c.get("updated_at", "")
                    ).get("body") or ""
                    print(
                        "(geen suggestions-comment ná since; fallback naar meest recente)",
                        file=sys.stderr,
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"kon /improve-comment niet ophalen: {exc}", file=sys.stderr)

    # 1b. FAAL-SAFE: géén verse analyse deze run, maar er WÉL een stale review
    # bestaat. Dat is het model-falasignaal (bv. lege output: "Empty content ...
    # finish_reason: length"): pr-agent postte deze run geen verse review en er
    # is ook geen verse review-JSON. Een stale body van een oude review als
    # "verse" review op een nieuwe head posten = "oude reviews herhalen"
    # (misleidend). Per harde regel 2 (model-/gateway-fout = rode workflow, geen
    # stille degradatie) faalt de job in dat geval. Zonder enige (zelfs stale)
    # review-comment is er niets mis — dat behandelt de return-0 hieronder.
    if (
        not args.no_body
        and not review_data
        and not had_fresh_body
        and had_stale_comment
    ):
        print(
            "GEEN verse review deze run: geen review-JSON én geen verse "
            "review-guide-comment ná --since (model-output leeg/afgekapt?), "
            "maar er is wél een oudere review. Stale fallback NIET gepost — "
            "job faalt (rode workflow).",
            file=sys.stderr,
        )
        return 3

    if not review_data and not body:
        print("(geen review geplaatst: geen review-JSON én geen review-comment gevonden)")
        return 0

    # 2. Inline comments uit de JSON (key-issues, evt. met suggested_fix-fences
    # in single-call-modus) + /improve-suggesties (2-call-modus).
    comments = build_inline_comments(review_data) if review_data else []
    # Haal de diff (pad -> toegevoegde regels) op voor de resolvable-filter
    # hieronder (GitHub 422 "Line could not be resolved" anders).
    added_lines: dict = {}
    if comments:
        try:
            added_lines = get_added_lines_per_file(repo, args.pr_number, token)
        except Exception as exc:  # noqa: BLE001
            print(f"kon diff-regels niet ophalen ({exc})", file=sys.stderr)
            added_lines = {}
    if not single_call:
        suggestion_comments = build_suggestion_comments(suggestions_body)
        comments.extend(suggestion_comments)

    # 2b. Filter: alleen threads waarvan de locatie op een TOEGEVOEGDE diff-regel
    # ligt (GitHub 422 "Line could not be resolved" anders). Niet-resolvable
    # threads overslaan, de rest posten — de samenvatting blijft altijd staan.
    if comments:
        try:
            comments, skipped = filter_resolvable_comments(comments, added_lines)
            if skipped:
                print(
                    f"({skipped} inline thread(s) overgeslagen: locatie niet op "
                    f"toegevoegde diff-regel)",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001
            # Kan de diff niet ophalen: behoud de 422-retry als vangnet.
            print(f"kon diff-regels niet ophalen (422-retry blijft actief): {exc}",
                  file=sys.stderr)

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

    # 4b. Dedup: deze head-SHA al formeel gereviewed? (marker in body of
    # zelfde commit_id + Reviewer Guide) -> niet opnieuw posten.
    #
    # Belangrijk: dedup slaat ALLEEN toe als er géén verse review-data is uit
    # de HUIDIGE run (review_data is None, dus we zouden een fallback-body van
    # een oude issue-comment posten). Komt de review-JSON wél door (een échte
    # nieuwe analyse), dan posten we altijd: een verse review moet een evt.
    # stale review op dezelfde head vervangen (GitHub toont de laatste als de
    # actieve review). Dedup is bedoeld om identieke hertriggers te stoppen,
    # niet om een verse analyse te blokkeren.
    if commit_sha and review_data is None:
        try:
            if already_reviewed(repo, args.pr_number, token, commit_sha):
                print(
                    f"(head {commit_sha[:8]} is al formeel gereviewed én er is "
                    f"geen verse review-JSON uit deze run — geen duplicaat "
                    f"fallback-review gepost)",
                    file=sys.stderr,
                )
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"kon dedup-check niet uitvoeren ({exc}); ga door", file=sys.stderr)

    # 4c. Marker in de review-body zodat latere runs de review herkennen.
    if body and commit_sha:
        body = body.rstrip() + "\n\n" + _review_marker(commit_sha)

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
