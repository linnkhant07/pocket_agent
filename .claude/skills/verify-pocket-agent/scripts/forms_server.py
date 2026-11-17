"""Serve the fictional form suite (easy + hard + demo) on a fixed port until killed.

Verification scaffolding: nothing here is a real employer. Usage:
    uv run python .claude/skills/verify-pocket-agent/scripts/forms_server.py PORT
Prints `READY http://127.0.0.1:PORT` once listening.
"""
from __future__ import annotations

import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from pocket_agent.demo import DEMO
from pocket_agent.forms.catalog import FORMS
from pocket_agent.forms.hard_catalog import HARD_FORMS
from pocket_agent.forms.server import FormServer


def main() -> int:
    port = int(sys.argv[1])
    server = FormServer(tuple(FORMS) + tuple(HARD_FORMS) + (DEMO,))
    # FormServer binds port 0; bind the requested port instead so URLs are stable.
    original = ThreadingHTTPServer.__init__

    def bind_fixed(self, address, handler, *args, **kwargs):
        original(self, (address[0], port), handler, *args, **kwargs)

    ThreadingHTTPServer.__init__ = bind_fixed
    base = server.start()
    ThreadingHTTPServer.__init__ = original
    print(f"READY {base}", flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
