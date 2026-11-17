"""The local app: loopback guards, the shared Keychain session, account API, and the Desk fill protocol."""
from __future__ import annotations

import os
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from account_fakes import EMAIL, USER, FakeAccount, install, ready_profile
from pocket_agent.forms.engine import FieldOutcome, FormRun, activity_source, review_counts
from pocket_agent.forms.memory import MemoryEntry
from pocket_agent.ui import app as ui
from pocket_agent.ui import desk as desk_module

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws"
SAME = {"Origin": BASE}
PROFILE = {"name": "Example Person", "education": [{"school": "Example University", "degree": "BS"}],
           "citizenship": {"authorized_to_work": ["US"], "requires_sponsorship": False},
           "skills": ["Python"], "preferences": {"roles": ["Engineer"]}}


class FakeContext:
    browser = SimpleNamespace(is_connected=lambda: True)

    def new_page(self):
        return SimpleNamespace(is_closed=lambda: False)


def make_client(monkeypatch, tmp_path, *, signed_in=True, profile=None):
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    state, ring = install(monkeypatch.setattr, signed_in=signed_in, profile=profile)
    monkeypatch.setattr(ui, "extract_resume", lambda _: {"profile": {"name": "Resume Person", "skills": ["Python"]},
        "warnings": [], "extracted_fields": ["name", "skills.0"], "parser": "pypdf + Ollama"})
    client = TestClient(ui.create_app(port=8000, launch=lambda _: FakeContext()), base_url=BASE, headers=SAME)
    client.state, client.ring = state, ring
    return client


@pytest.fixture
def client(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path) as test:
        test.bearer = {"Authorization": "Bearer " + test.post("/api/session").json()["access_token"]}
        yield test


def test_review_counts_keep_asks_out_of_the_filled_total() -> None:
    outcomes = [
        FieldOutcome("Email", "contact", 0, "email", "fill", "a@example.com", False, True),
        FieldOutcome("Salary", "questions", 0, "salary_expectation", "ask", "80000", True, False),
        FieldOutcome("Cover letter", "questions", 0, "cover_letter", "skip", "", False, False),
    ]
    assert review_counts(outcomes) == {"filled": 1, "asked": 1, "skipped": 1}


def test_activity_source_labels() -> None:
    memory = type("M", (), {})()
    memory.get = lambda key: MemoryEntry(key, "x", False, False, "resume") if key == "email" else None
    filled = FieldOutcome("Email", "", 0, "email", "fill", "x", False, True, False)
    modeled = FieldOutcome("City", "", 0, "city", "fill", "Austin", False, True, True)
    asked = FieldOutcome("Salary", "", 0, "salary_expectation", "ask", "1", True, False, False)
    assert activity_source(memory, filled) == "resume"  # type: ignore[arg-type]
    assert activity_source(memory, modeled) == "model"  # type: ignore[arg-type]
    assert activity_source(memory, asked) == "answer"  # type: ignore[arg-type]


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:9999", "attacker.test:8000", ""])
def test_other_host_names_are_rejected(client, host):
    # A DNS-rebinding page reaches 127.0.0.1 under its own name, so Host is the only tell.
    assert client.get("/", headers={"Host": host}).status_code == 403
    assert client.post("/api/session", headers={"Host": host, "Origin": "http://" + host}).status_code == 403
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(WS, headers={"Host": host, "Origin": "http://" + host}):
            pass
    assert closed.value.code == 1008


def test_localhost_name_is_accepted(client):
    assert client.get("/", headers={"Host": "localhost:8000"}).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9999", "null", None])
def test_cross_site_websocket_is_rejected(client, origin):
    headers = {"Origin": origin} if origin else {}
    if origin is None:
        client.headers.pop("Origin")
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(WS, headers=headers):
            pass
    assert closed.value.code == 1008


