#!/usr/bin/env bash
# Start an isolated verification instance: fresh POCKET_AGENT_HOME, fictional form server,
# and (with --ui) the app with a fake signed-in account. Prints the run dir; source "$RUN/env" afterwards.
# Usage: [HEADED=1] launch.sh [--ui]    (HEADED=1 shows the filler's Chromium window)
set -euo pipefail
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO"
free_port() { uv run --quiet python -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
RUN="$REPO/.pytest_cache/verify-pocket-agent/run-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$RUN/home" "$RUN/evidence" "$RUN/logs"
export POCKET_AGENT_HOME="$RUN/home"
uv run --quiet pocket-agent init --no-pull --yes > "$RUN/logs/init.log" 2>&1

FORMS_PORT="$(free_port)"
nohup uv run --quiet python .claude/skills/verify-pocket-agent/scripts/forms_server.py "$FORMS_PORT" \
  > "$RUN/logs/forms.log" 2>&1 &
echo $! > "$RUN/forms.pid"
for _ in $(seq 1 100); do grep -q READY "$RUN/logs/forms.log" 2>/dev/null && break; sleep 0.2; done
grep -q READY "$RUN/logs/forms.log" || { echo "forms server failed; see $RUN/logs/forms.log" >&2; exit 1; }

{
  echo "export RUN='$RUN'"
  echo "export POCKET_AGENT_HOME='$RUN/home'"
  echo "export FORMS='http://127.0.0.1:$FORMS_PORT'"
} > "$RUN/env"

if [[ "${1:-}" == "--ui" ]]; then
  # The real app with a fake signed-in Supabase account and Keychain (see serve_app.py).
  UI_PORT="$(free_port)"; CDP_PORT="$(free_port)"
  POCKET_AGENT_HOME="$RUN/home" nohup uv run --quiet python .claude/skills/verify-pocket-agent/scripts/serve_app.py \
    "$UI_PORT" "http://127.0.0.1:$FORMS_PORT" "$CDP_PORT" ${HEADED:+--headed} > "$RUN/logs/ui.log" 2>&1 &
  echo $! > "$RUN/ui.pid"
  for _ in $(seq 1 100); do curl -sf "http://127.0.0.1:$UI_PORT/" >/dev/null && break; sleep 0.2; done
  curl -sf "http://127.0.0.1:$UI_PORT/" >/dev/null || { echo "ui failed; see $RUN/logs/ui.log" >&2; exit 1; }
  echo "export UI='http://127.0.0.1:$UI_PORT'" >> "$RUN/env"
  echo "export FILLER_CDP='$CDP_PORT'" >> "$RUN/env"
fi
echo "$RUN"
