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

POLICY (operator decision 2026-10-26): the gate depends on WHO asks.
  * An actor listed in --unrestricted-actors (the operator's own GitHub
    account) may pick ANY model the guardian gateway serves; the id is
    validated against the live catalog (GET {gateway}/v1/models), so it is
    always current instead of a stale static list.
  * Every other actor is limited to config/models.allowed.yaml (open-weights).
An unknown id exits 3 so the workflow fails loudly instead of silently
reviewing with a different model.

When a bare vendor/model id matches several routes the most harmless route
wins: local GPU first, then the github-copilot subscription bridge, then the
metered cloud routes (see ROUTE_PRIORITY).

Outputs (GITHUB_OUTPUT when set, otherwise stdout as key=value lines):
    proceed      true|false          run the workflow at all
    command      review|review2|<other>|none
    model        effective litellm model, e.g. openai/z-ai/glm-5.3
    model_label  display form, e.g. z-ai/glm-5.3
    override     true|false          a model override was requested
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

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

# When a bare id matches several catalog routes, prefer the cheapest/harmless
# one: local GPU (free) -> github-copilot (subscription billing) -> metered
# cloud routes. Anything not listed sorts last, alphabetically.
ROUTE_PRIORITY = (
    "ai-kvm2-local/",
    "14700k-local/",
    "github-copilot/",
    "openrouter/",
    "groq/",
    "nvidia/",
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


def fetch_catalog(url: str, key: str, timeout: float = 10.0) -> list[str] | None:
    """Live model ids from the gateway (None when unreachable).

    The catalog is the source of truth for "what guardian serves", so the
    operator-scoped gate never goes stale the way a hand-maintained list does.
    """
    if not url:
        return None
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:  # noqa: BLE001 - caller decides fail-open/closed
        print(f"gateway catalog unavailable ({exc})", file=sys.stderr)
        return None
    return [entry.get("id", "") for entry in payload.get("data", []) if entry.get("id")]


def route_rank(model_id: str) -> tuple[int, str]:
    for index, prefix in enumerate(ROUTE_PRIORITY):
        if model_id.startswith(prefix):
            return (index, model_id)
    return (len(ROUTE_PRIORITY), model_id)


def resolve_in_catalog(raw: str, catalog: list[str]) -> str | None:
    """Best catalog match for a requested id.

    Exact matches win. Otherwise the LONGEST suffix wins, so
    ``openai/gpt-6.1-sol`` resolves against ``*/openai/gpt-6.1-sol`` and not
    against an unrelated vendor that happens to end in ``/gpt-6.1-sol``.
    Among equally specific matches the route priority decides (local GPU ->
    github-copilot subscription -> metered cloud), and the chosen id is
    logged so the operator always sees which billing path ran.
    """
    catalog_set = set(catalog)
    exact = [c for c in candidates(raw) if c in catalog_set]
    if exact:
        return sorted(exact, key=route_rank)[0]

    for suffix in candidates(raw):
        matches = [c for c in catalog if c.endswith(f"/{suffix}")]
        if matches:
            return sorted(matches, key=route_rank)[0]
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
    parser.add_argument(
        "--actor",
        default="",
        help="GitHub login that wrote the comment (scopes the policy gate)",
    )
    parser.add_argument(
        "--unrestricted-actors",
        default="",
        help="comma-separated logins allowed to pick ANY gateway model",
    )
    parser.add_argument(
        "--catalog-url",
        default="",
        help="gateway /v1/models URL; empty = skip the catalog check",
    )
    parser.add_argument(
        "--catalog-key-env",
        default="GUARDIAN_API_KEY",
        help="env var holding the gateway key for the catalog request",
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

    unrestricted = {
        login.strip().lower().lstrip("@")
        for login in args.unrestricted_actors.split(",")
        if login.strip()
    }
    actor = args.actor.strip().lower().lstrip("@")

    if actor and actor in unrestricted:
        # Operator scope: anything the gateway actually serves may be used.
        catalog = fetch_catalog(args.catalog_url, os.environ.get(args.catalog_key_env, ""))
        if catalog is None:
            # Fail-open: the gateway is the thing that would reject an unknown
            # id anyway, and its error is just as loud. Never block a review
            # because the policy source had a hiccup.
            canonical = raw_model.removeprefix("openai/")
            print(f"::warning::catalog unavailable — accepting '{canonical}' unchecked")
        else:
            canonical = resolve_in_catalog(raw_model, catalog)
            if canonical is None:
                print(
                    f"::error::model '{raw_model}' is not served by the guardian "
                    f"gateway ({len(catalog)} models). Check "
                    f"GET {args.catalog_url} for the exact id.",
                    file=sys.stderr,
                )
                return 3
            print(f"model '{raw_model}' resolved to gateway id '{canonical}' (actor @{actor})")
    else:
        try:
            allowed = load_allowlist(args.allowlist)
        except Exception as exc:  # noqa: BLE001 - fail closed, never guess
            print(f"::error::cannot read model allowlist: {exc}", file=sys.stderr)
            return 3

        canonical = resolve_model(raw_model, allowed)
        if canonical is None:
            print(
                f"::error::model '{raw_model}' is not allowed for @{actor or 'this actor'} "
                f"(open-weights allowlist: {args.allowlist}). Allowed: {', '.join(allowed)}",
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
