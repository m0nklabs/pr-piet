#!/usr/bin/env python3
"""
rewrite_event_command.py — rewrite the triggering comment body in a copy of the
GitHub event payload.

pr-agent parses the triggering comment itself, so a comment form like
``/review=z-ai/glm-5.3`` reaches it as the unknown command
``/review=z-ai/glm-5.3`` (its tokenizer splits on whitespace only). The model
override is therefore applied through the action's ``config.model`` env, and
the comment is rewritten to the plain command before the action runs.

The rewrite happens IN PLACE in the runner's own event payload
(``$GITHUB_EVENT_PATH``). Pointing the action at a copy does not work:
``GITHUB_EVENT_PATH`` is a reserved runner variable that a step-level ``env:``
cannot override (measured 2026-10-25: the action still read the original body
and answered "Unknown command: review=z-ai/glm-5.3"). The runner's
``_github_workflow/event.json`` is bind-mounted into the action container, so
writing to it on the host changes exactly the file the action reads.

Outputs (GITHUB_OUTPUT when set, otherwise stdout):
    rewritten    true when the body was replaced, false otherwise
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
    args = parser.parse_args()

    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not os.path.exists(event_path):
        print("no event payload; nothing to rewrite", file=sys.stderr)
        emit("rewritten", "false")
        return 0

    try:
        with open(event_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - fail closed: do not guess the command
        print(f"::error::cannot read event payload: {exc}", file=sys.stderr)
        emit("rewritten", "false")
        return 1

    comment = payload.get("comment")
    if not isinstance(comment, dict):
        # pull_request / push events have no comment to normalize.
        emit("rewritten", "false")
        return 0

    comment["body"] = args.command

    try:
        # Write next to the original and replace atomically, so a half-written
        # payload can never reach the action.
        tmp_path = f"{event_path}.pr-piet.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp_path, event_path)
    except Exception as exc:  # noqa: BLE001 - fail closed: do not silently keep the old body
        print(f"::error::cannot rewrite event payload: {exc}", file=sys.stderr)
        return 1

    print(f"comment body rewritten to {args.command} in {event_path}")
    emit("rewritten", "true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
