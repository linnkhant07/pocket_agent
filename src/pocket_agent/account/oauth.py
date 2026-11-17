"""Google OAuth for the local runner: PKCE, random state and loopback callback."""
from __future__ import annotations

import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from pocket_agent.account.client import AccountClient, AccountError


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def google_login(client: AccountClient, *, port: int = 8766, timeout: float = 180, open_browser=None) -> dict:
    verifier, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log authorization codes or state in callback URLs.

        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            valid = (parsed.path == "/auth/callback" and len(params.get("pa_state", [])) == 1
                     and hmac.compare_digest(params["pa_state"][0], state))
            if valid and ("error" in params or len(params.get("code", [])) == 1):
                result["error"] = "error" in params
                result["code"] = params.get("code", [""])[0]
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(b"<h1>Pocket Agent</h1><p>Return to your terminal to finish signing in. You can close this tab.</p>" if valid
                             else b"<h1>Sign-in not recognized</h1><p>Start again from pocket-agent login.</p>")

    class LoopbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

    try:
        server = LoopbackServer(("127.0.0.1", port), Callback)
    except OSError:
        raise AccountError(f"Could not open runner sign-in on port {port}. Close another runner login and retry.", 409) from None
    try:
        callback = f"http://127.0.0.1:{server.server_port}/auth/callback?" + urlencode({"pa_state": state})
        url = client.google_sign_in_url(callback, pkce_challenge(verifier))
        print("Continue with Google in your browser. If it does not open, visit this sign-in URL:")
        print(url)  # URL contains public challenge/state, never the verifier or session.
        (open_browser or webbrowser.open)(url)
        deadline = time.monotonic() + timeout
        server.timeout = 1
        while not result and time.monotonic() < deadline:
            server.handle_request()
        if not result or result.get("error") or not result.get("code"):
            raise AccountError("Google sign-in was cancelled or timed out. Run pocket-agent login again.", 400)
        return client.exchange_oauth_code(result["code"], verifier)
    finally:
        server.server_close()
