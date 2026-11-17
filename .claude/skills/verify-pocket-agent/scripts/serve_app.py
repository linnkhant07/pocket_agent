"""Run the real `pocket-agent ui` app with an in-memory Supabase account and Keychain.

Verification scaffolding. The account is already signed in, with the fictional Jordan Hale profile and resume.
The Desk's filler Chromium serves the fictional forms at https://forms.pocket.test, because account fills
refuse plain HTTP, and listens for CDP on --debug-port so drive_ui.py can read the filled page.
Usage:
    uv run python .claude/skills/verify-pocket-agent/scripts/serve_app.py PORT FORMS_URL DEBUG_PORT [--headed]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests"))


def main() -> int:
    import uvicorn
    from account_fakes import https_forms, install, ready_profile

    from pocket_agent.ui.app import create_app

    port, forms, debug_port = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    # Fake project settings, so a real .env in the working folder is never read.
    os.environ["SUPABASE_URL"] = "https://project.test"
    os.environ["SUPABASE_ANON_KEY"] = "public-test"
    install(setattr, signed_in=True, profile=ready_profile())
    app = create_app(port=port, launch=https_forms(forms, headless="--headed" not in sys.argv, debug_port=debug_port))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
