#!/usr/bin/env python3
"""Post an :eyes: receipt on the command comment (single, idempotent).

Called from the pr-piet reusable workflow right after the command guard, so
every accepted command gets a receipt even when the run is cancelled or fails
before pr-agent can set its own reaction (GitHub deduplicates per user and per
content, so map + tier-1 + tier-2 all calling this stays one 👀).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def main() -> int:
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    comment_id = os.environ.get("COMMENT_ID", "")
    if not token or not repo or not comment_id:
        print("GH_TOKEN, GITHUB_REPOSITORY en COMMENT_ID zijn verplicht", file=sys.stderr)
        return 0  # receipt is never fatal

    request = urllib.request.Request(
        f"{API}/repos/{repo}/issues/comments/{comment_id}/reactions",
        data=json.dumps({"content": "eyes"}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"eyes receipt: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:200]
        print(f"::warning::eyes receipt failed: HTTP {exc.code} {body}")
    except Exception as exc:  # noqa: BLE001 - receipt is never fatal
        print(f"::warning::eyes receipt failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
