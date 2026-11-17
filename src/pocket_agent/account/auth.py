"""The account session lives only in macOS Keychain, scoped to the project.

The terminal and the local app share it. The advisory lock serializes rotating refresh
tokens across local processes and server threads. Only the refresh token is saved.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import sys
from pathlib import Path

import yaml

from pocket_agent.account.client import AccountClient, AccountError, settings
from pocket_agent.config import config_path
from pocket_agent.paths import data_home


def _keyring():
    try:
        from keyring.backends.macOS import Keyring
        return Keyring()
    except (ImportError, RuntimeError):
        raise AccountError("Account login requires macOS Keychain. Install pocket-agent[accounts].", 503) from None


def _service(url: str) -> str:
    return "pocket-agent:supabase:" + hashlib.sha256(url.rstrip("/").encode()).hexdigest()


@contextmanager
def _lock():
    import fcntl
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / "account-session.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def login() -> int:
    url, key = settings()
    ring = _keyring()
    client = AccountClient(url, key)
    try:
        # Google only: password accounts could be pre-registered under someone else's email.
        from pocket_agent.account.oauth import google_login
        store_session(ring, url, key, google_login(client))
        print("Signed in on this computer.")
        return 0
    finally:
        client.close()


def store_session(ring, url: str, key: str, session: dict) -> None:
    """Keep only the refresh token. The terminal and `pocket-agent ui` share this one Keychain entry."""
    token = session.get("refresh_token")
    if not isinstance(token, str) or not token:
        raise AccountError("Sign-in did not return a session.")
    with _lock():
        ring.set_password(_service(url), "refresh", token)
    _remember_project(url, key)


def _remember_project(url: str, key: str) -> None:
    # Only the public project URL and anon key; later commands then work without exporting them.
    path = config_path()
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    data["account"] = {"supabase_url": url, "supabase_anon_key": key}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _access_token(ring, url: str, key: str) -> str | None:
    """Exchange the stored refresh token. None means this computer must sign in (again)."""
    with _lock():
        token = ring.get_password(_service(url), "refresh")
        if not token:
            return None
        client = AccountClient(url, key)
        try:
            session = client.refresh(token)
        except AccountError as error:
            if error.status_code not in (400, 401):
                raise
            code = error.provider_code or ""
            # A wrong anon key is not a revoked session. Leave the refresh token where it is.
            if code in {"invalid_api_key", "invalid_project_key"} or "api key" in str(error).casefold():
                raise
            if code and code not in {"refresh_token_not_found", "invalid_grant", "session_not_found"}:
                raise
            ring.delete_password(_service(url), "refresh")  # Revoked or expired; sign in again.
            return None
        finally:
            client.close()
        access, refresh = session.get("access_token"), session.get("refresh_token")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise AccountError("Sign in again; account session is invalid.", 401)
        ring.set_password(_service(url), "refresh", refresh)
        return access


def signed_in_client(*, sign_in_if_needed: bool | None = None) -> AccountClient:
    """A verified client for this computer. In a terminal, the first use opens Google sign-in once."""
    url, key = settings()
    if not url or not key:
        # The Keychain entry is keyed by project URL, so without it "not signed in" would be misleading.
        raise AccountError("No account project is configured. Run this from the folder with your .env file, or export SUPABASE_URL and SUPABASE_ANON_KEY.", 503)
    ring = _keyring()
    access = _access_token(ring, url, key)
    if access is None:
        if not (sys.stdin.isatty() if sign_in_if_needed is None else sign_in_if_needed):
            raise AccountError("Run `pocket-agent login` first.", 401)
        print("This computer is not signed in to your Pocket Agent account yet. "
              "Choose your Google account in the browser; you stay signed in after this.")
        login()
        access = _access_token(ring, url, key)
        if access is None:
            raise AccountError("Sign-in did not finish. Run `pocket-agent login` and try again.", 401)
    client = AccountClient(url, key, access)
    try:
        client.user()  # Server verification, never trust decoded JWT claims for identity.
    except Exception:
        client.close()
        raise
    return client


def access_token() -> str | None:
    url, key = settings()
    return _access_token(_keyring(), url, key)


def sign_out() -> None:
    """Revoke the shared session, then forget it even when revocation fails."""
    url, _ = settings()
    ring = _keyring()
    try:
        client = signed_in_client(sign_in_if_needed=False)
        try:
            client.logout()
        finally:
            client.close()
    finally:
        with _lock():
            if ring.get_password(_service(url), "refresh"):
                ring.delete_password(_service(url), "refresh")


def logout() -> int:
    sign_out()
    print("Signed out of this computer.")
    return 0
