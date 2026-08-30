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

## 2026-08-30 — diff-cap verhoging (512 KB)

- **701 vs 703 symbolen**: lokaal telde ik 700 functies + 1 klasse = 701; de
  mapper meldt 703 (docstring-niveau verschil in tellingsgrens, geen functie).
  Wanneer je mapper-tellingen vergelijkt met bronanalyse, verwacht ±2.
- **Draft → ready_for_review dubbel-trigger**: PR #8 vuurde twee runs af
  (opened + ready_for_review 29 s later); de concurrency-group annuleerde de
  eerste ná een al succesvolle map-job. Klassiek patroon: laat draft-PR's weg
  als je alleen één run wilt, of accepteer de cancel.
- **De review-bot vlagde de branch-pin zelf** ("Temporary Pin", CHANGES_REQUESTED):
  het verdict-beleid werkt zoals ontworpen — mutable pins zijn een terechte
  bevinding. Voor toekomstige caller-pins: verwacht die bevinding en sluit de
  PR na verificatie.
- **Mapper-aanroep in de map-job gebruikt `${MAX_DIFF_BYTES}` als string** in de
  shell — workflow input type `number` stringified netjes; geen quotes-probleem.

## 2026-08-30 — token-verbruik-analyse (gateway capture)

- **Gateway-capture lokatie**: `/home/flip/llama_cpp_guardian/data/capture/guardian_capture_*.jsonl.gz`
  (rotatie per ~1-1,5 h; naam = start-epoch; `current` = actief). Vlak record-schema:
  `prompt_tokens`, `completion_tokens`, `total_tokens`, `reasoning_content`,
  `response_content`, `duration_ms`, `client_ref` (hash van de key), `timestamp_utc`.
  Let op: `completion_tokens` kan als float serialiseren (`131072.0`) — match met
  int(), niet met substring. `guardian_capture_current.jsonl.gz` is vaak truncatief
  (EOFError bij lezen) — per bestand try/except.
- **client_ref-hash van pr-piet**: `07bc50a6220f5714951bbf88f3ef304b7671eaf621f19b00213386af1117bd5b`.
  Capture bevat géén request-payload → PR-identificatie via timing kruisverwijzen.
- **Dashboard-tijden = request-START**: de capture-`timestamp_utc` is het EIND
  (duur erbij optellen). Voorbeeld: gebruikers-zag 18:56 → call eindigde 17:09:13Z
  met duration 735,8 s.
- **Output = reasoning + content**: deepseek-v4-flash-0731 zet vrijwel alles in
  `reasoning_content`. Geval 30-08 17:09Z: 23.331 output = ~23.290 reasoning
  (92.696 chars) + 148 chars echte review-YAML (`key_issues_to_review: []`).
  glm-5.2: 16 calls, 27,5k output totaal, 500 tokens leeg — orde groter efficiënter.
- **Runaway-calls**: 7x op 30-08 output = 131.072 (harde output-cap DeepInfra, 2^17)
  met reasoning_len=0 ÉN content_len=0 → 922.004 output-tokens = **39% van het
  pr-piet-verbruik die dag was weggegooide output**. Duur tot 2383 s — dáár moet
  nog uitgelegd worden waarom ai_timeout=900 en de provider-cap (1200 s) niet
  klapten (vermoeden: duration_ms omvat failover-retries; `attempts`-veld bestaat).
- **Succesvolle calls hadden max ~44,6k output** (09:24Z, content slechts 3.566
  chars) → een max_tokens-cap moet ≥ ~48-65k om geen succesvolle reviews te breken.
- **Input-bereik 30-08**: 17k-67k. Zware full reviews ~60-67k in; 17k past bij
  incrementele push-reviews of kleinere PRs (pr-agent prunt de diff op model-budget).
