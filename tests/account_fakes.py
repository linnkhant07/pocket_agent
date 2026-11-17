"""In-memory Supabase account and Keychain for driving the real local app without a network.

pytest and the verify-pocket-agent skill both install these, so they exercise the same seams.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from pocket_agent.account.client import AccountError
from pocket_agent.forms.seed import load_example_profile
from pocket_agent.paths import examples_dir

USER = "11111111-1111-4111-8111-111111111111"
EMAIL = "jordan.hale@example.com"
FORMS_ORIGIN = "https://forms.pocket.test"


class FakeKeychain:
    """One project's entries, keyed by name. The real service name is a hash of the project URL."""

    def __init__(self, token: str | None = None) -> None:
        self.entries: dict[str, str] = {"refresh": token} if token else {}

    def get_password(self, service, name):
        return self.entries.get(name)

    def set_password(self, service, name, value):
        self.entries[name] = value

    def delete_password(self, service, name):
        del self.entries[name]


@dataclass
class AccountState:
    """What Supabase would hold for one user. Every FakeAccount instance shares it."""
    profile: dict | None = None
    answers: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    access: set[str] = field(default_factory=set)
    refresh: set[str] = field(default_factory=set)
    records: list[tuple] = field(default_factory=list)
    saved_answers: list[tuple] = field(default_factory=list)
    deleted_resumes: list[str] = field(default_factory=list)
    logouts: int = 0
    wiped: bool = False
    fail_save: bool = False
    tokens: count = field(default_factory=lambda: count(1))

    def session(self) -> dict:
        n = next(self.tokens)
        self.access.add(f"access-{n}")
        self.refresh.add(f"refresh-{n}")
        return {"access_token": f"access-{n}", "refresh_token": f"refresh-{n}", "expires_in": 3600,
                "user": {"id": USER, "email": EMAIL}}


def ready_profile() -> dict:
    """A complete account profile with an uploaded resume, so fills can start."""
    data = load_example_profile()
    data.pop("form_answers", None)
    data["resume_object"] = f"{USER}/{uuid4()}.pdf"
    return {"data": data, "revision": 1}


class FakeAccount:
    state = AccountState()

    def __init__(self, url, key, token="", **_):
        self.token = token

    def close(self):
        pass

    def user(self):
        if self.token not in self.state.access:
            raise AccountError("secret remote error", status_code=401)
        return {"id": USER, "email": EMAIL}

    def snapshot(self):
        self.user()
        return {"profile": self.state.profile, "answers": list(self.state.answers), "runs": list(self.state.runs)}

    def google_sign_in_url(self, callback, challenge):
        return "https://project.test/auth/v1/authorize?" + urlencode(
            {"provider": "google", "redirect_to": callback, "code_challenge": challenge})

    def exchange_oauth_code(self, code, verifier):
        return self.state.session()

    def refresh(self, token):
        if token not in self.state.refresh:
            raise AccountError("revoked", status_code=401)
        self.state.refresh.discard(token)
        return self.state.session()

    def logout(self):
        self.user()
        self.state.logouts += 1
        self.state.access.clear()
        self.state.refresh.clear()

    def save_profile(self, data, expected_revision):
        current = self.state.profile["revision"] if self.state.profile else 0
        if expected_revision != current or self.state.fail_save:
            raise AccountError("conflict", status_code=409)
        self.state.profile = {"data": data, "revision": current + 1}
        return self.state.profile

    def save_answer(self, key, value, always_use, label=""):
        self.user()
        self.state.saved_answers.append((key, value, always_use, label))
        self.state.answers = [a for a in self.state.answers if a["key"] != key]
        self.state.answers.append({"key": key, "value": value, "always_use": always_use, "label": label})

    def delete_answer(self, key):
        self.state.answers = [a for a in self.state.answers if a["key"] != key]

    def upload_resume(self, content):
        return f"{USER}/{uuid4()}.pdf"

    def delete_resume(self, path):
        self.state.deleted_resumes.append(path)

    def delete_account_data(self):
        self.state.wiped = True

    def download_resume(self, object_path):
        self.user()
        return (examples_dir() / "resume.pdf").read_bytes()

    def record_run(self, run_id, url, status, unresolved):
        self.user()
        self.state.records.append((run_id, url, status, unresolved))
        self.state.runs.insert(0, {"id": run_id, "url": url, "status": status, "unresolved": unresolved,
                                   "created_at": "2026-11-16T12:00:00Z"})


def install(patch, *, signed_in: bool, profile: dict | None = None) -> tuple[AccountState, FakeKeychain]:
    """Swap the Supabase client and Keychain. patch is monkeypatch.setattr in tests, setattr in scripts."""
    from pocket_agent.account import auth
    from pocket_agent.ui import app

    state = AccountState(profile=profile)
    patch(FakeAccount, "state", state)
    ring = FakeKeychain(state.session()["refresh_token"] if signed_in else None)
    patch(auth, "_keyring", lambda: ring)
    patch(auth, "AccountClient", FakeAccount)
    patch(app, "AccountClient", FakeAccount)
    return state, ring


def https_forms(forms_base: str, *, headless: bool, debug_port: int | None = None):
    """A Desk browser launcher that serves the local fictional forms at an HTTPS origin, as account fills require.

    debug_port lets a checker attach over CDP and read the filler's page from another process or thread.
    """

    def launch(playwright):
        args = [f"--remote-debugging-port={debug_port}"] if debug_port else []
        context = playwright.chromium.launch(headless=headless, args=args).new_context()

        def serve(route):
            target = urlsplit(route.request.url)
            response = route.fetch(url=forms_base + target.path + ("?" + target.query if target.query else ""))
            route.fulfill(response=response)

        context.route(FORMS_ORIGIN + "/**", serve)
        return context

    return launch
