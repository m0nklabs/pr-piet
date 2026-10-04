#!/bin/bash
# PART B: wait for the push (synchronize) run on head ccf0359, then inspect the
# tier-1 job log for the fail-signal and incremental evidence, and check the
# new formal review (marker + head sha).
set -u
REPO=m0nklabs/pr-piet-test
PR=23
HEAD_SHA=ccf03598f274a21a6f5b8027ede9125d7df69bbe
DIR=/home/flip/pr-piet/e2e-anchor2
LOGFILE=$DIR/tier1-part-b.log
DEADLINE=$(( $(date +%s) + 1800 ))

echo "== PART B poll started $(date -u +%FT%TZ) =="

run_id=""
while [ $(date +%s) -lt $DEADLINE ]; do
  run_id=$(gh api "repos/$REPO/actions/runs?head_sha=$HEAD_SHA&per_page=10" \
    --jq '[.workflow_runs[] | select(.name | test("PR-Piet"))] | sort_by(.created_at) | last | .id // ""' 2>/dev/null)
  if [ -n "$run_id" ]; then
    echo "run found: $run_id"
    break
  fi
  sleep 20
done
if [ -z "$run_id" ]; then
  echo "FAIL: no PR-Piet run appeared for head $HEAD_SHA within 30 min"
  exit 2
fi

while [ $(date +%s) -lt $DEADLINE ]; do
  status=$(gh api "repos/$REPO/actions/runs/$run_id" --jq '.status')
  conclusion=$(gh api "repos/$REPO/actions/runs/$run_id" --jq '.conclusion // "pending"')
  echo "$(date -u +%FT%TZ) run=$run_id status=$status conclusion=$conclusion"
  [ "$status" = "completed" ] && break
  sleep 30
done
echo "== run $run_id completed: conclusion=$conclusion =="
gh api "repos/$REPO/actions/runs/$run_id/jobs?per_page=20" \
  --jq '.jobs[] | "job id=\(.id) name=\(.name) status=\(.status) conclusion=\(.conclusion)"'

# Tier-1 job id and its raw log
job_id=$(gh api "repos/$REPO/actions/runs/$run_id/jobs?per_page=20" \
  --jq '[.jobs[] | select(.name | test("Review tier 1"))] | first | .id')
echo "tier-1 job id: $job_id"
gh api "repos/$REPO/actions/jobs/$job_id/logs" > "$LOGFILE" 2>/dev/null
echo "log size: $(wc -c < "$LOGFILE") bytes"

echo "== CHECK A: fail-signal 'No previous review found, will review the entire PR' =="
if grep -nF "No previous review found, will review the entire PR" "$LOGFILE"; then
  echo "CHECK-A-RESULT: FAIL-SIGNAL PRESENT"
else
  echo "CHECK-A-RESULT: fail-signal ABSENT"
fi

echo "== CHECK B: incremental evidence in tier-1 log (literal lines) =="
grep -niE "incremental|is_incremental|commits_range|since previous PR-Agent review" "$LOGFILE" | head -40 || echo "no incremental matches in log"

echo "== CHECK C: reviews after push =="
gh api "repos/$REPO/pulls/$PR/reviews" --jq 'sort_by(.id) | .[] | "review id=\(.id) state=\(.state) commit=\(.commit_id) submitted=\(.submitted_at)"'

new_body=$(gh api "repos/$REPO/pulls/$PR/reviews" --jq '[.[] | select(.commit_id=="'$HEAD_SHA'")] | sort_by(.id) | last | .body // ""')
if [ -z "$new_body" ]; then
  echo "NO FORMAL REVIEW on head $HEAD_SHA"
  exit 3
fi
echo "== first 5 lines of the review body on head $HEAD_SHA =="
printf '%s' "$new_body" > "$DIR/review-b-body.md"
head -5 "$DIR/review-b-body.md" | nl -ba -w2 -s': '
first5=$(head -5 "$DIR/review-b-body.md")
if printf '%s' "$first5" | grep -qF '<!-- pr-agent:review:incremental -->'; then
  echo "MARKER-CHECK: '<!-- pr-agent:review:incremental -->' IS within first 5 lines"
else
  echo "MARKER-CHECK: '<!-- pr-agent:review:incremental -->' NOT within first 5 lines"
  if grep -qF '<!-- pr-agent:review:incremental -->' "$DIR/review-b-body.md"; then
    echo "(marker IS in the body, but later than line 5 — line: $(grep -nF '<!-- pr-agent:review:incremental -->' "$DIR/review-b-body.md" | head -1 | cut -d: -f1))"
  else
    echo "(marker NOT in the body at all)"
  fi
fi
echo "== incremental section lines in review body =="
grep -niE "incremental|since previous PR-Agent review" "$DIR/review-b-body.md" | head -10