def test_session_and_writes_require_same_origin(client):
    client.headers.pop("Origin")
    assert client.post("/api/session").status_code == 403
    assert client.post("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/session", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.delete("/api/account", headers={**client.bearer, "Origin": "https://evil.example"}).status_code == 403
    assert client.state.logouts == 0 and client.state.wiped is False


def test_exchange_saves_the_refresh_token_to_keychain_and_never_returns_it(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path, signed_in=False) as client:
        assert client.post("/api/session").status_code == 401
        response = client.post("/api/auth/google/exchange", json={"code": "one-time-code", "verifier": "v" * 43})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"access_token", "expires_in", "email"} and body["email"] == EMAIL
        assert "refresh" not in response.text
        assert client.ring.entries["refresh"] in client.state.refresh
        assert "supabase_url: https://project.test" in (tmp_path / "config.yaml").read_text()
        assert client.get("/api/account", headers={"Authorization": "Bearer " + body["access_token"]}).status_code == 200


def test_reload_restores_the_session_from_keychain(client):
    first = client.ring.entries["refresh"]
    restored = client.post("/api/session")
    assert restored.status_code == 200
    assert set(restored.json()) == {"access_token"}
    assert client.ring.entries["refresh"] != first  # Supabase rotates refresh tokens on every use.
    headers = {"Authorization": "Bearer " + restored.json()["access_token"]}
    assert client.get("/api/account", headers=headers).json()["user"]["email"] == EMAIL


def test_sign_out_revokes_and_clears_keychain(client):
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.state.logouts == 1
    assert client.ring.entries == {}
    assert client.post("/api/session").status_code == 401


def test_google_start_returns_to_this_origin(client):
    response = client.post("/api/auth/google/start", json={"challenge": "a" * 43, "state": "b" * 43})
    redirect = parse_qs(urlsplit(response.json()["url"]).query)["redirect_to"]
    assert redirect == [BASE + "/auth/callback?pa_state=" + "b" * 43]
    assert "Continue with Google" in client.get("/auth/callback").text
    assert client.post("/api/auth/google/start", json={"challenge": "bad", "state": "bad"}).status_code == 422
    assert client.post("/api/auth/google/exchange", json={"code": "one-time-code", "verifier": "bad"}).status_code == 422


