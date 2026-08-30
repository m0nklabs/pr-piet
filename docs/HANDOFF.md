# HANDOFF — PR-Piet current state

> Appended same-session by agents. Hot files (`AGENTS.md`, `README.md`) are only
> touched in deliberate batched promotion passes — see user-level AGENTS.md
> maintenance discipline.

## Session 2026-08-30 (mapper production test + open points)

### 1. Tree-sitter mapper tested on the busiest repo — defect found & fixed

Busiest repo: `m0nklabs/guardian-llmprovider-gateway` (Python, most active).
Fixtures: PR #12 (+2803/−283, 12 files, 214 KB diff) and caretaker-llamacpp PR #1
(+557/−0, 11 files).

| Scenario | Result |
|---|---|
| PR #12, CI defaults (100 KB diff cap) | diff-only fail-safe fires (by design): files-only context, 0.55 s |
| PR #12, raised cap (full AST path) | 566 symbols + 2190 call refs in 2.0 s; raw 33 309 tokens → truncated |
| caretaker PR #1 (fits under cap) | complete 4-section map, 2710 tokens, 0.33 s, no truncation |

**Defect found (latent, pre-existing):** the token-budget hard-truncate loop could
drop an ENTIRE section. Mechanism: the keep-loop stopped at `used + tokens(kept)`
= 3891, the marker pushed the section to 3902 tokens, and the outer check
`used + approx(part) > budget` (200 + 3902 > 4096) discarded the whole symbols
section — context shrank to a 200-token files-only stub. Pre-fix behavior only
worked by luck (alphabetical ordering happened to land under budget).

**Fixes (commit `a1633f8`, pushed to main → live for all 49 callers because
`pr_piet_ref` defaults to `main`):**
1. Reserve 12 tokens for the truncate-marker inside the keep-loop; a freshly
   truncated section is never discarded.
2. Symbol-section files ordered by churn (additions+deletions) instead of
   alphabetically, so truncation keeps the core of the PR (on PR #12 the new
   `caretaker_runtime.py` was cut while `manager.py` survived).
3. Log line for the diff-only path reported `approx_tokens(list)` (bogus "4
   tokens"); now reports the real count.

Verified: py_compile + PR #12 fixture (4044/4096 tokens, symbols section present,
churn-first) + pr-piet self-test + edge case `--max-tokens 50` (44 tokens, no
crash) + old-vs-new apples-to-apples via `git stash`.

### 2. Latency benchmark deepseek vs glm (open point → done)

Identical review-style payload (12.8 KB, real diff hunk from PR #12), gateway
`127.0.0.1:11436/v1`, key `pr-piet`:

| Model | Time | Output | Verdict |
|---|---|---|---|
| `deepseek/deepseek-v4-flash-0731` (DeepInfra) | **74.2 s** | 4000 completion tokens, ALL reasoning, `finish_reason=length`, content=null | no review content after 74 s |
| `z-ai/glm-5.2` | **16.5 s** | 1229 tokens (922 reasoning), `finish_reason=stop`, real finding | complete review |

DeepSeek burns entire token budgets on reasoning — the exact known failure mode
behind `ai_timeout=900` / "Empty content (finish_reason: length)" in AGENTS.md.
GLM-5.2 answered the same request ~4.5× faster with usable content. Production
corroboration (check-run evidence): tier-1 model calls 36 s–15m17s depending on
diff; tier-2 glm wall time ~49–59 s in production.

Caveat: the gateway silently response-caches identical chat payloads (repeat call
→ 0.3 s, fresh id, zero usage, identical content). Rarely hits in production
(payloads differ per PR); benchmark repeats must vary the payload or use the probe
values.

### 3. Auto-trigger on production PRs (open point → proven)

Verified independently (subagent report + spot-check of the load-bearing claim):
- Guardian #8: bot review submitted 11m47s after PR-opened with NO `/review`
  command before it — pure auto-trigger. (This session re-read the review object:
  `submitted_at 2026-08-27T20:02:26Z`, PR opened `19:50:39`.)
- 13 production PRs ran pr-piet automatically (guardian #8/#10/#11/#12/#13/#14/#15,
  caretaker #1/#3/#4/#5/#6/#7); ≥11 with fully successful pipeline; tier 2 (glm-5.2)
  succeeded 3× in production; map job: 12/12 observed waves success, zero failures.
- First-review latency: 2m21s–11m47s; runner queue up to 14m06s (caretaker #6).
- No true misses: one GitHub-side event-delivery miss (guardian #14, recovered via
  re-trigger), bot-authored PRs correctly skipped by the caller guard.

### 4. Fork-PRs / GitHub App variant (open point → assessed, deferred)

Facts: **0 external fork-PRs** in m0nklabs (435 PRs scanned across all 12 public
repos, cross-checked via GitHub MCP + gh REST); all human authors are org-members,
bot PRs (dependabot 220, Copilot 98, …) are already skipped by the caller guard
`sender.type != 'Bot'` (empirically verified).

Design (tested on paper, NOT built): App "pr-piet" as token source only (webhooks
off); caller switches `pull_request` → `pull_request_target` (workflow file always
from base branch; review jobs never check out PR code — pr-agent is API-only, the
Security-Lab-approved pattern); map-job stays secret-free; review jobs mint
per-job tokens via `actions/create-github-app-token@v3` (1 h, revoked in
post-step); minimal permissions `pull_requests: write` + `contents: read`;
`issues: write` as belt-and-braces. Watch out: `actions/checkout` v7 refuses
fork-PR checkouts in `pull_request_target` workflows by default (2026-06-18
pwn-protection) → pin the map-job checkout to v4 or opt out consciously in that
secret-free job. `workflow_run` is NOT an alternative (pr-agent skips fork-PRs
there).

**Recommendation: defer** until the first real external fork-PR (criterion:
`head.repo.full_name != github.repository`, or human author with association
`NONE`/`FIRST_TIME_CONTRIBUTOR`, or an operator decision to allow external
contributors). Then E2E on pr-piet-test first, org rollout after. Only prep done
now: the previously dangling README section "Fork-PRs en secrets" was written
(referenced by `examples/caller-pr-piet.yml`).

### 5. Remaining recommendations (not blocking)

- Consider raising `max_diff_bytes` (100 KB → e.g. 512 KB) or making it a caller
  input: PR #12 (214 KB) got a files-only context in CI while the full AST path
  takes only 2 s and truncation is now correct. Test via a test-PR first (repo rule).
- Callers should adopt the 2026-08-30 caller template (per-event concurrency
  split): guardian still runs the old template — the cancelled jobs on PR #15 and
  the ~31-min failure on #14 are direct evidence.
- AGENTS.md Status section updated in the same batched pass (auto-trigger,
  benchmark, GitHub App assessment).
