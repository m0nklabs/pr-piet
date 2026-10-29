# Reviewer prompt — anatomy, gaps and comparison

Status: working document (2026-10-30). Answers the operator question: *is the
reviewer's system prompt good enough to review everything in a PR, and how does
it compare to strong public review prompts?*

## 1. What the reviewer actually receives

| Layer | Size | Source |
|---|---|---|
| System prompt | ~226 lines | `pr_agent/settings/pr_reviewer_prompts.toml` in `m0nklabs/pr-agent` (upstream `pr-agent` line, `pr_review_prompt.system`) |
| Output schema | ~328 lines | same file — pydantic `PRReview.review`, rendered as YAML instructions |
| Our extra instructions | **13 lines / 1107 chars** | `config/.pr_agent.toml` → `[pr_reviewer].extra_instructions` |
| Repo context (mapper) | artifact, `max_artifact_size = 50000` | `scripts/repo_mapper.py` → `.pr_piet/context.md`, injected via `artifact_path` |
| Project conventions | up to `repo_context_max_lines = 300` | `repo_context_files = ["AGENTS.md"]` (fetched from the PR's repo) |

The honest headline: **our own contribution is 13 lines.** The review quality
is overwhelmingly determined by upstream's prompt plus the model. That is not
automatically bad — upstream's prompt is thoughtful — but it means "improve the
reviewer" mostly means "write better extra instructions and lift the silent
caps", not "rewrite the system prompt".

## 2. What the current prompt does well (keep this)

- **Explicit false-positive discipline**: flag clear bugs thoroughly, be certain
  for lower-severity claims, never speculate about code you cannot see, do not
  flag intentional design choices.
- **Diff-aware scope rules**: acknowledge visible scope boundaries (`if`/`for`/
  `try` at end of hunk) instead of calling the code incomplete; avoid duplicating
  existing functionality you cannot see.
- **Concrete reporting contract**: `issue_content` must name the trigger
  scenario; severity must be accurate; no praise/filler.
- **Machine-usable output**: line-anchored `key_issues_to_review` →
  inline comments + `suggested_fix` → GitHub "Apply" buttons.
- **Our additions**: classification tags (`[verified-bug]` / `[hypothetical]` /
  `[backlog]`) feeding the verdict logic, dedupe against existing threads,
  impact ordering, repo-context usage.

## 3. Gaps — what a senior reviewer checks that the prompt never asks for

Each item is a *dimension the prompt never mentions*, so the model covers it only
by accident.

| # | Gap | Consequence |
|---|---|---|
| G1 | **No dimension checklist.** "bugs, security, correctness, performance, test gaps" appears once, in passing. | Coverage is stochastic: a review may nail security and miss concurrency, or vice versa. |
| G2 | **No intent-first step.** Nothing asks "what is this PR trying to do, and does the diff actually achieve it?" | Misses *incomplete* implementations — the most common real defect — because only present code is judged. |
| G3 | **No change-completeness check.** No instruction to look for the callers/tests/config/docs that *should* have changed but did not. | Signature changes with untouched call sites, new config keys with no default, features with no test. |
| G4 | **No test-quality judgement.** Only `relevant_tests: Yes/No`. | Asserts-nothing tests, tests that mock the unit under test, happy-path-only all pass as "tests present". |
| G5 | **No cross-file tracing protocol.** Our extra instructions say "use callers/callees", but there is no procedure (e.g. "for every changed signature, check each caller in the context map"). | The repo map is available but under-used. |
| G6 | **No framework/language guidance.** Nothing about Python async, Go error wrapping, TS nullability, SQL parameterisation, React effect deps. | Language-specific bug classes depend on the model's own priors. |
| G7 | **No noise suppression list.** No rules for generated files, vendored code, lockfiles, formatting-only hunks, pure moves. | Review time spent on non-reviewable diffs; false positives on generated code. |
| G8 | **No duplicate-root-cause rule.** Same defect repeated in N files may produce N findings. | Findings list exhausts the cap (see §4) with one root cause. |
| G9 | **`security_concerns` is one free-text blob**, not per-finding. | Security issues cannot become inline threads with an Apply-able fix. |
| G10 | **Project conventions not enforced.** `AGENTS.md` is injected, but the prompt never says "treat these as binding project standards; cite the rule". | Reviewer ignores house rules that a human reviewer would cite. |
| G11 | **Our dedupe instruction is not executable (verified).** We tell the model "read the existing review threads on this PR… reference that thread instead of opening a duplicate". The prompt's only variables are `diff_hunk_format`, `extra_instructions`, `num_max_findings`, `num_pr_files`, `related_tickets_omitted`, `repo_context`, `skills_context` — **none carries existing review threads**. Comments are fetched in exactly two places: `/answer` mode Q&A and the finding-state marker reconciliation. | The model cannot see prior threads, so it either silently under-reports (fearing a duplicate it cannot check) or ignores the instruction. Real dedupe happens downstream in `reconcile_review_findings` + `submit_review.py`, not in the model. |

## 4. Silent caps that limit coverage more than the prompt does

These are configuration, not prompt text — and they cut coverage *without telling
the reader*.

| Setting | Value | Effect |
|---|---|---|
| `pr_reviewer.num_max_findings` | **5** | The prompt literally asks for "a concise list (0-5 issues)". A 12-issue PR yields 5 findings and 7 silent drops. |
| `config.large_patch_policy` | **`clip`** | Oversized diffs are *clipped*: files fall out of the review. The review still reads as complete. |
| `config.repo_context_max_lines` | **300** | Caps how much of the PR repo's `AGENTS.md` reaches the prompt. |
| `config.repo_context_files` | `["AGENTS.md"]` | Only this file; no `CONTRIBUTING.md`, no architecture doc. |

## 5. Comparison with strong public review prompts

### 5a. Review dimensions from the recognised standards

Derived from Google's engineering-practices review guide, OWASP (Top-10,
Cheat Sheets, ASVS), CWE concurrency classes (833/362/367/404/770), Google's
test guidance + Fowler on mocks, SemVer/AIP-180/ParallelChange. Every source
link was HTTP-200 verified. Per check question: can a diff-only LLM judge it
(`ja` / `deels` / `nee`), where **O** = observable inside the hunk and **I** =
inferred from the injected repo context.

