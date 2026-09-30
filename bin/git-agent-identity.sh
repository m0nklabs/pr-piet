#!/usr/bin/env bash
# git-agent-identity.sh — dynamische commit-identity = de naam van de LLM-agent.
#
# Zet de auteur/committer op de modelnaam van de agent die het werk doet
# (bv. deepseek-v4.1-flash), i.p.v. de statische `PR-Piet`-identity. Wordt
# gesourcet door de `gc`-shellfunctie (zie install-dynamic-commit-identity.sh)
# en gebruikt door bin/refresh-agent-identity.sh om per-checkout identities bij
# te houden.
#
# Model-bepaling (prioriteit) — geen enkele modelnaam staat in dit script:
#   1. $AGENT_MODEL of $PI_MODEL              (expliciete override per commit)
#   2. $DSH_SESSION_JSONL -> laatste `request/header`-record, config.model
#                                             (het model van DEZE sessie)
#   3. $DSH_HOME/settings.yaml -> agent-default-model.model
#                                             (host-default van DeepSeek Harness)
#   4. $PI_SETTINGS -> defaultModel           (pi-harness default)
#   niets matcht -> bestaande identity blijft ongewijzigd.
#
# Paden zijn overschrijfbaar via env (DSH_HOME, DSH_SESSION_JSONL, PI_SETTINGS).
# Let op: DSH gaat vóór pi, dus op een host waar beide staan wint de
# DSH-default; forceer een ander model met `AGENT_MODEL=<naam> gc ...`.
#
# Gebruik:
#   source git-agent-identity.sh        # exporteert GIT_AUTHOR_*/GIT_COMMITTER_*
#   git-agent-identity.sh --print       # print alleen de resolved modelnaam
#
# @module git-agent-identity

set -uo pipefail

DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
DSH_SETTINGS="${DSH_SETTINGS:-$DSH_HOME/settings.yaml}"
PI_SETTINGS="${PI_SETTINGS:-$HOME/.pi/agent/settings.json}"

model=""

# 1. env-aanwijzing
if [ -n "${AGENT_MODEL:-}" ]; then model="$AGENT_MODEL"; fi
if [ -n "${PI_MODEL:-}" ]; then model="${PI_MODEL}"; fi

# 2. het model van de lopende DSH-sessie (autoritatief per sessie)
if [ -z "$model" ] && [ -n "${DSH_SESSION_JSONL:-}" ] && [ -f "${DSH_SESSION_JSONL}" ]; then
  model="$(DSH_SESSION_JSONL="$DSH_SESSION_JSONL" python3 - <<'PY' 2>/dev/null || true
import json, os, sys
path = os.environ["DSH_SESSION_JSONL"]
last = None
try:
    from compression import zstd          # Python >= 3.14
    with open(path, "rb") as fh, zstd.ZstdFile(fh) as stream:
        for line in stream:
            if b'"type":"request/header"' in line:
                last = line
except ImportError:                        # oudere python: val terug op zstdcat
    import subprocess
    proc = subprocess.run(["zstdcat", path], capture_output=True)
    for line in proc.stdout.splitlines():
        if b'"type":"request/header"' in line:
            last = line
if last:
    try:
        print(json.loads(last)["data"]["header"]["config"]["model"])
    except Exception:
        pass
PY
)"
fi

# 3. host-default uit de DSH-settings (geen YAML-dependency, alleen de sleutel)
if [ -z "$model" ] && [ -f "$DSH_SETTINGS" ]; then
  model="$(DSH_SETTINGS="$DSH_SETTINGS" python3 - <<'PY' 2>/dev/null || true
import os, re
block = False
with open(os.environ["DSH_SETTINGS"], encoding="utf-8") as fh:
    for line in fh:
        if re.match(r"^agent-default-model:\s*$", line):
            block = True
            continue
        if block:
            if line.strip() and not line[:1].isspace():
                break
            match = re.match(r"\s*model:\s*(\S+)", line)
            if match:
                print(match.group(1))
                break
PY
)"
fi

# 4. pi-harness default
if [ -z "$model" ] && [ -f "$PI_SETTINGS" ]; then
  model="$(PI_SETTINGS="$PI_SETTINGS" python3 -c "import json,os; print(json.load(open(os.environ['PI_SETTINGS'])).get('defaultModel',''))" 2>/dev/null || true)"
fi

# normaliseer: haal provider-prefixen weg en houd het model-identiteits-suffix
# openrouter/deepseek/deepseek-v4.1-flash            -> deepseek-v4.1-flash
# guardian/openrouter/deepseek/deepseek-v4.1-flash:high -> deepseek-v4.1-flash
if [ -n "$model" ]; then
  model="$(printf '%s' "$model" | sed -E 's#^[^/]+/[^/]+/##; s#^[^/]+/##; s#:[A-Za-z0-9._-]+$##' | tr -d '[:space:]')"
fi

# --print: alleen de resolved naam (voor scripts), geen env-mutatie
if [ "${1:-}" = "--print" ]; then
  [ -n "$model" ] && printf '%s\n' "$model"
  exit 0
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
