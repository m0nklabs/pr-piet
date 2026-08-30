# AGENT_JOURNAL — append-only findings log

> Facts that had to be dug up, live-test results, lessons. Append-only; cold file
> (not loaded into the prompt). Promote stable rules to AGENTS.md only in batched
> passes.

## 2026-08-30 — mapper production test + open points

- **Gateway keys file** `/home/flip/llama_cpp_guardian/config/guardian.keys.yaml`:
  top-level key IS the bearer token (e.g. `oelala_<hash>`); client identity lives in
  `metadata.client` OR only in `name` — the pr-piet key has `name: pr-piet` and no
  `client` field. Match on `name` when extracting programmatically; never echo the key.
- **Gateway ports**: 11434 (0.0.0.0) and 11436 (127.0.0.1 host-loopback). From the
  host, use `http://127.0.0.1:11436/v1`; from containers, `http://172.17.0.1:11434/v1`.
- **Catalog** (2026-08-30, `/v1/models`): both tier models present as
  `openrouter/deepseek/deepseek-v4-flash-0731` and `openrouter/z-ai/glm-5.2`.
  Direct gateway calls accept the bare route names (`deepseek/deepseek-v4-flash-0731`,
  `z-ai/glm-5.2`); the `openai/`-prefixed variants are rejected as "invalid model ID"
  by the gateway itself (the prefix is a litellm client-side routing directive and is
  stripped before the request leaves litellm).
- **deepseek-v4-flash-0731 is a heavy reasoning model**: with `max_tokens=500` the
  entire budget went to the reasoning field (`reasoning_tokens=3999` at 4000) and
  `content=null`, `finish_reason=length`. Any caller asking for short reviews from it
  must budget for reasoning tokens. This reproduces the known pr-agent "Empty content
  (finish_reason: length)" failure mode outside CI.
- **glm-5.2 also reasons but stops in time**: 1229 completion tokens (922 reasoning),
  `finish_reason=stop`, real review finding produced.
- **Gateway response cache exists**: an identical chat payload repeated 40 s later
  returned in 0.3 s with a FRESH response id, zero usage, identical content — i.e.
  litellm/gateway-level response cache, not a client cache. Benchmark repeats must
  vary the payload. Production impact ≈ nil (payloads differ per PR/diff).
- **Mapper truncation bug (fixed in a1633f8)**: exact numbers from spy instrumentation
  on PR #12 data — keep-loop reached `used(200) + 3891`; marker pushed part to 3902;
  outer check `200 + 3902 > 4096` → `break` dropped the 31 415-token symbols section.
  Root cause: the keep-loop condition ignored the marker bytes and used a different
  join expression than the final part, so a truncated section could miss budget by
  1–12 tokens. Fix: +12-token marker reserve inside the keep-loop.
- **Diagnosis method that worked**: (1) reproduce in isolation by importing the module
  and calling `build_context` directly; (2) monkeypatch `approx_tokens` with a stack-
  reporting spy to see the exact failing comparison; (3) `git stash` apples-to-apples
  old-vs-new on the same fixture to attribute the regression. Static reading alone
  failed three times before the spy pinpointed it.
- **`approx_tokens(list)` log bug**: `approx_tokens` uses `len(text)//4`; passing the
  `summary` LIST gave `len(list)//4` = 4 (items, not chars). Type-checking hint: the
  function signature says `text: str` but nothing enforces it.
- **Production evidence cross-check habit**: subagent reported first bot review on
  guardian #8 at 20:02:22; direct API read showed `submitted_at 20:02:26` (report
  likely used the comment timestamp vs review object). Immaterial to the conclusion
  (~11m47s after opened), but when copying timestamps into docs, re-read the primary
  object.
- **pr_piet_ref defaults to `main`** in the reusable workflow inputs → mapper/config
  fixes pushed to main are live for all 49 callers on their next run, no caller update
  needed.
