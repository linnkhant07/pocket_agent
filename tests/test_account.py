from argparse import Namespace
from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from pocket_agent.account.client import AccountClient, AccountError
from pocket_agent.account.runner import run_account_fill, snapshot_profile
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.seed import load_example_profile, seed_memory

UID = "11111111-1111-4111-8111-111111111111"
OBJECT = f"{UID}/{uuid4()}.pdf"


def snapshot():
    profile = load_example_profile()
    profile["resume_object"] = OBJECT
    profile["form_answers"] = {"gender": {"value": "must not leak", "always_use": True}}
    return {"profile": {"data": profile, "revision": 1}, "answers": [
        {"key": "gender", "value": "private", "always_use": False},
        {"key": "race", "value": "Decline", "always_use": True},
        {"key": "phone", "value": "corrected", "always_use": False},
    ]}


def test_snapshot_sensitive_consent_and_canonical_precedence(tmp_path):
    source = snapshot()
    before = deepcopy(source)
    profile, reference = snapshot_profile(source)
    assert reference == OBJECT
    assert source == before
    memory = Memory(tmp_path / "memory.sqlite")
    try:
        seed_memory(memory, profile)
        assert memory.get("gender") is None
        assert memory.get("race").always_use
        assert memory.get("phone").value == "corrected"
    finally:
        memory.close()


def test_restored_answer_keeps_its_label_so_the_next_fill_reuses_it(tmp_path):
    from pocket_agent.forms.engine import stored_value
    from pocket_agent.forms.match import match_label

    label = "Have you previously applied to Amazon or any Amazon subsidiary?"
    key = "q.have_you_previously_applied_to_amazon_or_any_amazon_subsidiary"
    source = snapshot()
    source["answers"].append({"key": key, "value": "No", "always_use": False, "label": label.casefold()})
    profile, _ = snapshot_profile(source)
    memory = Memory(tmp_path / "memory.sqlite")
    try:
        seed_memory(memory, profile)
        match = match_label(label, learned=memory.learned_labels())
        assert match is not None and match.key == key and match.score >= 0.93
        assert stored_value(memory, match.key, label) == "No"
    finally:
        memory.close()


class FakeClient:
    def __init__(self):
        self.closed = False
        self.records = []
        self.saved_answers = []

    def snapshot(self):
        return snapshot()

    def download_resume(self, reference):
        assert reference == OBJECT
        return b"%PDF-test"

    def record_run(self, *record):
        self.records.append(record)

    def save_answer(self, key, value, always_use, label=""):
        self.saved_answers.append((key, value, always_use))

    def close(self):
        self.closed = True


def args():
    return Namespace(url="https://example.test/jobs/1", headless=False, resume=None, unattended=None, account=True)


@pytest.mark.parametrize("fails", [False, True])
def test_run_isolates_and_cleans_files_even_on_failure(tmp_path, monkeypatch, fails):
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    (tmp_path / "forms.sqlite").write_text("local secret")
    client = FakeClient()
    paths = []

    def fill(options, home, account):
        paths.append(home)
        assert home != tmp_path
        assert (home.stat().st_mode & 0o777) == 0o700
        assert not (home / "forms.sqlite").exists()
        assert account.resume.read_bytes() == b"%PDF-test"
        if fails:
            raise RuntimeError("browser crashed")
        account.status, account.unresolved = "review", ["Start date"]
        return 0

    if fails:
        with pytest.raises(RuntimeError, match="browser crashed"):
            run_account_fill(args(), fill, client=client)
    else:
        assert run_account_fill(args(), fill, client=client) == 0
    assert not paths[0].exists()
    assert os.environ["POCKET_AGENT_HOME"] == str(tmp_path)
    assert (tmp_path / "forms.sqlite").read_text() == "local secret"
    assert client.closed
    assert client.records[0][2] == ("stopped" if fails else "review")


def test_network_failure_never_opens_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    client = FakeClient()
    def fail():
        raise AccountError("offline")
    client.snapshot = fail
    with pytest.raises(AccountError, match="offline"):
        run_account_fill(args(), lambda *_: pytest.fail("must not launch"), client=client)
    assert client.closed and not client.records
    assert os.environ["POCKET_AGENT_HOME"] == str(tmp_path)


def test_history_failure_reports_incomplete_sync(capsys):
    client = FakeClient()
    def fail(*args):
        raise AccountError("offline")
    client.record_run = fail
    assert run_account_fill(args(), lambda *_: 0, client=client) == 0
    assert "history could not be saved" in capsys.readouterr().out
    assert client.closed