| Dimension | Highest-value check questions | LLM without running code? |
|---|---|---|
| Design / scope / code health | does this mix a refactor with a behaviour change; is a helper duplicated instead of reused; does a config/build change ship with its documentation | mostly yes |
| Functionality by reading | error paths and boundaries (empty/null/zero/negative); early return after acquiring a resource; a removed guard; a log message that now lies | mostly yes |
| Security — injection & access | string-interpolated query/shell/HTML sink; authorization on a *new* route; unsafe deserialization; SSRF from a user-controlled URL | mostly yes |
| Security — secrets / crypto / data leak | secret or token in code, fixture or log; weak or misused crypto; PII written to logs | yes |
| Concurrency & resources | lock or transaction held across `await`/IO; lock released on every exit path; check-then-act race; unbounded queue/retry; missing timeout | mixed — interleaving proof is **no** |
| Test quality | does the test assert anything; does it mock the unit under test; happy-path only; would it still pass with the bug reintroduced | yes |
| API / contract & migrations | breaking contract without version bump or deprecation; migration without rollback; serialisation or unit change | partly |

**What a diff reviewer must not claim** (scope fence — worth stating in the
prompt itself): compile/test results, real performance, actual interleaving
races or deadlocks across unmapped call graphs, end-to-end authorization,
exploitability or severity, impact outside the repo (external consumers,
production schema state, real rollback), coverage or mock fidelity, style
compliance without the repo's linter config. "No findings" is a scope
statement, not a safety guarantee.

## 6. Proposed improvements

### 6a. Rewrite `extra_instructions` as a method, not a wish list

The current 13 lines are *constraints* ("don't invent", "be certain"). What is
missing is a **procedure**: intent → achievement → trace → dimension sweep →
test quality. Proposed replacement (25 lines, no fork change needed):

