#!/bin/bash
# PART A: wait for the auto-review run on PR #23 (head 1a9fdc7), then inspect
# the formal review: first 5 lines + marker + review-id + head-sha.
set -u
REPO=m0nklabs/pr-piet-test
PR=23
HEAD_SHA=1a9fdc7f145de4b5e00cf8865eb66c9ca5ddc006
OUT=/home/flip/pr-piet/e2e-anchor2/part-a-result.txt
DEADLINE=$(( $(date +%s) + 1800 ))

echo "== PART A poll started $(date -u +%FT%TZ) =="

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

# Wait for the run to complete
while [ $(date +%s) -lt $DEADLINE ]; do
  status=$(gh api "repos/$REPO/actions/runs/$run_id" --jq '.status')
  conclusion=$(gh api "repos/$REPO/actions/runs/$run_id" --jq '.conclusion // "pending"')
  echo "$(date -u +%FT%TZ) run=$run_id status=$status conclusion=$conclusion"
  [ "$status" = "completed" ] && break
  sleep 30
done

echo "== run $run_id completed: conclusion=$conclusion =="

# Jobs of this run
gh api "repos/$REPO/actions/runs/$run_id/jobs?per_page=20" \
  --jq '.jobs[] | "job id=\(.id) name=\(.name) status=\(.status) conclusion=\(.conclusion)"'

# The formal reviews on the PR
echo "== reviews on PR #$PR =="
gh api "repos/$REPO/pulls/$PR/reviews" --jq '.[] | "review id=\(.id) user=\(.user.login) state=\(.state) commit=\(.commit_id) submitted=\(.submitted_at)"'

# First 5 lines of the LATEST formal review body (with line numbers)
echo "== first 5 lines of the latest review body =="
gh api "repos/$REPO/pulls/$PR/reviews" --jq 'sort_by(.id) | last | .body' | head -5 | nl -ba -w2 -s': '

# Marker check
latest_body=$(gh api "repos/$REPO/pulls/$PR/reviews" --jq 'sort_by(.id) | last | .body')
first5=$(printf '%s' "$latest_body" | head -5)
if printf '%s' "$first5" | grep -qF '<!-- pr-agent:review:full -->'; then
  echo "MARKER-CHECK: '<!-- pr-agent:review:full -->' IS within first 5 lines of latest review body"
else
  echo "MARKER-CHECK: '<!-- pr-agent:review:full -->' NOT within first 5 lines of latest review body"
fi

# Also show where the marker line is (line number) in the full body
marker_line=$(printf '%s' "$latest_body" | grep -nF '<!-- pr-agent:review:full -->' | head -1 | cut -d: -f1)
echo "marker line number in review body: ${marker_line:-none}"

# Issue comments by the bot (guide/describe) for context (ids + first line only)
echo "== bot issue comments (first line only) =="
gh api "repos/$REPO/issues/$PR/comments?per_page=50" \
  --jq '.[] | select(.user.login | startswith("github-actions")) | "comment id=\(.id) :: \(.body | split("\n")[0])"'
