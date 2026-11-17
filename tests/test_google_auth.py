"""OAuth boundary tests; never connect to Google or send email."""
import json
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from pocket_agent.account.client import AccountClient, AccountError
from pocket_agent.account.oauth import google_login, pkce_challenge


def test_pkce_rfc7636_vector():
    assert pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_google_authorize_and_code_exchange_use_pkce_without_email():
    def handler(request):
        if request.url.path == "/auth/v1/settings":
            return httpx.Response(200, json={"external": {"google": True}})
        assert request.url.path == "/auth/v1/token"
        assert request.url.params["grant_type"] == "pkce"
        assert json.loads(request.content) == {"auth_code": "one-time-code", "code_verifier": "v" * 43}
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})
    client = AccountClient("https://project.test", "public-test", transport=httpx.MockTransport(handler))
    try:
        url = urlsplit(client.google_sign_in_url("http://127.0.0.1:8000/auth/callback?pa_state=test", "a" * 43))
        params = parse_qs(url.query)
        assert url.hostname == "project.test" and url.path == "/auth/v1/authorize"
        assert params["provider"] == ["google"]
        assert params["code_challenge_method"] == ["s256"]
        assert params["code_challenge"] == ["a" * 43]
        assert client.exchange_oauth_code("one-time-code", "v" * 43)["access_token"] == "access"
    finally:
        client.close()


def test_google_disabled_reports_required_configuration():
    client = AccountClient("https://project.test", "public-test", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"external": {"google": False}})))
    try:
        with pytest.raises(AccountError) as error:
            client.google_sign_in_url("http://127.0.0.1:8000/auth/callback", "a" * 43)
        assert error.value.reason == "google_not_configured"
    finally:
        client.close()


def test_runner_loopback_rejects_wrong_state_and_exchanges_correct_code(capsys):
    workers, failures = [], []
    class Client:
        def google_sign_in_url(self, callback, challenge):
            self.callback, self.challenge = callback, challenge
            return "https://project.test/auth/v1/authorize?" + urlencode({"redirect_to": callback})
        def exchange_oauth_code(self, code, verifier):
            assert code == "one-time-code"
            assert pkce_challenge(verifier) == self.challenge
            return {"refresh_token": "private-refresh"}
    client = Client()
    def open_browser(url):
        def respond():
            try:
                parsed = urlsplit(client.callback)
                base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                with httpx.Client(trust_env=False) as http:
                    invalid = http.get(base + "?pa_state=wrong&code=wrong", timeout=5)
                    assert invalid.status_code == 400
                    valid = http.get(client.callback + "&code=one-time-code", timeout=5)
                    assert valid.status_code == 200
                    assert "one-time-code" not in valid.text
            except BaseException as exc:
                failures.append(exc)
        worker = Thread(target=respond, daemon=True)
        workers.append(worker)
        worker.start()
    assert google_login(client, port=0, open_browser=open_browser, timeout=10) == {"refresh_token": "private-refresh"}
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert not failures
    output = capsys.readouterr().out
    assert "private-refresh" not in output and "one-time-code" not in output


def test_runner_default_login_uses_google_and_only_stores_refresh_token(monkeypatch, tmp_path):
    from pocket_agent.account import auth
    from pocket_agent.account import oauth
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    stored = []
    class Ring:
        def set_password(self, service, name, value): stored.append((name, value))
    class Client:
        def __init__(self, *args): pass
        def close(self): pass
    monkeypatch.setattr(auth, "AccountClient", Client)
    monkeypatch.setattr(auth, "_keyring", Ring)
    monkeypatch.setattr(oauth, "google_login", lambda _: {"refresh_token": "private-refresh"})
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Google login must not ask for email or password"))
    assert auth.login() == 0
    assert stored == [("refresh", "private-refresh")]
