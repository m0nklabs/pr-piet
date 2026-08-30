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

## 2026-08-30 (avond) — modelvergelijkingssporen + capture-bugs

- **131k-runaways opgelost (dubbele oorzaak):** (1) pr-agent stuurt geen
  max_tokens → DeepInfra service-default 131.072 gold (fork-knop
  `config.max_output_tokens` bestaat, default 0 = niet verzonden;
  litellm_ai_handler.py:720-725, 864-867); (2) deepseek's reasoning-behoefte
  is sterk run-variabel (5,3k tot ≥16k op identieke payload) — bij 4k budget
  praktisch altijd length/null, bij 16k nog 1/2 runs length.
- **glm-5.3-flash is géén runaway-case** (eerdere 4k-test was een
  budget-artefact): stopt natuurlijk bij ~9k totaal (8,5k reasoning,
  2,6k chars content, stop). Mijn eerste framing "zelfde probleem als
  deepseek" was te sterk — gecorrigeerd.
- **reasoning-cap `{"reasoning":{"max_tokens":N}}`:** gateway filtert hem
  niet weg (routing.py prepare_cloud_candidate_request 1-op-1 doorgifte);
  effectief bij glm-5.3-flash via Z.AI (reasoning 8460 → 111/267, 7×
  sneller, méér content), WERKLOOS bij deepseek via DeepInfra (genegeerd:
  15.999 reasoning, length, null). N.b.: de fork verstuurt reasoning-params
  alleen bij `openrouter/`-modelnamen — `openai/...` gaat zonder; cap-injectie
  moet dus gateway-side (of fork-patch).