def test_account_api_requires_a_verified_bearer(client):
    assert client.get("/api/account").status_code == 401
    response = client.get("/api/account", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401 and "secret" not in response.text
    assert client.get("/api/account", headers=client.bearer).json()["user"]["id"] == USER


@pytest.mark.parametrize("path", ["login", "signup", "verify", "link", "code"])
def test_password_and_email_sign_in_endpoints_are_gone(client, path):
    # Password sign-up with auto-confirmed email let anyone pre-register someone else's address
    # and inherit their later Google sign-in through Supabase identity linking.
    assert client.post("/api/auth/" + path, json={"email": "test@example.com", "password": "x"}).status_code in {404, 405}
    assert "/api/auth/" + path not in client.get("/app.js").text


def test_old_local_desk_endpoints_are_gone(client):
    for path in ("/api/settings", "/api/memory", "/api/profile", "/api/resume", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_safe_diagnostic_reaches_browser(client, monkeypatch):
    from pocket_agent.account.client import AccountError

    def fail(self, code, verifier):
        raise AccountError("secret upstream response", 500, reason="auth_service_error")
    monkeypatch.setattr(FakeAccount, "exchange_oauth_code", fail)
    response = client.post("/api/auth/google/exchange", json={"code": "one-time-code", "verifier": "v" * 43})
    assert response.status_code == 502
    assert "Authentication logs" in response.json()["detail"] and "secret" not in response.text
    assert client.get("/favicon.ico").status_code == 204


def test_pages_do_not_cache_or_expose_tokens(client):
    page = client.get("/")
    assert page.headers["Cache-Control"] == "no-store"
    policy = page.headers["Content-Security-Policy"]
    assert "script-src 'self';" in policy and "frame-ancestors 'none'" in policy and "ws://127.0.0.1:8000" in policy
    assert "click Submit yourself" in page.text and "terminal" not in page.text
    assert 'id="googleLogin"' in page.text and 'id="fill"' in page.text and "<script>" not in page.text
    script = client.get("/app.js").text
    assert "innerHTML" not in script
    assert script.count("localStorage.setItem(") == 1 and "localStorage.setItem('pocketModel'" in script


def test_profile_conflicts_and_validation(client):
    response = client.put("/api/account/profile", headers=client.bearer, json={"data": deepcopy(PROFILE), "expected_revision": 0})
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert client.put("/api/account/profile", headers=client.bearer, json={"data": deepcopy(PROFILE), "expected_revision": 0}).status_code == 409
    for name, value in [("skills", 7), ("preferences", []), ("location", []), ("experience", ["bad"]), ("form_answers", {})]:
        data = deepcopy(PROFILE)
        data[name] = value
        assert client.put("/api/account/profile", headers=client.bearer, json={"data": data, "expected_revision": 1}).status_code == 422
    for path in (f"{uuid4()}/{uuid4()}.pdf", f"{USER}/../other.pdf", f"{USER}/resume.pdf", f"{USER}/{uuid4()}.pdf/extra", 42):
        data = {**PROFILE, "resume_object": path}
        assert client.put("/api/account/profile", headers=client.bearer, json={"data": data, "expected_revision": 1}).status_code == 422
    data = {**PROFILE, "resume_object": f"{USER}/{uuid4()}.pdf"}
    assert client.put("/api/account/profile", headers=client.bearer, json={"data": data, "expected_revision": 1}).status_code == 200


def test_resume_upload_limits_and_first_upload_draft(client):
    files = lambda content: {"file": ("resume.pdf", content, "application/pdf")}
    assert client.post("/api/account/resume", headers=client.bearer, files=files(b"not a pdf")).status_code == 422
    assert client.post("/api/account/resume", headers=client.bearer, files=files(b"%PDF-" + b"x" * ui.MAX_RESUME)).status_code == 413
    response = client.post("/api/account/resume", headers=client.bearer, files=files(b"%PDF-fictional"))
    data = response.json()
    assert data["profile"]["revision"] == 1 and data["profile"]["data"]["name"] == "Resume Person"
    assert data["profile"]["data"]["resume_object"] == data["object_path"] and data["object_path"].startswith(USER + "/")
    assert "citizenship.requires_sponsorship" in data["missing_fields"]
    conflict = client.post("/api/account/resume", headers=client.bearer, data={"expected_revision": "7"}, files=files(b"%PDF-x"))
    assert conflict.status_code == 409


def test_replacing_resume_deletes_the_old_pdf_and_failed_save_deletes_the_new_one(client):
    old = f"{USER}/{uuid4()}.pdf"
    client.state.profile = {"data": {"name": "Resume Person", "resume_object": old}, "revision": 3}
    files = {"file": ("resume.pdf", b"%PDF-new", "application/pdf")}
    assert client.post("/api/account/resume", headers=client.bearer, data={"expected_revision": "3"}, files=files).status_code == 200
    assert client.state.deleted_resumes == [old]
    client.state.fail_save = True
    assert client.post("/api/account/resume", headers=client.bearer, data={"expected_revision": "4"}, files=files).status_code == 409
    assert len(client.state.deleted_resumes) == 2 and client.state.deleted_resumes[1].startswith(USER + "/")


def test_answers_require_known_keys_and_are_always_reused(client):
    assert client.put("/api/account/answers/not_a_key", headers=client.bearer, json={"value": "x"}).status_code == 422
    assert client.put("/api/account/answers/gender", headers=client.bearer, json={"value": "Prefer not to say", "label": "Gender"}).status_code == 200
    assert client.state.saved_answers[-1] == ("gender", "decline", True, "Gender")
    assert client.put("/api/account/answers/gender", headers=client.bearer, json={"value": "x", "always_use": False}).status_code == 422
    client.state.answers.append({"key": "learned_key", "value": "old", "label": "Learned"})
    assert client.put("/api/account/answers/learned_key", headers=client.bearer, json={"value": "new"}).status_code == 200
    assert client.delete("/api/account/answers/learned_key", headers=client.bearer).status_code == 200
    # A cleared learned q.* answer can be typed again even after it leaves the snapshot.
    assert client.put("/api/account/answers/q.abcdef123456", headers=client.bearer, json={"value": "again", "label": "Explain your answer"}).status_code == 200


def test_delete_my_data_requires_sign_in(client):
    assert client.delete("/api/account").status_code == 401
    assert client.delete("/api/account", headers=client.bearer).json() == {"ok": True}
    assert client.state.wiped is True


def scripted_fill(asks, seen):
    """Stands in for the engine: asks each question, stores answers the way the engine does, reaches Review."""

    def fill_form(page, url, memory, resume, ask, decider, **options):
        seen.append({"url": url, "resume": resume.read_bytes()[:5], "env": dict(os.environ), "options": options})
        for asked in asks:
            answer = ask(asked)
            if not answer.skip:
                memory.put(asked.key, answer.value, always_use=answer.always_use, sensitive=asked.sensitive,
                           source="answer", label=asked.label)
        return FormRun(url=url, status="review")

    return fill_form


def receive_until(socket, kind):
    events = []
    while not events or events[-1]["type"] != kind:
        events.append(socket.receive_json())
    return events


def test_desk_fill_asks_inline_syncs_answers_and_records_the_run(monkeypatch, tmp_path):
    from pocket_agent.forms.engine import Asked

    seen = []
    asks = [Asked("referrer_name", "Referrer name", "Who referred you?"),
            Asked("gender", "Gender", "Gender?", kind="dropdown", options=("Woman", "Man"), sensitive=True)]
    monkeypatch.setattr(desk_module, "fill_form", scripted_fill(asks, seen))
    with make_client(monkeypatch, tmp_path, profile=ready_profile()) as client:
        env_before = dict(os.environ)
        with client.websocket_connect(WS) as socket:
            socket.send_json({"type": "answer", "value": "early"})
            assert socket.receive_json() == {"type": "error", "message": "No question is waiting."}
            socket.send_json({"type": "start", "url": "https://jobs.example.test/apply", "model": "none"})
            ask = receive_until(socket, "ask")[-1]
            assert ask.pop("question_id")
            assert ask == {"type": "ask", "key": "referrer_name", "label": "Referrer name", "question": "Who referred you?",
                           "kind": "text", "options": [], "sensitive": False}

            socket.send_json({"type": "start", "url": "https://jobs.example.test/other"})
            assert socket.receive_json()["message"].startswith("A fill is already running")
            with client.websocket_connect(WS) as reloaded:
                again = receive_until(reloaded, "ask")[-1]
                again.pop("question_id")
                assert again == ask  # A reloaded tab sees the open question.

            socket.send_json({"type": "answer", "value": "Sam Rivera"})
            sensitive = receive_until(socket, "ask")[-1]
            assert sensitive["sensitive"] is True and sensitive["options"] == ["Woman", "Man"]
            socket.send_json({"type": "answer", "value": "Woman"})
            assert receive_until(socket, "done")[-1] == {"type": "done", "status": "review", "unresolved": []}
        assert dict(os.environ) == env_before
    assert seen[0]["env"] == env_before  # The fill ran without moving POCKET_AGENT_HOME for the server.
    assert seen[0]["resume"] == b"%PDF-" and seen[0]["options"]["navigate"] is True
    assert [r[1:3] for r in client.state.records] == [("https://jobs.example.test/apply", "review")]
    # A sensitive answer is saved only when the ask card's checkbox is on. This client left it off.
    assert sorted(client.state.saved_answers) == [("referrer_name", "Sam Rivera", False, "referrer name")]


def test_desk_rejects_bad_input_and_reports_account_errors_as_done(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_module, "fill_form", scripted_fill([], []))
    with make_client(monkeypatch, tmp_path, profile=None) as client:
        with client.websocket_connect(WS) as socket:
            socket.send_json({"type": "launch-missiles"})
            assert socket.receive_json() == {"type": "error", "message": "Unknown message."}
            socket.send_json({"type": "start", "url": "http://jobs.example.test/apply"})
            events = receive_until(socket, "done")
            assert events[-2] == {"type": "error", "message": "Account fills require an HTTPS job application URL."}
            assert events[-1]["status"] == "stopped"
            socket.send_json({"type": "start", "url": "https://jobs.example.test/apply"})
            events = receive_until(socket, "done")
            assert "Upload your resume" in events[-2]["message"]
        assert client.state.records == []


def test_cli_account_fill_runs_through_the_shared_lifecycle(monkeypatch, tmp_path):
    from pocket_agent import cli

    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    state, _ = install(monkeypatch.setattr, signed_in=True, profile=ready_profile())
    env_before, seen = dict(os.environ), []

    def fill(args, home, account):
        seen.append(home)
        assert home != tmp_path and (home.stat().st_mode & 0o777) == 0o700
        assert account.resume.read_bytes().startswith(b"%PDF-") and (home / "profile.yaml").exists()
        assert dict(os.environ) == env_before
        account.status, account.unresolved = "review", ["Start date"]
        return 0

    monkeypatch.setattr(cli, "_fill", fill)
    assert cli.main(["fill", "https://jobs.example.test/apply", "--account", "--model", "none"]) == 0
    assert not seen[0].exists()
    assert [r[1:] for r in state.records] == [("https://jobs.example.test/apply", "review", ["Start date"])]
    assert (tmp_path / "account-session.lock").exists()  # The Keychain lock stayed in the real home.


def test_ai_keys_are_saved_to_keychain_and_never_returned(client, monkeypatch, tmp_path):
    from pocket_agent.forms.cloud import CloudBackend
    from pocket_agent.ai_settings import model

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("POCKET_AGENT_AI_MODEL", raising=False)
    assert client.get("/api/ai").json()["gpt"] == {"key_saved": False, "key_from_env": False, "model": "", "model_from_env": False}
    saved = client.put("/api/ai/gpt", json={"api_key": "sk-test-0123456789", "model": "provider-model-1"})
    assert saved.status_code == 200 and "sk-test" not in saved.text
    assert saved.json()["gpt"]["key_saved"] is True and saved.json()["gpt"]["model"] == "provider-model-1"
    assert client.ring.entries["gpt"] == "sk-test-0123456789"
    assert "sk-test" not in (tmp_path / "config.yaml").read_text()
    # The terminal and the Desk read the same settings.
    assert CloudBackend("gpt").key == "sk-test-0123456789" and model("gpt") == "provider-model-1"
    # Saving without a key keeps the stored one.
    assert client.put("/api/ai/gpt", json={"model": "provider-model-2"}).json()["gpt"]["key_saved"] is True
    assert client.put("/api/ai/gpt", json={"api_key": "short", "model": "m"}).status_code == 422
    assert client.put("/api/ai/gemini", json={"model": "m"}).status_code == 422
    assert client.put("/api/ai/gpt", json={"api_key": "sk-evil-0123456789", "model": "m"},
                      headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.delete("/api/ai/gpt").json()["gpt"]["key_saved"] is False
    assert "gpt" not in client.ring.entries


def test_environment_keys_still_win(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-0123456789")
    monkeypatch.setenv("POCKET_AGENT_AI_MODEL", "env-model")
    status = client.get("/api/ai").json()["claude"]
    assert status["key_from_env"] is True and status["model"] == "env-model" and status["model_from_env"] is True
    assert "env-key" not in client.get("/api/ai").text