def test_transport_uses_user_bearer_and_scoped_queries():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["apikey"] == "public-test"
        assert request.headers["authorization"] == "Bearer user-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": UID})
        return httpx.Response(200, json=[])
    client = AccountClient("https://project.test", "public-test", "user-token", transport=httpx.MockTransport(handler))
    try:
        assert client.snapshot() == {"profile": None, "answers": [], "runs": []}
        for request in requests[1:]:
            assert request.url.params["user_id"] == f"eq.{UID}"
    finally:
        client.close()


def test_transport_errors_do_not_expose_response_or_credentials():
    client = AccountClient("https://project.test", "public-test", "secret-token",
                           transport=httpx.MockTransport(lambda _: httpx.Response(403, text="secret PII")))
    try:
        with pytest.raises(AccountError) as error:
            client.user()
        assert "secret" not in str(error.value)
        assert error.value.status_code == 403
    finally:
        client.close()


@pytest.mark.parametrize("status,payload,reason", [
    (500, {"error_code": "unexpected_failure", "msg": "secret SMTP contents"}, "auth_service_error"),
    (429, {"error_code": "over_request_rate_limit", "msg": "secret@example.com"}, "over_request_rate_limit"),
    (422, {"error_code": "signup_disabled"}, "signup_disabled"),
    (401, {"message": "secret key rejected"}, "invalid_project_key"),
    (500, {"error_code": ["malformed"], "msg": "secret"}, "auth_service_error"),
])
def test_auth_errors_have_safe_actionable_reasons(status, payload, reason, caplog):
    client = AccountClient("https://project.test", "public-test", transport=httpx.MockTransport(
        lambda _: httpx.Response(status, json=payload)))
    try:
        with pytest.raises(AccountError) as error:
            client._request("GET", "/auth/v1/settings")
        assert error.value.reason == reason
        assert "secret" not in str(error.value)
        assert "secret" not in caplog.text
        assert str(status) in caplog.text
    finally:
        client.close()


@pytest.mark.parametrize("path", ["../resume.pdf", f"{UID}/../../secret", "https://attacker.test/resume.pdf", f"{uuid4()}/{uuid4()}.pdf"])
def test_foreign_resume_reference_rejected_before_storage_request(path):
    def handler(request):
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={"id": UID})
    client = AccountClient("https://project.test", "public-test", "token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AccountError, match="invalid"):
            client.download_resume(path)
    finally:
        client.close()


def test_service_role_key_rejected():
    import base64
    payload = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    with pytest.raises(AccountError):
        AccountClient("https://project.test", f"header.{payload}.sig")


@pytest.mark.parametrize("field,value", [("experience", [{"highlights": "bad"}]),
    ("skills", {"languages": "Python"}), ("citizenship", {}), ("location", {"remote_ok": "no"})])
def test_runner_revalidates_profiles_written_outside_portal(field, value):
    from pocket_agent.profile import ProfileError
    source = snapshot()
    source["profile"]["data"][field] = value
    with pytest.raises(AccountError if field == "citizenship" else ProfileError):
        snapshot_profile(source)


def test_auth_rotates_and_verifies_using_keychain(monkeypatch, tmp_path):
    from pocket_agent.account import auth
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    class Ring:
        token = "old-refresh"
        def get_password(self, service, name):
            return self.token
        def set_password(self, service, name, token):
            self.token = token
    ring = Ring()
    monkeypatch.setattr(auth, "_keyring", lambda: ring)
    class Client:
        def __init__(self, url, key, access=""):
            self.access = access
        def refresh(self, token):
            assert token == "old-refresh"
            return {"access_token": "access", "refresh_token": "rotated"}
        def user(self):
            assert self.access == "access"
            return {"id": UID}
        def close(self):
            pass
    monkeypatch.setattr(auth, "AccountClient", Client)
    assert auth.signed_in_client().user()["id"] == UID
    assert ring.token == "rotated"
    assert not any("refresh" in path.read_text() for path in tmp_path.iterdir())


def test_answers_typed_during_a_fill_are_saved_to_the_account(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    client = FakeClient()

    def fill(options, home, account):
        memory = Memory(home / "forms.sqlite")
        try:
            seed_memory(memory, load_example_profile())  # derived facts must never upload
            memory.put("referrer_name", "Sam Rivera", source="answer", label="Referrer name")
            memory.put("veteran_status", "Not a veteran", sensitive=True, always_use=False, source="answer")
            memory.put("disability_status", "No", sensitive=True, always_use=True, source="answer")
            memory.put("race", "Decline", sensitive=True, always_use=True, source="answer")  # unchanged
        finally:
            memory.close()
        account.status = "review"
        return 0

    assert run_account_fill(args(), fill, client=client) == 0
    assert sorted(client.saved_answers) == [("disability_status", "No", True), ("referrer_name", "Sam Rivera", False)]
    assert "Saved 2 new answer(s)" in capsys.readouterr().out


def test_answer_sync_failure_does_not_fail_the_fill(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    client = FakeClient()
    def broken(*_):
        raise AccountError("offline")
    client.save_answer = broken

    def fill(options, home, account):
        memory = Memory(home / "forms.sqlite")
        memory.put("referrer_name", "Sam", source="answer")
        memory.close()
        account.status = "review"
        return 0

    assert run_account_fill(args(), fill, client=client) == 0
    assert "could not be saved" in capsys.readouterr().out


def test_delete_account_data_removes_own_resumes_and_rows():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": UID})
        if request.url.path == "/storage/v1/object/list/resumes":
            assert json.loads(request.content)["prefix"] == UID
            return httpx.Response(200, json=[{"name": OBJECT.split("/")[1]}, {"name": "../escape.pdf"}])
        return httpx.Response(200, json=[])
    client = AccountClient("https://project.test", "public-test", "user-token", transport=httpx.MockTransport(handler))
    try:
        client.delete_account_data()
    finally:
        client.close()
    deletes = [(path, params) for method, path, params in calls if method == "DELETE"]
    assert deletes[0][0] == f"/storage/v1/object/resumes/{OBJECT}"
    assert len([d for d in deletes if d[0].startswith("/storage/")]) == 1  # the traversal name is refused
    assert {path for path, _ in deletes[1:]} == {"/rest/v1/account_answers", "/rest/v1/account_runs", "/rest/v1/account_profiles"}
    assert all(params == {"user_id": f"eq.{UID}"} for _, params in deletes[1:])


@pytest.mark.parametrize("path", ["../resume.pdf", f"{uuid4()}/{uuid4()}.pdf", f"{UID}/resume.pdf"])
def test_delete_resume_refuses_foreign_paths(path):
    client = AccountClient("https://project.test", "public-test", "user-token", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"id": UID}) if request.url.path == "/auth/v1/user" else pytest.fail("must not delete")))
    try:
        with pytest.raises(AccountError):
            client.delete_resume(path)
    finally:
        client.close()


