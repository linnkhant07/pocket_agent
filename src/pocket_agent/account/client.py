"""Supabase REST boundary: only a public project key and the user's own JWT.

Never include response bodies or HTTP exceptions in errors: they may contain PII.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID, uuid4

import httpx

MAX_RESUME_BYTES = 10 * 1024 * 1024

# Only locally written messages may reach the browser, never Supabase response text.
ACCOUNT_ERROR_MESSAGES = {
    "network_error": "Cannot reach Supabase. Check SUPABASE_URL, your connection, and whether the project is paused.",
    "invalid_project_key": "Supabase rejected the project key. Check that SUPABASE_URL and SUPABASE_ANON_KEY belong to the same project and use the public key.",
    "signup_disabled": "New account sign-ups are disabled in Supabase. Enable sign-ups or use an existing account.",
    "over_request_rate_limit": "Supabase's request limit was reached. Wait before retrying.",
    "captcha_failed": "Supabase requires CAPTCHA verification, which this portal does not yet support.",
    "auth_service_error": "Supabase could not complete sign-in. Check Authentication logs in your Supabase dashboard for SMTP or Auth errors.",
    "google_not_configured": "Enable Google in Supabase Authentication > Sign In / Providers and add your Google OAuth client ID and secret.",
    "oauth_callback_failed": "Google sign-in did not finish. Try Continue with Google again in the same browser tab.",
}
_LOG = logging.getLogger(__name__)


class AccountError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, *, reason: str | None = None, provider_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason if reason in ACCOUNT_ERROR_MESSAGES else None
        self.provider_code = provider_code if isinstance(provider_code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", provider_code) else None


def _dotenv_settings() -> dict[str, str]:
    """SUPABASE_URL and SUPABASE_ANON_KEY from ./.env, so the repo checkout works without exporting them."""
    try:
        lines = open(".env", encoding="utf-8").read().splitlines()
    except OSError:
        return {}
    found = {}
    for line in lines:
        name, _, value = line.strip().removeprefix("export ").partition("=")
        if name in {"SUPABASE_URL", "SUPABASE_ANON_KEY"}:
            found[name] = value.strip().strip("'\"")
    return found


def settings() -> tuple[str, str]:
    """Environment, then the project `pocket-agent login` remembered in config.yaml, then ./.env."""
    from pocket_agent.config import load_config
    saved = load_config().get("account") or {}
    dotenv = _dotenv_settings()
    return (os.environ.get("SUPABASE_URL") or str(saved.get("supabase_url") or "") or dotenv.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_ANON_KEY") or str(saved.get("supabase_anon_key") or "") or dotenv.get("SUPABASE_ANON_KEY", ""))


class AccountClient:
    def __init__(self, url: str, public_key: str, access_token: str = "", *, transport=None):
        parsed = urlsplit(url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}
                or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise AccountError("Set SUPABASE_URL to an HTTPS project URL (HTTP is allowed on localhost).", 503)
        if not public_key or public_key.startswith("sb_secret_"):
            raise AccountError("Set SUPABASE_ANON_KEY to the public project key, never a service key.", 503)
        # Legacy project keys are JWTs; reject service_role keys without displaying them.
        if public_key.count(".") == 2:
            try:
                payload = public_key.split(".")[1]
                role = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("role")
                if role != "anon":
                    raise ValueError("not public")
            except (ValueError, TypeError):
                raise AccountError("SUPABASE_ANON_KEY must be the public anon key.", 503) from None
        self._http = httpx.Client(base_url=url.rstrip("/"), timeout=30, transport=transport,
                                  headers={"apikey": public_key, **({"Authorization": f"Bearer {access_token}"} if access_token else {})})
        self._identity = None

    def close(self):
        self._http.close()

    def _request(self, method, path, **kwargs):
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise AccountError(ACCOUNT_ERROR_MESSAGES["network_error"], reason="network_error") from None
        if response.is_error:
            status = response.status_code
            reason = None
            provider_code = None
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                candidate = payload.get("error_code") or payload.get("code")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", candidate):
                    provider_code = candidate
            if path.startswith("/auth/v1/"):
                # Unrecognized provider strings must never be reflected or logged.
                if provider_code in ACCOUNT_ERROR_MESSAGES:
                    reason = provider_code
                elif status in {401, 403} and path == "/auth/v1/settings":
                    reason = "invalid_project_key"
                elif status >= 500:
                    reason = "auth_service_error"
                _LOG.warning("Supabase Auth request failed: upstream_status=%s reason=%s", status, reason or "unclassified")
            if provider_code in {"40001", "PT409"}:
                status = 409
            if reason:
                raise AccountError(ACCOUNT_ERROR_MESSAGES[reason], status, reason=reason, provider_code=provider_code)
            message = ("Sign in again; your account session expired." if status in {401, 403}
                       else "Your profile changed in another tab. Reload before saving." if status == 409
                       else "Account request failed. Check your input and retry.")
            raise AccountError(message, status, provider_code=provider_code)
        return response

    def _json(self, method, path, **kwargs):
        try:
            return self._request(method, path, **kwargs).json()
        except ValueError:
            raise AccountError("Account service returned an invalid response.") from None

    def google_sign_in_url(self, redirect_to: str, challenge: str) -> str:
        config = self._json("GET", "/auth/v1/settings")
        if not isinstance(config, dict) or (config.get("external") or {}).get("google") is not True:
            raise AccountError(ACCOUNT_ERROR_MESSAGES["google_not_configured"], 409, reason="google_not_configured")
        return str(self._http.base_url).rstrip("/") + "/auth/v1/authorize?" + urlencode({
            "provider": "google", "redirect_to": redirect_to, "code_challenge": challenge,
            "code_challenge_method": "s256", "scopes": "openid email profile"})

    def exchange_oauth_code(self, code: str, verifier: str) -> dict:
        return self._json("POST", "/auth/v1/token", params={"grant_type": "pkce"},
                          json={"auth_code": code, "code_verifier": verifier})

    def refresh(self, refresh_token: str) -> dict:
        return self._json("POST", "/auth/v1/token", params={"grant_type": "refresh_token"}, json={"refresh_token": refresh_token})

    def logout(self):
        self._request("POST", "/auth/v1/logout", params={"scope": "local"})

    def user(self) -> dict:
        if self._identity is None:
            self._identity = self._json("GET", "/auth/v1/user")
            try:
                UUID(self._identity["id"])
            except (KeyError, ValueError, TypeError):
                raise AccountError("Account service returned an invalid identity.") from None
        return self._identity

    def snapshot(self) -> dict:
        uid = self.user()["id"]
        params = {"user_id": f"eq.{uid}", "select": "*"}
        profiles = self._json("GET", "/rest/v1/account_profiles", params=params)
        return {"profile": profiles[0] if profiles else None,
                "answers": self._json("GET", "/rest/v1/account_answers", params=params),
                "runs": self._json("GET", "/rest/v1/account_runs", params={**params, "order": "created_at.desc", "limit": "50"})}

    def save_profile(self, data: dict, expected_revision: int) -> dict:
        result = self._json("POST", "/rest/v1/rpc/save_account_profile",
                            json={"data": data, "expected_revision": expected_revision})
        return result[0] if isinstance(result, list) else result

    def save_answer(self, key: str, value: str, always_use: bool, label: str = ""):
        self._request("POST", "/rest/v1/account_answers", headers={"Prefer": "resolution=merge-duplicates"},
                      json={"user_id": self.user()["id"], "key": key, "value": value, "always_use": always_use, "label": label})

    def delete_answer(self, key: str):
        self._request("DELETE", "/rest/v1/account_answers", params={"user_id": f"eq.{self.user()['id']}", "key": f"eq.{key}"})

    def upload_resume(self, content: bytes) -> str:
        if len(content) > MAX_RESUME_BYTES or not content.startswith(b"%PDF-"):
            raise AccountError("Choose a PDF resume no larger than 10 MiB.", 422)
        path = f"{self.user()['id']}/{uuid4()}.pdf"
        self._request("POST", f"/storage/v1/object/resumes/{path}", content=content,
                      headers={"Content-Type": "application/pdf", "x-upsert": "false"})
        return path

    def _own_resume_path(self, object_path: str) -> str:
        # Accept exactly our immutable uid/uuid.pdf format, never a URL or a traversal.
        uid = self.user()["id"]
        parts = object_path.split("/") if isinstance(object_path, str) else []
        try:
            if len(parts) != 2 or parts[0] != uid or not parts[1].endswith(".pdf"):
                raise ValueError()
            UUID(parts[1][:-4])
        except (ValueError, TypeError):
            raise AccountError("The account resume reference is invalid.", 422) from None
        return object_path

    def delete_resume(self, object_path: str):
        path = self._own_resume_path(object_path)
        self._request("DELETE", f"/storage/v1/object/resumes/{quote(path, safe='/')}")

    def resume_paths(self) -> list[str]:
        uid = self.user()["id"]
        rows = self._json("POST", "/storage/v1/object/list/resumes", json={"prefix": uid, "limit": 1000, "offset": 0})
        names = [row.get("name") for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        paths = []
        for name in names:
            try:
                if isinstance(name, str) and name.endswith(".pdf") and str(UUID(name[:-4])) == name[:-4].lower():
                    paths.append(f"{uid}/{name}")
            except ValueError:
                continue  # Not an object this app wrote; never build a path from it.
        return paths

    def delete_account_data(self):
        """Remove every resume object and row this user owns. The auth identity itself needs an admin."""
        for path in self.resume_paths():
            self.delete_resume(path)
        params = {"user_id": f"eq.{self.user()['id']}"}
        for table in ("account_answers", "account_runs", "account_profiles"):
            self._request("DELETE", f"/rest/v1/{table}", params=params)

    def download_resume(self, object_path: str) -> bytes:
        object_path = self._own_resume_path(object_path)
        try:
            with self._http.stream("GET", f"/storage/v1/object/authenticated/resumes/{quote(object_path, safe='/')}") as response:
                if response.is_error:
                    raise AccountError("Could not download this account's resume.", response.status_code)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESUME_BYTES:
                        raise AccountError("The stored resume exceeds the 10 MiB limit.", 422)
        except httpx.HTTPError:
            raise AccountError("Resume download failed. Retry when connected.") from None
        if not content.startswith(b"%PDF-"):
            raise AccountError("The stored resume is not a PDF.", 422)
        return bytes(content)

    def record_run(self, run_id: str, url: str, status: str, unresolved: list[str]):
        # A retry of the same run ID is a no-op. Never upload raw errors or answer values.
        self._request("POST", "/rest/v1/account_runs", headers={"Prefer": "resolution=ignore-duplicates"},
                      json={"user_id": self.user()["id"], "id": run_id, "url": url, "status": status, "unresolved": unresolved})
