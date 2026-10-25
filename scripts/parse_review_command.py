#!/usr/bin/env python3
"""
parse_review_command.py — parse a PR comment into a pr-agent command plus an
optional model override.

Supported forms (one command per comment, on its own line):

    /review                     review with the configured tier-1 model
    /review=<model>             review with <model> (must be in the allowlist)
    /review2                    second opinion with the configured tier-2 model
    /review2=<model>            second opinion with <model>
    /describe /improve /ask ...  passed through unchanged (no model override)

The model id may be written the way the gateway catalog spells it
(``openrouter/z-ai/glm-5.3``, ``ai-kvm2-local/qwen/qwen3.5-9b``) or as the bare
gateway id (``z-ai/glm-5.3``). A leading ``openai/`` litellm routing prefix is
accepted and re-applied, so both ``/review=z-ai/glm-5.3`` and
``/review=openai/z-ai/glm-5.3`` work.

HARD RULE 1: only models listed in config/models.allowed.yaml (open-weights)
are accepted. An unknown id exits 3 so the workflow can fail loudly instead of
silently reviewing with a different model.

Outputs (GITHUB_OUTPUT when set, otherwise stdout as key=value lines):
    proceed      true|false          run the workflow at all
    command      review|review2|<other>|none
    model        effective litellm model, e.g. openai/z-ai/glm-5.3
    model_label  display form, e.g. z-ai/glm-5.3
    override     true|false          a model override was requested
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import yaml

# Commands pr-agent understands in a comment (the fork adds review2).
PASSTHROUGH_COMMANDS = (
    "improve",
    "describe",
    "ask",
    "help",
    "update_changelog",
    "add_docs",
    "generate_labels",
    "find_similar_component",
    "feedback",
)
REVIEW_COMMANDS = ("review", "review2")

# Gateway route prefixes that may precede a model id in the catalog listing.
ROUTE_PREFIXES = (
    "openrouter/",
    "ai-kvm2-local/",
    "14700k-local/",
    "nvidia/",
    "groq/",
    "openai/",
)

COMMAND_RE = re.compile(
    r"^[ \t]*/(?P<cmd>[A-Za-z0-9_]+)(?:=(?P<model>\S+))?(?:[ \t]|$)"
)


def first_command(body: str) -> tuple[str, str] | None:
    """Return (command, model_override) from the first command line, or None."""
    for line in (body or "").splitlines():
        match = COMMAND_RE.match(line)
        if not match:
            continue
        command = match.group("cmd").lower()
        if command not in REVIEW_COMMANDS and command not in PASSTHROUGH_COMMANDS:
            continue
        return command, (match.group("model") or "")
    return None


def load_allowlist(path: str) -> list[str]:
    """Read the allowed model ids (bare gateway ids) from the YAML file."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    models = data.get("models") or []
    if not isinstance(models, list):
        raise ValueError(f"{path}: 'models' must be a list")
    return [str(m).strip() for m in models if str(m).strip()]


def candidates(raw: str) -> list[str]:
    """Spellings to try for one requested id, most literal first."""
    seen: list[str] = []

    def add(value: str) -> None:
        value = value.strip().strip("/")
        if value and value not in seen:
            seen.append(value)

    add(raw)
    add(raw.removeprefix("openai/"))
    for prefix in ROUTE_PREFIXES:
        add(raw.removeprefix(prefix))
        add(raw.removeprefix("openai/").removeprefix(prefix))
    return seen


def resolve_model(raw: str, allowed: list[str]) -> str | None:
    """Return the canonical allowed id for a requested model, or None."""
    allowed_set = set(allowed)
    for candidate in candidates(raw):
        if candidate in allowed_set:
            return candidate
    return None


def emit(pairs: dict[str, str]) -> None:
    """Write outputs to GITHUB_OUTPUT when available, else to stdout."""
    target = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in pairs.items()]
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comment-body",
        default=os.environ.get("COMMENT_BODY", ""),
        help="comment body (default: $COMMENT_BODY)",
    )
    parser.add_argument(
        "--allowlist",
        default="config/models.allowed.yaml",
        help="YAML file with the allowed open-weights model ids",
    )
    parser.add_argument(
        "--default-model",
        required=True,
        help="model to use when the comment carries no override (litellm form)",
    )
    parser.add_argument(
        "--expect",
        choices=REVIEW_COMMANDS,
        default=None,
        help="only honour a model override on this command (tier isolation)",
    )
    args = parser.parse_args()

    default_model = args.default_model.strip()
    default_label = default_model.removeprefix("openai/")
    base = {
        "proceed": "false",
        "command": "none",
        "model": default_model,
        "model_label": default_label,
        "override": "false",
    }

    found = first_command(args.comment_body)
    if found is None:
        emit(base)
        return 0

    command, raw_model = found
    if args.expect is not None and command != args.expect:
        # Another command owns this comment (e.g. /review2 for tier 2): keep the
        # configured default so one tier never inherits the other tier's override.
        emit(base)
        return 0

    base["command"] = command
    base["proceed"] = "true"

    if not raw_model:
        emit(base)
        return 0

    if command not in REVIEW_COMMANDS:
        print(
            f"::error::/…={raw_model} is only supported for /review and /review2 "
            f"(got /{command})",
            file=sys.stderr,
        )
        return 3

    try:
        allowed = load_allowlist(args.allowlist)
    except Exception as exc:  # noqa: BLE001 - fail closed, never guess
        print(f"::error::cannot read model allowlist: {exc}", file=sys.stderr)
        return 3

    canonical = resolve_model(raw_model, allowed)
    if canonical is None:
        print(
            f"::error::model '{raw_model}' is not allowed (open-weights allowlist: "
            f"{args.allowlist}). Allowed: {', '.join(allowed)}",
            file=sys.stderr,
        )
        return 3

    base["model"] = f"openai/{canonical}"
    base["model_label"] = canonical
    base["override"] = "true"
    emit(base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
