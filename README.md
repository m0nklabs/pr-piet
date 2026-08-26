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
| `config/.pr_agent.toml` | PR-Agent config (models, budget, extra_instructions) |
| `examples/caller-pr-piet.yml` | Voorbeeld-caller voor doel-repos |
| `AGENTS.md` | Canonieke agent-context (lees dit eerst) |
