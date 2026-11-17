#!/usr/bin/env bash
# Stop only the processes this run started. Keeps $RUN/evidence and $RUN/logs.
# Usage: cleanup.sh RUN_DIR [--purge-home]
set -uo pipefail
RUN="${1:?usage: cleanup.sh RUN_DIR}"
for pidfile in "$RUN"/*.pid; do
  [[ -f "$pidfile" ]] || continue
  pid="$(cat "$pidfile")"
  # uv spawns the python child; stop the child tree first, then the uv parent.
  pkill -TERM -P "$pid" 2>/dev/null; kill -TERM "$pid" 2>/dev/null
  for _ in $(seq 1 25); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  kill -0 "$pid" 2>/dev/null && { pkill -KILL -P "$pid"; kill -KILL "$pid"; } 2>/dev/null
  rm -f "$pidfile"
done
[[ "${2:-}" == "--purge-home" ]] && rm -rf "$RUN/home"
echo "cleaned $RUN (evidence kept at $RUN/evidence)"
