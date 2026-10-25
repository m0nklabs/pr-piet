#!/usr/bin/env python3
"""
rewrite_event_command.py — rewrite the triggering comment body in a copy of the
GitHub event payload.

pr-agent parses the triggering comment itself, so a comment form like
``/review=z-ai/glm-5.3`` reaches it as the unknown command
``/review=z-ai/glm-5.3`` (its tokenizer splits on whitespace only). The model
override is therefore applied through the action's ``config.model`` env, and
the comment is rewritten to the plain command before the action runs.

The rewritten payload must be readable INSIDE the pr-agent docker container, so
callers pass both the host path (``--out``) and the container path
(``--container-out``, normally ``/github/workspace/...``).

Outputs (GITHUB_OUTPUT when set, otherwise stdout):
    event_path   container path to use as GITHUB_EVENT_PATH, or empty when the
                 event carries no comment (nothing to rewrite)
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def emit(key: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}"
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default="/review", help="replacement body")
    parser.add_argument("--out", required=True, help="host path to write")
    parser.add_argument(
        "--container-out",
        required=True,
        help="path of the same file inside the action container",
    )
    args = parser.parse_args()

    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not os.path.exists(event_path):
        print("no event payload; nothing to rewrite", file=sys.stderr)
        emit("event_path", "")
        return 0

    try:
        with open(event_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - keep the run alive, use the default path
        print(f"cannot read event payload: {exc}", file=sys.stderr)
        emit("event_path", "")
        return 0

    comment = payload.get("comment")
    if not isinstance(comment, dict):
        # pull_request / push events have no comment to normalize.
        emit("event_path", "")
        return 0

    comment["body"] = args.command

    try:
        folder = os.path.dirname(args.out)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception as exc:  # noqa: BLE001 - fail closed: do not silently keep the old body
        print(f"::error::cannot write rewritten payload: {exc}", file=sys.stderr)
        return 1

    print(f"comment body rewritten to {args.command} ({args.out})")
    emit("event_path", args.container_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
