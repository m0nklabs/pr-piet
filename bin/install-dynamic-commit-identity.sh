#!/usr/bin/env bash
# install-dynamic-commit-identity.sh — installeer de dynamische commit-identity
# (auteur/committer = de LLM-modelnaam van de agent) op deze host.
#
# Wat het doet:
#   1. Legt een `gc`-shellfunctie in ~/.bashrc die vóór elke `git commit` de
#      identity op de modelnaam zet (via bin/git-agent-identity.sh) en dan de
#      commit aanroept. Robuust: de env staat in HETZELFDE proces als de commit,
#      dus git gebruikt hem écht (bewezen: `gc` → deepseek-v4-flash-0731).
#   2. Optioneel per-checkout: voor elk opgegeven checkout-dir zet hij de
#      lokale git user.name/user.email op de modelnaam (voor ALLE git-tools die
#      daar werken, óók voor bare `git commit`). Statisch per checkout, maar de
#      dynamische route (gc / AGENT_MODEL / PI_MODEL) heeft voorrang en overridet
#      dit altijd.
#
# Waarom geen hook/alias: beide zijn bewezen NIET-werkend op git niveau
#   - een prepare-commit-msg hook kan de auteur niet wijzigen (git legt de
#     auteur vóór de hook vast),
#   - git negeert een alias met de naam van een builtin (`git commit`).
#   Dus: gc (dynamisch) + per-checkout config (garantie voor elke tool).
#
# Model-bepaling per commit: AGENT_MODEL/PI_MODEL env > ~/.pi/agent/settings.json
# defaultModel (openrouter/deepseek/deepseek-v4-flash-0731 -> deepseek-v4-flash-0731).
#
# Gebruik:
#   ./install-dynamic-commit-identity.sh                  # alleen gc in ~/.bashrc
#   ./install-dynamic-commit-identity.sh <dir> [<dir>…]  # + lokale identity zetten
#   ./install-dynamic-commit-identity.sh --unset <dir>   # lokale identity terugzetten
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IDENTITY="${SCRIPT_DIR}/git-agent-identity.sh"
PROFILE="${HOME}/.bashrc"

if [ "${1:-}" = "--unset" ]; then
  for dir in "${@:2}"; do
    if [ -d "$dir/.git" ]; then
      git -C "$dir" config --unset user.name 2>/dev/null || true
      git -C "$dir" config --unset user.email 2>/dev/null || true
      echo "↩️  lokale identity teruggezet in $dir (valt terug op global)"
    else
      echo "⚠️  geen $dir/.git — overgeslagen"
    fi
  done
  exit 0
fi

# ---------- 1. gc-functie in ~/.bashrc ----------
GC_BLOCK=$(cat <<EOF

# --- dynamische commit-identity (modelnaam) ---
gc() {
  eval "\$(bash -c 'source ${IDENTITY}; echo "export GIT_AUTHOR_NAME=\${GIT_AUTHOR_NAME@Q} GIT_AUTHOR_EMAIL=\${GIT_AUTHOR_EMAIL@Q} GIT_COMMITTER_NAME=\${GIT_COMMITTER_NAME@Q} GIT_COMMITTER_EMAIL=\${GIT_COMMITTER_EMAIL@Q}"')"
  git commit "\$@"
}
EOF
)

if ! grep -q "dynamische commit-identity (modelnaam)" "$PROFILE" 2>/dev/null; then
  printf '\n%s\n' "$GC_BLOCK" >> "$PROFILE"
  echo "✅ gc-functie toegevoegd aan $PROFILE"
else
  echo "ℹ️  gc-functie stond al in $PROFILE"
fi

# ---------- 2. optioneel per-checkout identity zetten ----------
if [ "$#" -gt 0 ]; then
  # bepaal de modelnaam één keer (voor de per-checkout config)
  MODEL="$(env -u AGENT_MODEL -u PI_MODEL bash -c 'source '"${IDENTITY}"'; printf "%s" "${GIT_AUTHOR_NAME:-}"')"
  for dir in "$@"; do
    if [ -d "$dir/.git" ]; then
      git -C "$dir" config user.name "${MODEL:-NOTSET}"
      git -C "$dir" config user.email "${MODEL:-NOTSET}@m0nklabs.dev"
      echo "✅ lokale identity in $dir -> ${MODEL:-NOTSET} <${MODEL:-NOTSET}@m0nklabs.dev>"
    else
      echo "⚠️  geen $dir/.git — overgeslagen"
    fi
  done
fi

echo ""
echo "Klaar. Identity per commit dynamisch via:"
echo "  - 'gc ...' (git commit)      -> auteur/committer = modelnaam (dynamisch)"
echo "  - AGENT_MODEL=glm-5.2 gc ... -> forceer een specifiek model"
echo "Nota: bestaande shells krijgen gc pas na 'source ~/.bashrc'."
