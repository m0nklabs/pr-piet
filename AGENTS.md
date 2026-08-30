# AGENTS.md — instructies voor AI-agents in deze repo (m0nklabs)

Dit bestand is de canonieke context voor agents die aan PR-Piet werken.
Lees het VOORDAT je iets wijzigt.

## Wat dit is

PR-Piet is de **org-level PR-reviewer** van m0nklabs: een reusable GitHub
Actions workflow die [The-PR-Agent/pr-agent](https://github.com/The-PR-Agent/pr-agent)
draait op de org self-hosted runners (`ai-kvm2`), met extra repo-context die
een tree-sitter-mapper (Aider-stijl) per PR bouwt. Alle modelcalls gaan via
de **guardian-llmprovider-gateway** op dezelfde host.

Andere m0nklabs-repos gebruiken PR-Piet via:
`uses: m0nklabs/pr-piet/.github/workflows/reusable-pr-piet.yml@main`
(zie `examples/caller-pr-piet.yml`).

## Harde regels (nooit overtreden)

0. **Delegeer wachten & herbewerking aan subagents.** Lange GitHub Actions
   runs, poll-loops (`sleep`/job-wachten), status-controles en logs-grep zijn
   subagent-werk (`subagent` met `run_in_background: true`), NIET werk voor
   de hoofd-agent. De hoofd-agent start delegaties parallel en doet andere
   productieve stappen; poll niet in de hoofd-context. (Zie ook de
   dsh-richtlijn over achtergrond-jobs: nooit busy-pollen in de hoofdlus.)
1. **Alleen open-weights modellen.** `STRICT_OPEN_WEIGHTS_ONLY`:
   `deepseek/deepseek-v4-flash-0731` (tier 1) en `z-ai/glm-5.2` (tier 2,
   optionele second opinion). Nooit closed-weights modellen toevoegen.
2. **GEEN fallback_models.** Een model- of gateway-fout moet een rode
   workflow zijn, geen stille degradatie. `fallback_models = []` blijft leeg.
3. **Geen secrets in deze repo.** De gateway-key (`GUARDIAN_API_KEY`) is een
   org-secret en staat alleen in GitHub Secrets / lokale env — nooit in
   config, workflows of README. De `.env`-bestanden zijn gitignored.
4. **De map-job heeft géén secrets.** Job `map` draait de tree-sitter mapper
   op PR-code (untrusted) met alleen `contents: read`. Secrets zitten alleen
   in `review_tier1`/`review_tier2`. Nooit secrets naar de map-job brengen.
5. **Modelnamen komen uit de gateway-catalog, niet uit aannames.** Controleer
   altijd `config/models.cloud.overrides.yaml` en `/v1/models` van de gateway
   (Bearer-key) voordat je een model toevoegt. De `openai/`-prefix in model-
   namen is verplicht: die dwingt litellm naar de OpenAI-compatibele route
   (`OPENAI.API_BASE`) via de gateway.
6. **Repo-context via artifact_path, niet via extra_instructions.** De action
   injecteert `.pr_piet/context.md` natively via `artifact_path`. Gebruik geen
   `file://`-constructies — die bestaan niet in pr-agent.
7. **Deze repo moet publiek blijven** — anders werken cross-repo `uses:`
   vanuit andere m0nklabs-repos niet.
8. **NOOIT auto-merge of auto-approve.** Altijd human merge. PR-Agent kan via
   zijn interne `auto_approve()` (event=`APPROVE`) en `commitable_code_suggestions`
   een PR laten approven/mergen; dat moet altijd UIT blijven. Houd
   `.pr_agent.toml` vrij van `enable_auto_approve`/`auto_approve` en
   `commitable_code_suggestions = false`. De workflow mag nooit een review met
   event `APPROVE` posten en nooit een merge-API-aanroep doen. Merge gebeurt
   uitsluitend handmatig (human) op GitHub.
9. **PR-Piet maakt geen PR's in project-/doel-repos en zet daar nooit commits
   in. PR-Piet gaat over de setup en het onderhoud van de PR-review-stack
   zelf.** Het mandaat is beperkt tot de review-infra: de reusable workflow,
   de `submit_review.py`-converter, de config en de documentatie van deze
   stack (wijzigen van bestanden in dit repo = stack-onderhoud, géén
   feature-werk). In doel-repos is PR-Piet uitsluitend reviewer — comments,
   formele reviews, suggesties; hij opent daar nooit een PR en zet er nooit
   een commit in.
   **Uitzondering — test-PR's:** PR-Piet MAG test-PR's maken óm de
   review-stack zelf te verifiëren (E2E: review loopt automatisch, een
   bewuste fout genereert een suggestie, enz.). Die test-PR's zijn strikt
   alleen voor testen: maak ze in een toegewijde test-/sandbox-repo (bv.
   `m0nklabs/pr-piet-test`), NOOIT in productie-/doel-repos en nooit
   gerelateerd aan het echte project. In test-PR's mag PR-Piet **wél
   committen** (dat is testmateriaal, geen bijdrage aan een project). De
   test-PR-inhoud is dummy/triviaal en expliciet als test gemarkeerd; zodra de
   verificatie klaar is, wordt de test-PR gesloten (niet gemerged).
   Suggesties blijven suggesties (` ```suggestion ``` `-fence + Apply-knop
   voor de mens); de reviewer past zelf nooit code toe in project-repos.

## Architectuur

```
doel-repo/.github/workflows/pr-piet.yml (caller: triggers + secrets)
  └─ m0nklabs/pr-piet/.github/workflows/reusable-pr-piet.yml
       ├─ job map          : checkout PR-head, scripts/repo_mapper.py
       │                    (tree-sitter symbols + PageRank) -> .pr_piet/context.md
       │                    → upload-artifact (geen secrets in deze job)
       ├─ job review_tier1 : m0nklabs/pr-agent@main (ONZE FORK van
       │                    The-PR-Agent/pr-agent; patch: conditional
       │                    suggested_fix veld, zie PR-PIET-PATCH.md daar)
       │                    met artifact_path=.pr_piet/context.md,
       │                    model=openai/deepseek/deepseek-v4-flash-0731,
       │                    fallback_models=[], OPENAI.API_BASE=gateway
       │                    → detect_review_clean.py (tier1_clean output)
       └─ job review_tier2 : alleen als enable_tier2 && tier1_clean
                             model=openai/z-ai/glm-5.2, zelfde artifact
```

Config-hiërarchie voor pr-agent (laag → hoog):
`built-in defaults` → `PR_AGENT_EXTRA_CONFIG_URL` (= onze
`config/.pr_agent.toml`) → repo-local `.pr_agent.toml` (doel-repo, indien
aanwezig) → workflow-env (`config.model`, `OPENAI.KEY`, ...).

## Belangrijke feiten (geverifieerd, niet opnieuw uitzoeken)

- **The-PR-Agent/pr-agent is een andere repo dan qodo-ai/pr-agent.** Deze
  stack gebruikt The-PR-Agent (open-source, actief; de repo van de officiële
  marketplace action `the-pr-agent/pr-agent@main`).
- **De action leest `OPENAI_KEY`** (runner: `github_action_runner.py`), niet
  `OPENAI__KEY`. De api_base gaat via `OPENAI.API_BASE` env.
- **Vanuit docker-containers (pr-agent action) is de gateway bereikbaar op
  `http://172.17.0.1:11434/v1`** (docker bridge → host nginx plain-HTTP,
  geverifieerd 2026-08-26 met HTTP 200). `127.0.0.1:11436` is host-loopback
  only en werkt niet vanuit een container.
- **`extra_instructions` is een inline string** — geen file://-mechanisme.
  De `artifact_path`-input van de action injecteert een bestand in
  `extra_instructions` van pr_reviewer/pr_description/pr_code_suggestions.
- **Tier-2 poortwachter-marker:** pr-agent schrijft exact
  `No major issues detected` in de review-comment als er geen key issues
  zijn (`scripts/detect_review_clean.py` matcht case-insensitief).
- **Review-comment heading is hardcoded** in de nieuwe "Reviewer Guide"-stijl:
  `## PR Reviewer Guide 🔍` (pr_agent/algo/utils.py `PRReviewHeader`). De
  `pr_reviewer.review_heading`-setting werkt alleen in de klassieke stijl;
  de tier-detectie matcht daarom op de vaste header "PR Reviewer Guide".
- **Formele GitHub review (Copilot-look):** `scripts/submit_review.py` zet de
  pr-agent review-JSON om naar `POST /pulls/{n}/reviews` met `event` +
  `comments[]` (inline threads). De review-JSON komt **wel** door via
  `github_action_config.enable_output=true` (GITHUB_OUTPUT werkt ook vanuit
  de docker action — geverifieerd 2026-08-26 op PR #4
  guardian-llmprovider-gateway: `CHANGES_REQUESTED` + body + 2 inline
  threads). De markdown-body wordt altijd uit de zojuist geplaatste
  issue-comment gehaald (pr-agent post die sowieso).
  **Combined-flow (PR #6, 2026-08-26):** `/review` + `/improve` worden
  gecombineerd in één formele review. De `/improve`-suggesties (uit de
  "PR Code Suggestions ✨"-issue-comment) worden als ` ```diff ``` `-blokken
  gepost; `submit_review.py` zet die om naar ` ```suggestion ``` `-fences
  (de voorgestelde code) zodat GitHub een "Apply"-knop toont, zoals
  Copilot-suggesties. Belangrijk: bij `commitable_code_suggestions=false`
  (onze harde regel 8) post pr-agent de suggesties ALLEEN als issue-comment,
  niet inline — onze converter hergebruikt die comment en plaatst ze inline
  mét Apply-knop via de reviews REST API.
- **Inline threads vereisen een toegevoegde diff-regel (GitHub 422).** De
  reviews REST API kan alleen comments plaatsen op `line` (side RIGHT) die in
  de PR-diff staat als toegevoegde regel (`+`). Een key-issue op een
  pure-deletie/context-regel (bv. `scripts/_paths.py` regel 1 in PR #2, diff
  was `@@ -6,4 +6,3 @@` pure deletie) faalt met 422 "Line could not be
  resolved", waardoor de héle inline-set werd gedropt. `submit_review.py`
  bouwt daarom via `GET /pulls/{n}/files`+patch-hunks de set toegevoegde
  regels per bestand (`get_added_lines_per_file`) en filtert niet-resolvable
  threads (skip) — de samenvatting blijft altijd als formele review staan.
  Dit is inherent aan de GitHub API, niet te omzeilen met lijn-nummering.
- **Dedup blokkeert alleen fallback-reviews (geen verse JSON).** De
  review-dedup (marker `<!-- pr-piet-review:v1 head=<sha> -->` of zelfde
  commit_id + Reviewer Guide-heading) geldt ALLEEN als de review-JSON van de
  huidige run NIET is doorgekomen (`review_data is None`, dus we zouden een
  fallback-body van een oude issue-comment posten). Komt de verse review-JSON
  wél door, dan posten we altijd — een échte nieuwe analyse moet een evt.
  stale review op dezelfde head vervangen (GitHub toont de laatste als de
  actieve review). Dedup is bedoeld om identieke fallback-hertriggers te
  stoppen, niet om een verse analyse te blokkeren.
- **Modelruimte: `ai_timeout = 900` (15 min/call).** Op omvangrijke PR's
  (tientallen bestanden + ticket-compliance-body + repo-map met duizenden
  symbolen) duurt een enkele modelcall >5 min; met `ai_timeout=300` kapte
  pr-agent het model af (finish_reason length + lege content) → geen review
  of stale fallback. 900s past ruim binnen de gateway deepseek-provider-cap
  (1200s in `providers.settings.yaml`). Deze timeout staat in
  `config/.pr_agent.toml` én de workflow-env (`config.ai_timeout`, tier 1 én
  2 — de env overschrijft de toml, dus beide consistent houden). Verhoog
  nooit boven de provider-cap.
- **Single-call mode (via pr-agent-fork): 1 modelcall i.p.v. 2 voor review+suggesties.**
  Standaard draait de combined-flow `/review` + `/improve` = **2** modelcalls per PR.
  Met de input `single_call_review: true` (default `false`) draait er **1** call:
  de action gebruikt onze fork **`m0nklabs/pr-agent`** (= upstream `The-PR-Agent/pr-agent`
  + patch, zie `PR-PIET-PATCH.md` in die repo) die met
  `pr_reviewer.require_suggested_fix=true` het model per key-issue om de **exacte
  vervangingscode** vraagt (`suggested_fix`, voor `start_line..end_line`). Dat veld
  stroomt via `github_action_config.enable_output` in de review-JSON, en
  `submit_review.py` wikkelt het in een ` ```suggestion ``` `-fence (Apply-knop) in de
  formele review; multi-line fixes worden multi-line comments (`start_line`+`line`).
  Eerste poging (losse ````diff```-blokken in de review-body vragen via context.md)
  faalde: het model volgt het vaste reviewer-schema, geen diff-blokken — vandaar de
  fork. **Terugdraaien (2 stappen, onafhankelijk):** (1) zet `single_call_review` weer
  op `false` (caller-input) → 2-call gedrag; (2) wil je helemaal van de fork af: zet
  `uses:` in de workflow terug naar `the-pr-agent/pr-agent@main`. Default blijft `false`.
- **Copilot-stijl: onzekere bevinding → wél een best-effort `suggested_fix` (fork
  `1181155`/`3682dc6`, 2026-08-28).** Aanleiding: PR #8 (guardian-llmprovider-gateway)
  leverde een key-issue met een **lege** `suggested_fix` (géén Apply-knop) omdat de
  prompt het model toestond "empty string" te gebruiken bij onzekerheid. De fork-prompt
  is nu zo bijgesteld dat het model ALTIJD een concrete best-effort fix geeft, óók voor
  een mogelijke/onzekere bevinding, én de bevinding markeert met `UNCERTAIN:`/`not
  verified` in `issue_content` (Copilot-gedrag). E2E-bewezen op `m0nklabs/pr-piet-test`:
  een bevinding krijgt nu wél een gevulde `suggested_fix` → inline ` ```suggestion ``` `-fence
  mét Apply-knop. **Bekende beperking (geverifieerd):** deepseek-v4-flash-0731 zet de
  `UNCERTAIN:`-markering niet consequent in de output — de twijfel staat intern in de
  reasoning, maar drukt zelden door naar `issue_content`. De best-effort-fix-kant werkt;
  de onzekerheids-vlag niet betrouwbaar (herschrijf niet: dit is modelgedrag, geen
  fork-prompt-bug).
- **VALKUIL pr-agent-fork: upstream action.yaml bouwt NIET from source.** De upstream
  `action.yaml` verwijst naar `Dockerfile.github_action_dockerhub`, en dat bestand is
  alleen `FROM pragent/pr-agent:github_action` — een **prebuilt upstream-image van
  Docker Hub**. Broncode-wijzigingen in een fork komen dan NOOIT in de container: de
  env-var (bv. `pr_reviewer.require_suggested_fix`) landt wél in dynaconf-settings
  (zichtbaar in de "Relevant configs"-debug-dump), maar het draaiende pr_agent-pakket
  en de prompts zijn upstream — het veld staat niet in de gerenderde prompt en het
  model ziet het nooit. Onze fork zet daarom `action.yaml → image:
  'Dockerfile.github_action'` (from-source, `ADD pr_agent pr_agent`). Diagnose-tip:
  grep de job-log op `class KeyIssuesComponentLink` — als `suggested_fix` ontbreekt in
  de gerenderde prompt, draait er upstream-code in de container.
- **Synchronize-events (push op open PR) zijn géén auto-actions.** pr-agent's
  `pr_actions` = `[opened, reopened, ready_for_review, review_requested]`; een
  `synchronize`-event zonder meer wordt geskipt met "Skipping action: synchronize"
  (geen modelcall), waarna de submit-step een **fallback-body van de vorige review
  herpost op de nieuwe head** (stale inhoud, misleidend). Fix in de workflow (beide
  tiers): `github_action_config.handle_push_trigger: "true"` +
  `github_action_config.push_commands` (tier 1: `["/review"]` in single-call, anders
  `["/review", "/improve"]`; tier 2: `["/review"]`). Push-door-Bot en merge-commits
  worden door pr-agent zelf genegeerd (default `ignore_*`-settings).
- **FAAL-SAFE tegen "oude reviews herhalen" (2026-08-29, caretaker PR #5).** Als
  de model-call niets oplevert (pr-agent logt bv. `Empty content in model response
  (finish_reason: length)` en post geen verse review-comment), dan is er géén verse
  review-JSON én géén verse (>= since) review-guide-comment én wél een oudere
  review. `submit_review.py` herpost die stale body dan NIET als verse review op de
  nieuwe head (dat was "oude reviews herhalen": de herhaalde reviews op caretaker
  PR #5 waren byte-identiek aan de vorige zodat de "verbeterde code" ontbrak).
  In plaats daarvan `return 3` → de workflow wordt ROOD (harde regel 2: model-fout
  = rode workflow, geen stille degradatie). Implementatie: `had_fresh_body` /
  `had_stale_comment` flags; de stale-fallback-body wordt alleen gebruikt als er
  WÉL een verse review-JSON is (dan is de JSON de bron van de inline-comments en
  is zo'n fallback zinnig). Overige exit-codes (1/2: GitHub API-fout op inline
  comments) blijven niet-fataal zoals voorheen.
- **Review-proces-beleid (2026-08-30, advies naar aanleiding van
  guardian-llmprovider-gateway PR #12: 34 bot-reviews / 100+ threads in 13 uur,
  herhaaldelijk weerlegde bevindingen, merge-state UNSTABLE).** Vier veranderingen:
  1. **Incrementele push-review**: `push_commands` = `["/review -i"]` (single-call)
     — een push reviewt alleen de commits sinds de laatste gereviewde head.
     Bewezen in de fork-code: `PRReviewer.parse_incremental` pakt `args[0]=="-i"`;
     `_can_run_incremental_review` valt terug naar FULL review als er geen
     review-marker is (`commits_range None → is_incremental=False`); de
     threshold-defaults zijn 0/0 en blokkeren nooit; `-i` passeert
     `CliArgs.validate_user_args` (die checkt alleen `--`-args). Full review
     blijft: auto-review op opened + menselijke `/review`. Tier 2 blijft bewust
     FULL (een second opinion moet de hele PR zien).
  2. **Verdict-gradering** (`submit_review.py verdict_from_review`):
     REQUEST_CHANGES alleen bij blokkerende bevindingen ([verified-bug] of
     ongetagd — conservatief default, zodat prompt-niet-naleving nooit milder
     is dan voorheen); alle [hypothetical]/[backlog]/"UNCERTAIN:"-content →
     COMMENT. NOOIT APPROVE (harde regel 8) — "geen nieuwe bevindingen" is
     altijd COMMENT. E2E 2026-08-30 (pr-piet-test PR #7): deepseek gaf in
     ronde 1 een ongetagde bevinding (`Possible Bug`) — gradering greep
     correct in via de conservatieve default (CHANGES_REQUESTED); ronde 2
     leverde een echte incrementele review (`## Incremental PR Reviewer
     Guide`, identity `<!-- pr-agent:review:incremental -->`) met verse
     analyse op de nieuwe head. Let op: de incrementele body-header luidt
     "Review for commits since previous PR-Agent review starting from
     commit …", niet "Review updated until commit".
  3. **Prompt-classificatie + dedupe** (`config/.pr_agent.toml`
     [pr_reviewer].extra_instructions): elke key-issue header begint met
     [verified-bug]/[hypothetical]/[backlog]; herhaal geen bevinding die in een
     bestaande thread al met bewijs is weerlegd; orden op impact. Bewezen: de
     artifact (repo-map) wordt geAPPEND aan extra_instructions
     (`github_action_runner.py`: `extra_instructions + separator +
     artifact_text`), dus toml-instructies en artifact-context coëxisteren.
  4. **Concurrency/timeout**: review-jobs 30→60 min (grote diffs liepen tegen
     de 30-min-jobcap); caller-template splitst de concurrency-group per
     event-name zodat een `/review`-comment de lopende pull_request-auto-review
     niet meer cancelt (gecancelde run = gefaalde required check = UNSTABLE).
     Bestaande callers (49 repos) kunnen dat overnemen bij hun volgende
     caller-update — niet geforceerd.
- **Closed/merged PR → review overgeslagen.** Als de PR al gesloten of
  gemerged is op het moment dat de run start, is een review nutteloos
  (modeltijd weggooien). `review_tier1` heeft daarom een vroege `pr-state`
  guard die `gh api pulls/{n}` checkt en de hele job skipt zodra `state !=
  open`. Alle vervolgsteps (model, submit, detect) dragen
  `if: steps.pr-state.outputs.closed != 'true'`. Tier2 skipt automatisch mee
  (zijn `tier1_clean`-output ontbreekt dan). Dit dekt het race-venster
  "PR gesloten tussen trigger en run-start" — bv. PR #2 (gemerged 22:44,
  run gestart 22:53) dat anders onnodig 23+ min modeltijd verbruikte.
- **Gateway-auth is Bearer**, geen `X-Guardian-Org`-header: `Authorization:
  Bearer <key>`; keys staan per-client in `config/guardian.keys.yaml`
  (oude pad van de draaiende service: `/home/flip/llama_cpp_guardian/config/`).
- **De draaiende gateway-service leest config uit `/home/flip/llama_cpp_guardian`**
  (systemd `llama-guardian.service`), NIET uit
  `/home/flip/guardian-llmprovider-gateway`. Keys-file wordt per request
  herlezen; nieuwe keys werken zonder restart. Bij twijfel: beide paden
  sync houden.
- **Runners:** org-pool `m0nklabs-runner-1..4`, labels `self-hosted`,
  `Linux`, `X64`, `gpu`. PR-Piet gebruikt géén GPU. GPU-jobs nooit zonder
  `bin/gpu-run.sh` (serieel-lock) — maar PR-Piet doet geen GPU-werk.
- **tree-sitter-versies** zijn gepind in `requirements.txt` (0.26.x).
  `Language()`-wrapper is verplicht in 0.26 (PyCapsule ≠ Language).
- Gateway-catalog (2026-08-26): `deepseek/deepseek-v4-flash-0731`
  (context 1M), `z-ai/glm-5.2` (max_tokens 16384), `~deepseek/...-latest`,
  `z-ai/glm-5.3`, `moonshotai/kimi-k3`. De modellen uit de oorspronkelijke
  spec (`deepseek-chat`, `qwen-2.5-coder-32b`, `deepseek-r1`) staan er NIET in.

## Dynamische commit-identity (auteur/committer = LLM-modelnaam)

Sinds 2026-08-27 is de git-identity op deze host niet meer automatisch `PR-Piet`.
Optie B (operator-besluit): commits moeten herleidbaar zijn naar het model dat ze
maakte — `deepseek-v4-flash-0731` (tier 1 / de implementerende pi-agent) of
`glm-5.2`. Implementatie in `bin/` (+ geïnstalleerd):

- **`bin/git-agent-identity.sh`** — bepaalt de modelnaam (prioriteit
  `AGENT_MODEL`/`PI_MODEL` env → `~/.pi/agent/settings.json` `defaultModel`
  → de `guardian/openrouter/<model>`-suffix) en exporteert `GIT_AUTHOR_*`/
  `GIT_COMMITTER_*` op `<model>@m0nklabs.dev`.
- **`gc`-shellfunctie** (in `~/.bashrc` via
  `bin/install-dynamic-commit-identity.sh`) — zet die env in HETZELFDE proces
  voordat `git commit` draait (bewezen: anders negeert git de identity).
  Override per commit: `AGENT_MODEL=glm-5.2 gc ...`.
- **Per-checkout lokale config** — voor checkouts waar de implementerende
  agent werkt (bv. `/home/flip/guardian-llmprovider-gateway`) is `user.name`/
  `user.email` op de modelnaam gezet, zodat ook niet-interactieve shells /
  andere git-tools de juiste identity gebruiken.

**Waarom géén hook/alias (bewezen, niet overdoen):** een `prepare-commit-msg`-hook
kan de auteur niet wijzigen (git legt die vóór de hook vast); git negeert een
`alias.commit` omdat `commit` een builtin is. Enige werkende routes zijn env-in-
hetzelfde-proces (`gc`) of per-checkout config.

**Terugdraaien:** `bin/install-dynamic-commit-identity.sh --unset <dir>` zet
lokale identity terug; de `gc`-blok in `~/.bashrc` verwijderen herstelt het
oude gedrag; de global identity van de host is `PR-Piet <pr-piet@m0nklabs.dev>`.

## Veilige workflows

- Mapper lokaal testen: zie README ("Lokale test").
- Workflow-wijzigingen: testen op een test-PR in een doel-repo voordat je
  `@main` laat verwijzen naar nieuwe logica (of gebruik een `@ref`-pinning
  in de caller).
- Nieuwe gateway-key: `scripts/generate_key.py` in de gateway-repo, en de
  entry óók in `/home/flip/llama_cpp_guardian/config/guardian.keys.yaml`
  (draaiende service) zetten.

## Status

- ✅ Phase 1 (Core Scaffold & AST Mapper): `scripts/repo_mapper.py` klaar en
  getest op fixture-repo (python/ts/go; truncate en ignore-patterns werken).
- ✅ Phase 2 (Workflow & Gateway): `reusable-pr-piet.yml` + `config/.pr_agent.toml`
  + `examples/caller-pr-piet.yml`; gateway-key `pr-piet` aangemaakt en
  geverifieerd (chat-call via gateway werkt).
- ✅ Phase 3 (Org-wide): repo publiek, org-secret `GUARDIAN_API_KEY`, e2e
  bewezen op test-PR's #10 (findings → tier 2 skip) en #11 (schoon →
  tier 2 draaide). Caller geïnstalleerd in **alle m0nklabs-repos én alle
  m0nk111-repos** (49/49, 2026-08-27), allemaal met
  `single_call_review: true` (1 modelcall). m0nklabs-repos draaien direct
  (org-runners online + org-secret visibility=all). m0nk111-repos hebben de
  caller klaarstaan maar draaien pas met eigen (repo-level) runners +
  repo-secret `GUARDIAN_API_KEY` — zonder runner blijft de job queued.
- ✅ Open punten afgerond (2026-08-30; bewijs + methodiek in
  `docs/HANDOFF.md` / `docs/AGENT_JOURNAL.md`):
  - **Auto-trigger in productie bewezen**: 13 productie-PR's (guardian
    #8-#15, caretaker #1-#7) draaiden pr-piet automatisch; zuivere
    opened-trigger hard bewezen op guardian #8 (bot-review 11m47s na
    opened, zonder /review-command); tier 2 (glm-5.2) 3x success in
    productie; map-job 12/12 waargenomen golven success, 0 failures.
    Bekend risico: één GitHub-side event-delivery-misser (guardian #14,
    hersteld via re-trigger).
  - **Latency-benchmark deepseek vs glm** (identieke review-payload via de
    gateway): glm-5.2 16,5s → complete review (1229 tok, stop);
    deepseek-v4-flash-0731 74s+ zonder content (4000 tok puur reasoning,
    finish_reason=length) — deepseek verbrandt hele token-budgets aan
    reasoning, exact het bekende "Empty content (length)"-faalmode.
    Let op: de gateway heeft een response-cache voor identieke payloads
    (0,3s herhaal-respons met zero-usage; geen productie-impact).
  - **GitHub App-variant voor fork-PRs: uitgesteld** tot de eerste echte
    externe fork-PR (criterium: `head.repo.full_name != github.repository`
    of menselijke auteur met association NONE/FIRST_TIME_CONTRIBUTOR).
    0 fork-PR's in 435 gescalede PR's. Ontwerp + operator-checklist:
    README-sectie "Fork-PRs en secrets".
- **Mapper-truncatie-bug gefixt (a1633f8, 2026-08-30)**: de hard-truncate
  van het token-budget kon een héle sectie weggooien wanneer de inkorting
  1-12 tokens overschreed (gevonden op PR #12 guardian: de
  symbolen-sectie van 31k tokens verdween, context kromp naar 200 tokens).
  Fix: 12-token marker-reserve in de keep-loop — een ingekorte sectie past
  gegarandeerd. Daarnaast: symbolen-sectie op **churn-volgorde** (de
  kern van de PR overleeft truncatie, niet de alfabetisch-eerste
  bestanden). Live voor alle callers (`pr_piet_ref` default = main).
  Mapper getest op productie-PR's: guardian PR #12 (+2803/-283; full AST
  path 2,0s) en caretaker PR #1 (complete 4-sectie map, 2710 tok, 0,33s).