```text
You are a senior code reviewer for m0nklabs. Judge the change, not the author:
be specific, actionable and brief. Never invent issues; a sound PR gets a short
review saying so.

METHOD (in order, do not skip a step):
1. INTENT: state in one sentence what this PR is trying to achieve.
2. ACHIEVEMENT: decide whether the diff achieves that intent, and name anything
   the intent requires that is MISSING from the diff (unhandled branch, missing
   validation, callers still on the old signature, config key without a default,
   behaviour change without a test or migration).
3. TRACE: for every changed signature, shared helper, schema, config key or
   constant, use the injected repository context to check its callers/callees.
   A change that silently alters a shared contract is the highest-value finding
   you can report.
4. SWEEP each dimension against the diff: correctness and edge cases (empty,
   None/null, zero, negative, huge, unicode, timezone, off-by-one); error
   handling (swallowed exception, wrong exception type, missing cleanup);
   concurrency and resources (lock or transaction held across await/IO,
   check-then-act race, unbounded queue or retry, missing timeout, unclosed
   handle); security (secret in code or log, injection, authorization on a new
   path, unsafe deserialization, path traversal, SSRF, weak crypto); data
   (migration without rollback, serialisation or unit change); performance
   (N+1, work inside a loop, unindexed query, accidental quadratic); tests.
   Report only dimensions where you have a concrete finding.
5. TESTS: judge quality, not presence. A test that asserts nothing, mocks the
   unit under test, covers only the happy path, or would still pass with the bug
   reintroduced is a finding of its own.

WHAT NOT TO FLAG: generated or vendored files, lockfiles, formatting-only hunks,
pure renames or moves, deliberate design choices, and anything the project's
style tooling already enforces.

PROJECT STANDARDS: the repository context may contain the project's own rules
(AGENTS.md and similar). Treat them as binding house standards; a diff that
violates one is a finding, so cite the rule you rely on.

EVIDENCE: every finding names the concrete trigger (input, state or sequence)
and why it matters. If you cannot state a trigger, downgrade or drop it.

SCOPE FENCE: you cannot run code. Never claim that tests pass, that performance
is fine, or that a race "cannot happen"; say what you verified and what remains
unverified.

CLASSIFICATION (mandatory): start every key-issue header with exactly one tag:
[verified-bug] = you traced the failure path and can state the concrete trigger;
[hypothetical] = plausible risk you could NOT verify (say what would confirm it);
[backlog] = valid improvement, not merge-blocking for this PR.

DEDUPE: one finding per root cause. If the same defect repeats across files,
report it once and list the affected locations.

ORDER findings by impact (merge-blocking first), not by file position.
```

### 6b. Fix the non-executable dedupe instruction (G11)

Two honest options:

- **Cheap (prompt-only):** delete the "read the existing review threads" bullet.
  Downstream dedupe (`reconcile_review_findings`, `submit_review.py` window) is
  what actually prevents duplicates; the model's version is theatre.
- **Real (fork patch):** render the previous review's findings into a new prompt
  section (`### prior findings`) so the model can genuinely avoid re-raising a
  refuted issue. This is the only way to get the behaviour we asked for.

Recommendation: do the cheap fix now, and treat the fork patch as a separate
change with its own test.

### 6c. Lift the silent coverage caps

| Change | From | To | Why |
|---|---|---|---|
| `pr_reviewer.num_max_findings` | 5 | 8 | A 12-issue PR currently loses 7 findings without telling the reader. |
| `config.repo_context_files` | `["AGENTS.md"]` | `["AGENTS.md", "CONTRIBUTING.md"]` | House conventions usually live in both. Verify the file exists in target repos first — a missing file must not fail the run. |

`large_patch_policy = clip` stays for now: no run in the observed sample
actually clipped anything (0 "Patch too large" messages in the last runs of
`caretaker-llamacpp` and `guardian-llmprovider-gateway`), because the 1M context
budget is far larger than the observed ~24k-token prompts. It remains a latent
silent-coverage risk for a future giant PR, not a current defect.

## 7. Verification plan

1. Unit tests green (`pytest tests/`).
2. Prompt-size sanity: the replacement must stay well under the artifact budget
   and must not push the prompt past `max_model_tokens`.
3. E2E on a test PR in `m0nklabs/pr-piet-test`: (a) a PR with a *missing* piece
   (e.g. a new config key without a default) must produce a finding — the intent
   step should catch it; (b) a clean PR must still produce no findings (guard
   against the checklist causing false positives); (c) an assert-less test must
   be flagged as a test-quality finding.
4. Compare verdicts before/after on the same three heads
   (`caretaker #15`, `guardian #33`, `oelala #218`) — a prompt change that
   turns previously-found blocking issues into silence is a regression.
