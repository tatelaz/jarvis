#!/usr/bin/env bash
# Commit solo se lint e test sono verdi. Uso: bash scripts/commit.sh "messaggio"
set -uo pipefail
cd "$(dirname "$0")/.."
log=$(mktemp)
if ! bash scripts/check.sh >"$log" 2>&1; then
  tail -25 "$log"; echo "CONTROLLI FALLITI: nessun commit."; exit 1
fi
tail -1 "$log"
git add -A
git commit -q -m "$1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git log --oneline -1
