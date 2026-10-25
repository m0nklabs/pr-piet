#!/usr/bin/env python3
"""Tests voor parse_review_command.py (de /review=<model>-vorm)."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "parse_review_command.py"
ALLOWLIST = REPO / "config" / "models.allowed.yaml"
DEFAULT_T1 = "openai/z-ai/glm-5.3-flash"
DEFAULT_T2 = "openai/z-ai/glm-5.3"


def run(body, *extra, default=DEFAULT_T1):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--comment-body",
            body,
            "--allowlist",
            str(ALLOWLIST),
            "--default-model",
            default,
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    outputs = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return result.returncode, outputs, result.stderr


def test_plain_review_uses_default():
    rc, out, _ = run("/review")
    assert rc == 0
    assert out["proceed"] == "true"
    assert out["command"] == "review"
    assert out["model"] == DEFAULT_T1
    assert out["model_label"] == "z-ai/glm-5.3-flash"
    assert out["override"] == "false"


def test_review_with_bare_model():
    rc, out, _ = run("/review=z-ai/glm-5.3")
    assert rc == 0
    assert out["model"] == "openai/z-ai/glm-5.3"
    assert out["model_label"] == "z-ai/glm-5.3"
    assert out["override"] == "true"


def test_review_accepts_litellm_prefix():
    rc, out, _ = run("/review=openai/z-ai/glm-5.3")
    assert rc == 0
    assert out["model"] == "openai/z-ai/glm-5.3"


def test_review_accepts_catalog_route_prefix():
    # De gateway-catalogus spelt dit als openrouter/deepseek/...
    rc, out, _ = run("/review=openrouter/deepseek/deepseek-v4.1-flash")
    assert rc == 0
    assert out["model"] == "openai/deepseek/deepseek-v4.1-flash"


def test_review2_with_model():
    rc, out, _ = run("/review2=moonshotai/kimi-k3", default=DEFAULT_T2)
    assert rc == 0
    assert out["command"] == "review2"
    assert out["model"] == "openai/moonshotai/kimi-k3"


def test_unknown_model_is_rejected():
    rc, out, err = run("/review=does-not/exist")
    assert rc == 3
    assert "not allowed" in err


def test_closed_weights_model_is_rejected():
    # Harde regel 1: github-copilot/* is closed-weights en staat niet in de lijst.
    rc, _, err = run("/review=github-copilot/openai/gpt-6.1-sol")
    assert rc == 3
    assert "not allowed" in err


def test_passthrough_command_ignores_default():
    rc, out, _ = run("/describe")
    assert rc == 0
    assert out["command"] == "describe"
    assert out["proceed"] == "true"
    assert out["override"] == "false"
    assert out["model"] == DEFAULT_T1


def test_override_rejected_for_non_review_command():
    rc, _, err = run("/describe=z-ai/glm-5.3")
    assert rc == 3
    assert "only supported for /review" in err


def test_expect_isolates_tiers():
    # tier 1 (--expect review) mag een /review2-override niet overnemen.
    rc, out, _ = run("/review2=z-ai/glm-5.3", "--expect", "review")
    assert rc == 0
    assert out["override"] == "false"
    assert out["model"] == DEFAULT_T1
    assert out["command"] == "none"


def test_expect_review2_matches():
    rc, out, _ = run("/review2=z-ai/glm-5.3", "--expect", "review2", default=DEFAULT_T2)
    assert rc == 0
    assert out["override"] == "true"
    assert out["model"] == "openai/z-ai/glm-5.3"


def test_no_command_skips():
    rc, out, _ = run("Just a status update, no command here.")
    assert rc == 0
    assert out["proceed"] == "false"
    assert out["command"] == "none"


def test_command_with_leading_whitespace_and_args():
    rc, out, _ = run("   /review=deepseek/deepseek-v4.1-flash-1017  ")
    assert rc == 0
    assert out["model"] == "openai/deepseek/deepseek-v4.1-flash-1017"


def test_allowlist_entries_are_bare_ids():
    import yaml

    data = yaml.safe_load(ALLOWLIST.read_text())
    models = data["models"]
    assert models, "allowlist is leeg"
    for model in models:
        assert not model.startswith("openai/"), model
        assert not model.startswith("openrouter/"), model
