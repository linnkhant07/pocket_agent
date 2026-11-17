#!/usr/bin/env bash
# Read-only health check for one run. Usage: doctor.sh RUN_DIR
set -uo pipefail
RUN="${1:?usage: doctor.sh RUN_DIR}"; source "$RUN/env"; ok=1
check() { if eval "$2" >/dev/null 2>&1; then echo "ok   $1"; else echo "FAIL $1"; ok=0; fi; }
check "isolated home has profile.yaml"       "test -f '$POCKET_AGENT_HOME/profile.yaml'"
check "isolated home has resume.pdf"         "test -f '$POCKET_AGENT_HOME/resume.pdf'"
check "home is not ~/.pocket-agent"          "test '$POCKET_AGENT_HOME' != '$HOME/.pocket-agent'"
check "forms pid alive (ours)"               "kill -0 \$(cat '$RUN/forms.pid')"
check "forms server serves demo form"        "curl -sf '$FORMS/form/prototype-demo' | grep -q 'First Name'"
if [[ -n "${UI:-}" ]]; then
  check "ui pid alive (ours)"                "kill -0 \$(cat '$RUN/ui.pid')"
  check "ui serves the app"                  "curl -sf '$UI/' | grep -q googleLogin"
  check "ui restores the fake session"       "curl -sf -X POST -H 'Origin: $UI' '$UI/api/session' | grep -q access_token"
  check "ui rejects a foreign Host"          "test \$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.test' '$UI/') = 403"
fi
check "playwright chromium installed"        "uv run --quiet python -c 'from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(); b.close(); p.stop()'"
[[ $ok == 1 ]] && echo "doctor: healthy" || { echo "doctor: unhealthy"; exit 1; }
