#!/usr/bin/env bash
# git-agent-identity.sh — dynamische commit-identity = de naam van de LLM-agent.
#
# Wordt gebruikt als `prepare-commit-msg` git-hook (per-checkout) om bij elke
# commit de auteur/committer automatisch op de modelnaam van de agent te zetten
# (bv. deepseek-v4-flash-0731), i.p.v. de statische `PR-Piet`-identity.
#
# Model-bepaling (prioriteit):
#   1. $AGENT_MODEL of $PI_MODEL (env, indien door de agent gezet)
#   2. `~/.pi/agent/settings.json` -> defaultModel  (openrouter/deepseek/deepseek-v4-flash-0731)
#   3. laatste route-segment van een `guardian/openrouter/<m>`-adres
#   valt terug op bestaande identity als niets matcht.
#
# Gebruik als hook:  (zie install-git-agent-identity.sh)

set -uo pipefail

model=""
# 1. env-aanwijzing
if [ -n "${AGENT_MODEL:-}" ]; then model="$AGENT_MODEL"; fi
if [ -n "${PI_MODEL:-}" ]; then model="${PI_MODEL}"; fi

# 2. pi defaultModel
if [ -z "$model" ] && [ -f "$HOME/.pi/agent/settings.json" ]; then
  model="$(python3 -c "import json,os; p=os.path.expanduser('~/.pi/agent/settings.json'); d=json.load(open(p)); print(d.get('defaultModel',''))" 2>/dev/null || true)"
fi

# normaliseer: haal provider-prefixen weg en houd het model-identiteits-suffix
# openrouter/deepseek/deepseek-v4-flash-0731            -> deepseek-v4-flash-0731
# guardian/openrouter/deepseek/deepseek-v4-flash-0731:high -> deepseek-v4-flash-0731
if [ -n "$model" ]; then
  model="$(printf '%s' "$model" | sed -E 's#^[^/]+/[^/]+/##; s#^[^/]+/##; s#:[A-Za-z0-9._-]+$##' | tr -d '[:space:]')"
fi

if [ -n "$model" ]; then
  NAME="$model"
  EMAIL="$model@m0nklabs.dev"
  export GIT_AUTHOR_NAME="$NAME" GIT_AUTHOR_EMAIL="$EMAIL"
  export GIT_COMMITTER_NAME="$NAME" GIT_COMMITTER_EMAIL="$EMAIL"
fi
# anders: laat git de bestaande identity gebruiken (geen wijziging)
# Opmerking: `return` i.p.v. `exit`, zodat het script ook veilig gesourcet kan
# worden (als hook-subprocess is het effect hetzelfde: exit-code 0).
return 0 2>/dev/null || exit 0
