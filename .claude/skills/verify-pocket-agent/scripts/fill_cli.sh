#!/usr/bin/env bash
# Run the real `pocket-agent fill` CLI unattended + headless against one fictional form.
# Usage: fill_cli.sh RUN_DIR FORM_ID [extra fill args...]
# Evidence: $RUN/evidence/cli-<FORM_ID>/{stdout.txt,last-fill-report.json,exit_code,memory-keys.txt}
set -uo pipefail
RUN="${1:?usage: fill_cli.sh RUN_DIR FORM_ID}"; FORM="${2:?form id}"; shift 2
source "$RUN/env"
OUT="$RUN/evidence/cli-$FORM"; mkdir -p "$OUT"
rm -f "$POCKET_AGENT_HOME/last-fill-report.json"
uv run --quiet pocket-agent fill "$FORMS/form/$FORM" --model none --unattended --headless "$@" > "$OUT/stdout.txt" 2>&1
code=$?; echo "$code" > "$OUT/exit_code"
cp "$POCKET_AGENT_HOME/last-fill-report.json" "$OUT/" 2>/dev/null || echo "no report written" >> "$OUT/stdout.txt"
uv run --quiet python -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);[print(r[0]) for r in c.execute('select key from entries order by key')]" \
  "$POCKET_AGENT_HOME/forms.sqlite" > "$OUT/memory-keys.txt" 2>&1
echo "exit=$code report=$OUT/last-fill-report.json"
exit "$code"