- **E2E tier-1 kandidaten met 3 geplande bugs (pr-piet-test #9/#10):**
  glm-5.3-flash 3/3 + formele review (CHANGES_REQUESTED, 73 s);
  glm-5.2 3/3 inhoudelijk maar 0/2 formele reviews — suggested_fix brak de
  YAML-parse (block-scalar-indentatie) in submit_review.py. dm-rate:
  glm-5.2 reasoning 47-63% van out vs glm-5.3-flash 81% (en deepseek 88-96%
  in productie).
- **Nieuwe stack-bug ontdekt (submit_review.py):** parse-faal bij een
  éérste review (geen vorige review op de PR) exit 0 → groene run met
  háár geen review geplaatst; de rc-3 `::error::` fail-safe triggert alleen
  bij stale-repost. Schendt harde regel 2. Fix-kandidaat samen met de
  YAML-robustheid.
- **Capture-bug G1 bevestigd met repro + root cause:** non-stream extractor
  (capture_dispatch.py:264-357) leest nooit message.reasoning(_content) en
  pakt finish_reason van het verkeerde niveau (message i.p.v. choice);
  ds-cap-probe: raw 67.971 chars reasoning → capture 0/0 (usage wél juist).
  Calls mét content tonen tot 141k chars reasoning — geen size-limiet, wel
  een pad/veld-bug. finish_reason ontbreekt óók in het record-schema (C4).
- **Capture-scan-pitfalls (nog steeds geldig):** floats in completion_tokens
  (16.000,0), EOFError op _current, glob-epoch-prefix filtering; nonce in
  user-veld nodig — de response-cache zit in OpenRouter zelf
  (X-OpenRouter-Cache; key = SHA-256 body+user, providers.py:627-631).
- **G2 (39,7-min call) versmald:** cancellation-machinerie bestaat voor beide
  paden (queue_helpers.py:88 watcher + _await_or_cancel_request
  routing.py:940-946) en de service (PID 1239, gestart 29-08) had die code al;
  geen disconnect-logregel → ofwel is_disconnected() vuret niet voor
  non-streamed requests in de docker→nginx-keten, ofwel brak de client nooit
  af. Repro-plan in het bugreport-JSON.

- **Naleving (E2E-detail):** beide kandidaat-modellen negeerden de
  [verified-bug]/[hypothetical]-classificatie-instructie uit
  extra_instructions (bevindingen kwamen ongetagd binnen; de conservatieve
  verdict-default greep in). Prompt-naleving is dus geen modelgarantie — het
  graderings-beleid (verdict_from_review) is de echte rem.
- **Provider-routing is variabel:** glm-5.2 kwam in de E2E via Novita, in
  productie via Baidu; glm-5.3-flash consistent via Z.AI. Vergelijkingen
  tussen modellen zijn dus óók provider-serving-vergelijkingen (denk- aan
  per-provider caps en reasoning-param-ondersteuning).

- **Exacte token-splitsing (addendum, vervangt char-schatting):** de fork-logs
  bevatten `completion_tokens_details.reasoning_tokens` — de splitsing is nu
  exact, geen schatting. glm-5.3-flash review-call: 3852 out = 3129 reasoning
  (81,2%) + 723 content; glm-5.2: 1241/1681 out = 581/1059 reasoning (47-63%)
  + 660/622 content. Content-volume gelijk (~620-723 tok); glm-5.3-flash
  betaalt 2,8-5× meer reasoning ervoor — en levert als enige valide
  review-YAML (glm-5.2 0/2 formele reviews). Nugebruik voor de capture:
  het veld zit in de response-usage en kan gespiegeld worden (C5).

- **`config.custom_model_max_tokens` is load-bearing (subagent C-detail):**
  `get_max_tokens()` (algo/utils.py:1147-1170) kent de exacte modelnaam
  `openai/deepseek/deepseek-v4-flash-0731` (en evenmin
  `openai/z-ai/glm-5.3-flash`) niet in MAX_TOKENS → zonder de env
  `custom_model_max_tokens` CRASHT de review op utils.py:1165
  (Exception). De env-waarde zelf doet alleen input-clipping
  (min met max_model_tokens=64000); nooit verwijderen bij een modelwissel.
- **"Empty content"-faalmode eindigt ROOD (code-bevestiging):**
  litellm_ai_handler.py:970-979 raise APIError bij lege content met
  onderscheidende warning (finish_reason erin) → @retry
  MODEL_RETRIES=2 = precies 1 retry (regel 561-565) → reraise →
  submit_review.py exit 3 → rode workflow. De fail-safe is dus ook voor de
  nieuwe tier-1-code gesloten; let op de asymmetrie: in het STREAMING-pad
  zou hetzelfde geval alleen op DEBUG loggen (litellm_helpers.py:76-78) —
  niet onze route.
- **Onze route is non-streaming (bevestigt G1-pad):**
  STREAMING_REQUIRED_MODELS=["openai/qwq-plus"] + force_streaming default ""
  → non-streaming acompletion voor openai/-modellen; de body-loze
  capture-records kwamen dus inderdaad door de non-stream extractor
  (capture_dispatch.py) — consistent met de G1-root-cause.

- **Incident 2026-08-30 20:18-20:27 (caretaker PR #8): rode runs op gewone
  comments.** Oorzaak: élke issue-comment triggert PR-Piet; m0nk111 postte
  status-comments ("Slot — review-cyclus afgesloten", met `**Head:**`
  -markdown) en pr-agent parseerde daar "**head:" als commando
  ("Unknown command") → geen modelcall → submit exit 3 → rood. NIET het
  nieuwe model: de 20:56-`/review`-run op dezelfde PR slaagde normaliter.
  Fix: commando-guard in map + review_tier1 — alleen comments met een
  commando op een eigen regel (`/review`, `/improve`, ...) doorlopen;
  andere comments skippend (geen rode run; harde regel 2 blijft intact,
  de guard draait vóór er een modelcall is). Getest tegen de exacte
  comment-bodies van vandaag (10/10 lokalen) + live sandbox-verificatie.
  Voorwaarde aan gebruikers: commando op een eigen regel; commando mid-regel
  wordt bewust genegeerd.

- **Live E2E-bewijs guard (subagent, pr-piet-test PR #11, 2026-08-30
  22:40-22:50Z):** ① pull_request-run 33339768220 success — volledige
  pipeline (map + tier1 glm-5.3-flash + tier2), guard proceed=true;
  ② comment zonder commando → run 33339959995 success met álle functionele
  steps skipped + guard-notice in beide jobs; ③ "/review" → run
  33340085748 success, volledige review geplaatst. PR gesloten (niet
  gemerged), branch verwijderd, caller gerevert naar @main. Methodische
  nuance: issue_comment-workflows draaien ALTIJD vanaf de default branch —
  caller-pins voor comment-tests moeten daarom tijdelijk op main van de
  doel-repo (gevestigd pin/revert-patroon).