def test_login_remembers_public_project_settings_for_later_commands(monkeypatch, tmp_path):
    from pocket_agent.account import auth
    from pocket_agent.account.client import settings
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("backend: ollama\n")
    auth._remember_project("https://project.test", "public-test")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert settings() == ("https://project.test", "public-test")
    assert "backend: ollama" in (tmp_path / "config.yaml").read_text()
    monkeypatch.setenv("SUPABASE_URL", "https://other.test")
    assert settings()[0] == "https://other.test"


@pytest.mark.parametrize("stored", [None, "revoked"])
def test_first_use_signs_in_once_then_reuses_the_session(monkeypatch, tmp_path, capsys, stored):
    from pocket_agent.account import auth
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")

    class Ring:
        token = stored
        def get_password(self, service, name): return Ring.token
        def set_password(self, service, name, token): Ring.token = token
        def delete_password(self, service, name): Ring.token = None

    class Client:
        def __init__(self, url, key, access=""): self.access = access
        def refresh(self, token):
            if token == "revoked":
                raise AccountError("invalid refresh token", 400)
            return {"access_token": "access", "refresh_token": "rotated"}
        def user(self): return {"id": UID}
        def close(self): pass

    logins = []
    def login():
        logins.append(1)
        Ring.token = "fresh"
        return 0

    monkeypatch.setattr(auth, "_keyring", Ring)
    monkeypatch.setattr(auth, "AccountClient", Client)
    monkeypatch.setattr(auth, "login", login)
    assert auth.signed_in_client(sign_in_if_needed=True).user()["id"] == UID
    assert logins == [1] and Ring.token == "rotated"
    assert "Choose your Google account" in capsys.readouterr().out
    auth.signed_in_client(sign_in_if_needed=True)
    assert logins == [1]  # Signed in once; the next command reuses the stored session.


def test_non_interactive_use_never_opens_a_browser(monkeypatch, tmp_path):
    from pocket_agent.account import auth
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    class Ring:
        def get_password(self, service, name): return None
    monkeypatch.setattr(auth, "_keyring", Ring)
    monkeypatch.setattr(auth, "login", lambda: pytest.fail("must not open sign-in"))
    with pytest.raises(AccountError, match="login"):
        auth.signed_in_client(sign_in_if_needed=False)


def test_project_settings_fall_back_to_dotenv_in_current_folder(monkeypatch, tmp_path):
    from pocket_agent.account.client import settings
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    (tmp_path / ".env").write_text("OTHER_SECRET=x\nSUPABASE_URL=\"https://project.test\"\nexport SUPABASE_ANON_KEY='public-test'\n")
    monkeypatch.chdir(tmp_path)
    assert settings() == ("https://project.test", "public-test")
