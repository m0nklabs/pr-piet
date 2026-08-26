# PR-Piet 🤖 — org-level PR reviewer voor m0nklabs

Automatische PR-review voor m0nklabs-repos: [The-PR-Agent/pr-agent](https://github.com/The-PR-Agent/pr-agent)
met **tree-sitter + Aider-stijl repo-context** (callers/callees, PageRank),
gerund op de org **self-hosted runners** en via de **guardian-llmprovider-gateway**.
Alleen **open-weights modellen**, geen fallback (fout = rode workflow).

```
┌─ doel-repo ───────────────────────────────────────────────────────────┐
│  .github/workflows/pr-piet.yml  (caller: triggers + secrets)          │
│    └─ uses: m0nklabs/pr-piet/.github/workflows/reusable-pr-piet.yml@main
│         ├─ job map          : repo_mapper.py → .pr_piet/context.md    │
│         │                    (géén secrets; tree-sitter + PageRank)    │
│         ├─ job review_tier1 : deepseek-v4-flash-0731 + repo-context   │
│         │                    (artifact_path-injectie), geen fallback  │
│         └─ job review_tier2 : OPTIONEEL: z-ai/glm-5.2 second opinion  │
│                                alleen als tier 1 "geen problemen"     │
└───────────────────────────────────────────────────────────────────────┘
```

## Waarom deze opzet

| Onderdeel | Keuze | Waarom |
|---|---|---|
| Runtime | `the-pr-agent/pr-agent@main` (marketplace action) | Officieel onderhouden; `artifact_path`-input injecteert repo-context natively in de prompts |
| Context | `scripts/repo_mapper.py` (tree-sitter + PageRank) | Aider-stijl: gewijzigde symbolen + callers/callees, gecomprimeerd naar ~4k tokens |
| Security | map-job **zonder secrets**, review-jobs met secrets | PR-code is untrusted; secrets alleen waar pr-agent ze nodig heeft |
| Models | `deepseek/deepseek-v4-flash-0731` (tier 1), `z-ai/glm-5.2` (tier 2) | Beide open-weights, live in de gateway-catalog |
| Fail-safes | geen `fallback_models`; truncate naar diff-only bij overflow | Model-fouten worden zichtbaar, context-overflow degradeert netjes |
| Publicatie | Formele GitHub review via REST API (Copilot-look) | `submit_review.py` zet de pr-agent JSON-output om naar `POST /pulls/{n}/reviews` met `event` (CHANGES_REQUESTED/COMMENT) + inline comment-threads; de "PR Reviewer Guide"-markdown wordt de review-body |

## Gebruik in een doel-repo

1. Zet het org-secret `GUARDIAN_API_KEY` in de doel-repo
   (Settings → Secrets and variables → Actions). De key is de `pr-piet`-key
   van de gateway.
2. Kopieer [examples/caller-pr-piet.yml](examples/caller-pr-piet.yml) naar
   `.github/workflows/pr-piet.yml` in de doel-repo.
3. Klaar. De workflow reageert op:
   - `pull_request` (opened/reopened/synchronize/ready_for_review) →
     `/describe` + `/review` (tier 1) + `/improve`
   - `issue_comment` → alle pr-agent commando's: `/review`, `/describe`,
     `/improve`, `/ask`, ...
   - Tier 2 (second opinion) draait alleen als tier 1
     "No major issues detected" meldt en `enable_tier2: true`.

### Geïnstalleerd (status 2026-08-26)

De caller staat in alle m0nklabs-repos (m.u.v. `pr-piet` zelf — recursion —
en het lege `caretaker-llama-cpp`) én in de publieke non-fork m0nk111-repos:

| Org | Repos |
|---|---|
| `m0nklabs` (18) | agentic-stack-template, cryptotrader, fridge-cam-firmware, github-action-runners, github-copilot-config, github-loop-platform, guardian-llmprovider-gateway, hungryfoodtool, HydroCodo, keanu-factory, kyberm0nk, market-data, monifuse, nervesplat, NewNexus, oelala, oelala-storage, redacted, Reforger-LLM-Squad, wallets-data (+ llama-cpp-guardian al eerder) |
| `m0nk111` (4, publiek) | agent-forge, agent-forge-test, CouncilOfDicks, template-helper |

**Belangrijk voor m0nk111-repos:** die draaien pas als (a) er self-hosted
runners voor die repos beschikbaar zijn en (b) `GUARDIAN_API_KEY` als
**repo-secret** is gezet (het m0nklabs org-secret geldt niet voor een
persoonlijk account). Zonder runners blijven de jobs queued; zonder secret
faalt de workflow bij de eerste PR.

### Inputs (reusable workflow)

| Input | Default | Omschrijving |
|---|---|---|
| `pr_number` | — (verplicht) | PR-nummer (event payload) |
| `base_branch` | repo default | Basis-branch voor de diff |
| `enable_tier2` | `true` | Tier-2 second opinion aan/uit |
| `model_tier1` | `openai/deepseek/deepseek-v4-flash-0731` | Tier-1 model via gateway |
| `model_tier2` | `openai/z-ai/glm-5.2` | Tier-2 model via gateway |
| `gateway_base_url` | `http://172.17.0.1:11434/v1` | OpenAI-compatibele gateway URL (bereikbaar vanuit docker-container via host-bridge) |
| `auto_describe` / `auto_improve` | `true` / `true` | Auto-tools bij PR-opened |
| `response_language` | `en-US` | Taal van pr-agent output |
| `max_context_tokens` | `4096` | Token-budget repo-map |

## Hoe de repo-context werkt

1. **Job `map`** checkt PR-head uit en draait `scripts/repo_mapper.py`:
   - git diff tussen base en head (3-dot, merge-base)
   - tree-sitter symbolen van gewijzigde bestanden (Python, TypeScript/TSX,
     Go, Rust)
   - call-graph (callers/callees) + lichte PageRank (Aider-stijl)
   - output: `.pr_piet/context.md` (max `max_context_tokens`; bij overflow
     truncate, laatste redmiddel: diff-only summary)
2. **De review-jobs** downloaden het artifact en geven het mee als
   `artifact_path` aan de pr-agent action. De action injecteert de context in
   `extra_instructions` van `/review`, `/describe` en `/improve`.
3. **Fail-safes**: oversized diff (>102400 bytes) → diff-only summary;
   geen wijzigingen → lege context (pr-agent draait door); parse-fouten →
   bestand overgeslagen (nooit crash).

## Lokale test (mapper)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# in een willekeurige git-repo met een open PR:
.venv/bin/python scripts/repo_mapper.py \
  --repo /pad/naar/repo --base origin/main --head <sha> \
  --output /tmp/context.md --json
```

## Beveiliging & modelbeleid

- **Alleen open-weights**: `STRICT_OPEN_WEIGHTS_ONLY` (deepseek-v4-flash-0731,
  z-ai/glm-5.2). Voeg geen closed-weights modellen toe.
- **Geen fallback**: `fallback_models = []` — model-fouten zijn zichtbaar.
- **Secrets**: de gateway-key zit alleen in GitHub Secrets; nooit in deze
  repo. De map-job draait zonder secrets op untrusted PR-code.
- **Modelnamen**: altijd verifiëren tegen de gateway-catalog
  (`/v1/models`); de `openai/`-prefix is verplicht zodat litellm de
  OpenAI-compatibele route via `OPENAI.API_BASE` gebruikt.
- **Config-hiërarchie**: onze `config/.pr_agent.toml` wordt als laagste
  prioriteit gemerged (`PR_AGENT_EXTRA_CONFIG_URL`); een repo-local
  `.pr_agent.toml` in het doel-repo kan hem overriden, workflow-env wint
  altijd.

## Componenten

| Pad | Functie |
|---|---|
| `.github/workflows/reusable-pr-piet.yml` | Reusable workflow: map + tier1 + tier2 |
| `scripts/repo_mapper.py` | Tree-sitter + PageRank repo-map (stdlib + tree-sitter) |
| `scripts/detect_review_clean.py` | Tier-2 poortwachter ("No major issues detected"?) |
| `scripts/submit_review.py` | Converter: pr-agent JSON-output → formele GitHub review (REST API, Copilot-look) |
| `config/.pr_agent.toml` | PR-Agent config (models, budget, extra_instructions) |
| `examples/caller-pr-piet.yml` | Voorbeeld-caller voor doel-repos |
| `AGENTS.md` | Canonieke agent-context (lees dit eerst) |

## Formele GitHub review (Copilot-look)

De tier-1 review wordt óók als **formele pull-request review** gepost via de
GitHub reviews REST API, zodat de PR eruitziet zoals een Copilot-review:

- **state-badge**: `CHANGES_REQUESTED` (key issues gevonden) of `COMMENT`
  (schoon) — ingesteld als `event` op `POST /repos/{o}/{r}/pulls/{n}/reviews`
- **inline comment-threads** op de diff (`path`/`line`/`side`/`body` per key
  issue uit de pr-agent JSON-output)
- **code-suggesties met Apply-knop**: de `/improve`-suggesties worden ook
  opgenomen als inline threads, maar dan omgezet naar een
  ` ```suggestion ``` `-fence (de voorgestelde code), zodat GitHub er een
  "Apply"-knop bij toont — precies zoals Copilot-suggesties.
- **review-body**: de "PR Reviewer Guide"-markdown (uit de issue-comment)

De converter combineert `/review` + `/improve` in **één** formele review
(`submit_review.py`):
1. key-issues (uit de review-JSON) → textuele inline threads
2. `/improve`-suggesties (uit de "PR Code Suggestions"-comment) → ` ```diff ``` `
   blokken omgezet naar ` ```suggestion ``` `-fences → inline threads met
   Apply-knop

Flow:
```
pr-agent action (github_action_config.enable_output=true,
                 auto_review=true, auto_improve=true)
  ├─ review-JSON → step-output (GITHUB_OUTPUT)
  └─ /improve-suggesties → "PR Code Suggestions ✨"-issue-comment (```diff```-blokken)
submit_review.py (met GITHUB_TOKEN)
  ├─ body uit "PR Reviewer Guide"-comment
  ├─ key-issues uit review-JSON → comments[]
  └─ suggesties uit /improve-comment → ```diff``` → ```suggestion```-fences → comments[]
     → POST /pulls/{n}/reviews { event, body, comments }
formele review: badge + threads (tekst) + threads (Apply-knop)
```
> Let op: pr-agent plaatst de review **zowel** als issue-comment (de klassieke
> "PR Reviewer Guide"-tab) **als** (via onze converter) als formele review.
> Dit is bewust voor nu: de issue-comment blijft de bron voor de tier-2
> poortwachter (`detect_review_clean.py`).

Geverifieerd (2026-08-26, guardian-llmprovider-gateway):
- PR #4 (met bugs) → formele review `CHANGES_REQUESTED` + 2 inline threads
  op de diff (timing-leak, missing guard) + volle body.
- PR #5 (schoon) → formele review `COMMENTED` + body, 0 inline threads,
  tier 2 draaide.
- PR #6 (bug + /improve) → formele review + key-issues threads **plus**
  suggestie-threads met ` ```suggestion ``` `-fence (Apply-knop).
> De GitHub REST API noemt de `COMMENT`-event-state `COMMENTED` in responses.

## Modelruimte & timeouts

Op omvangrijke PR's (tientallen bestanden, ticket-compliance met de
masterplan-body, repo-map met duizenden symbolen) kan een enkele modelcall
langer dan 5 min duren. Als `ai_timeout` te krap staat, kapt pr-agent het
model af terwijl het nog bezig is → `finish_reason: length` + lege content en
géén review (of een stale fallback-body).

- `config.ai_timeout` = **900** (15 min per call), in `config/.pr_agent.toml`
  én de workflow-env (`config.ai_timeout` in `reusable-pr-piet.yml`, tier 1 en
  tier 2 — de env overschrijft de toml, dus beide consistent houden).
- Grens van de gateway: deepseek-provider `timeout_seconds: 1200` (20 min)
  in `providers.settings.yaml` — 900 past er ruim binnen. Verhoog `ai_timeout`
  nooit boven de provider-cap, anders kapt de gateway eerst af.
- `max_model_tokens` (output-cap) op 64000 laten staan; verlagen kneep het
  model juist (ramde tegen de cap aan op grote PRs).
