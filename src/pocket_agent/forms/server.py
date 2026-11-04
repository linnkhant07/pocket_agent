"""Local server for the fictional application suite. Nothing here is a real employer."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pocket_agent.forms.catalog import FORMS, FormSpec
from pocket_agent.forms.render import render_form


class FormServer:
    def __init__(self, forms: tuple[FormSpec, ...] = FORMS) -> None:
        self.forms = {form.id: form for form in forms}
        self.base = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        forms = self.forms

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/submitted":
                    self._body(b"submitted")
                    return
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) == 2 and parts[0] == "form" and parts[1] in forms:
                    query = parse_qs(parsed.query)
                    perturb = (query.get("perturb") or [None])[0]
                    body = render_form(forms[parts[1]], perturb=perturb).encode("utf-8")
                    self._body(body)
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                self._body(b"submitted")

            def _body(self, payload: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        self.base = f"http://{host}:{port}"
        return self.base

    def url(self, form_id: str, perturb: str | None = None) -> str:
        suffix = f"?perturb={perturb}" if perturb else ""
        return f"{self.base}/form/{form_id}{suffix}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
