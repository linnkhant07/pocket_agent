"""Pocket Agent on 127.0.0.1: Google sign-in, profile, answers, history, and the Desk fill websocket.

The browser holds only a short-lived access token in memory. The refresh token stays in Keychain,
shared with the terminal, so reloading the tab or running `pocket-agent fill --account` needs no new sign-in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.datastructures import Headers

from pocket_agent import ai_settings
from pocket_agent.account import auth
from pocket_agent.account.client import ACCOUNT_ERROR_MESSAGES, AccountClient, AccountError, settings
from pocket_agent.account.profile import missing_profile_fields, validate_account_profile
from pocket_agent.account.questions import questions
from pocket_agent.account.resume_import import extract_resume, prepare_onboarding
from pocket_agent.profile import ProfileError
from pocket_agent.ui.desk import Desk, visible_browser

MAX_RESUME = 10 * 1024 * 1024
STATIC = Path(__file__).resolve().parent / "static"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoogleStartBody(StrictBody):
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class GoogleExchangeBody(StrictBody):
    code: SecretStr
    verifier: SecretStr


class ProfileBody(StrictBody):
    data: dict
    expected_revision: int = Field(ge=0)


class AnswerBody(StrictBody):
    value: str = Field(max_length=20000)
    label: str = Field(default="", max_length=1000)


class AIBody(StrictBody):
    api_key: SecretStr | None = Field(default=None, max_length=500)
    model: str = Field(pattern=r"^[A-Za-z0-9._:/@-]{1,200}$")


def project() -> tuple[str, str]:
    url, key = settings()
    if not url or not key:
        raise HTTPException(503, "Set SUPABASE_URL and SUPABASE_ANON_KEY (for example in .env), then restart pocket-agent ui.")
    return url, key


def public_account():
    client = AccountClient(*project())
    try:
        yield client
    finally:
        client.close()


def signed_in(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(401, "Sign in to continue.")
    client = AccountClient(*project(), authorization[7:].strip())
    try:
        client.user()  # Supabase validates the token; no unverified JWT decoding.
        yield client
    finally:
        client.close()


def discard_resume(account, object_path: str) -> None:
    try:
        account.delete_resume(object_path)
    except AccountError:
        logging.getLogger(__name__).warning("Could not delete an unreferenced resume object.")


class LocalOnly:
    """Any web page the user visits can reach 127.0.0.1, and this server holds their session.

    Reject other Host names (DNS rebinding), cross-site websockets, and cross-site writes.
    """

    def __init__(self, app, port: int) -> None:
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        same_origin = headers.get("origin") == "http://" + host
        allowed = host in self.hosts and (
            same_origin if scope["type"] == "websocket"
            else scope["method"] in ("GET", "HEAD") or same_origin or headers.get("sec-fetch-site") == "same-origin")
        if allowed:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": "This request did not come from Pocket Agent."}, status_code=403)(scope, receive, send)


def security_headers(port: int):
    policy = ("default-src 'self'; script-src 'self'; style-src 'self'; "
              f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; img-src 'self' data:; "
              "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    async def middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = policy
        return response

    return middleware


def create_app(*, port: int = 8000, launch=visible_browser) -> FastAPI:
    app = FastAPI(title="Pocket Agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(security_headers(port))
    app.add_middleware(LocalOnly, port=port)
    from pocket_agent.account.runner import sweep_old_account_folders

    sweep_old_account_folders()
    desk = Desk(lambda: auth.signed_in_client(sign_in_if_needed=False), launch=launch)

    @app.exception_handler(AccountError)
    async def account_error(request, exc):
        code = getattr(exc, "status_code", 502)
        if code not in (400, 401, 403, 409, 413, 422, 429, 503):
            code = 502
        messages = {401: "Your session has expired. Sign in again.", 403: "This action is not allowed.",
                    409: "Your profile changed in another window. Reload before saving.",
                    429: "Too many requests. Please try again later.",
                    # 503s are local setup problems (Keychain, project settings) with locally written text.
                    503: str(exc)}
        detail = ACCOUNT_ERROR_MESSAGES.get(exc.reason) or messages.get(code, "The account service could not complete this request.")
        return JSONResponse({"detail": detail}, status_code=code)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/auth/callback")
    def google_callback():
        # JS checks per-tab state, removes the code from the URL, then exchanges it.
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    def script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    def stylesheet():
        return FileResponse(STATIC / "style.css", media_type="text/css")

    @app.post("/api/auth/google/start")
    def google_start(body: GoogleStartBody, request: Request, account=Depends(public_account)):
        callback = str(request.base_url).rstrip("/") + "/auth/callback?pa_state=" + body.state
        return {"url": account.google_sign_in_url(callback, body.challenge)}

    @app.post("/api/auth/google/exchange")
    def google_exchange(body: GoogleExchangeBody, account=Depends(public_account)):
        verifier = body.verifier.get_secret_value()
        code = body.code.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier) or not 1 <= len(code) <= 2048:
            raise HTTPException(422, "Google sign-in did not finish. Try again.")
        ring = auth._keyring()  # Before the exchange, so a missing Keychain does not burn the one-time code.
        session = account.exchange_oauth_code(code, verifier)
        if not isinstance(session, dict) or not isinstance(session.get("access_token"), str) or not session["access_token"]:
            raise HTTPException(502, "Google sign-in returned no session. Try again.")
        auth.store_session(ring, *project(), session)
        user = session.get("user") if isinstance(session.get("user"), dict) else {}
        return {"access_token": session["access_token"], "expires_in": session.get("expires_in", 3600),
                "email": str(user.get("email") or "")}

    @app.post("/api/session")
    def session():
        token = auth.access_token()
        if token is None:
            raise HTTPException(401, "Sign in to continue.")
        return {"access_token": token}

    @app.post("/api/auth/logout")
    def logout():
        auth.sign_out()
        desk.cancel()
        return {"ok": True}

    # AI keys never go back to the browser; it only learns whether one is saved.
    @app.get("/api/ai")
    def ai_status():
        return ai_settings.status()

    @app.put("/api/ai/{provider}")
    def save_ai(provider: Literal["gpt", "claude"], body: AIBody):
        key = body.api_key.get_secret_value().strip() if body.api_key else ""
        if key and (len(key) < 8 or any(c.isspace() for c in key)):
            raise HTTPException(422, "That does not look like an API key. Paste the whole key.")
        ai_settings.save(provider, key=key or None, model_id=body.model)
        return ai_settings.status()

    @app.delete("/api/ai/{provider}")
    def forget_ai_key(provider: Literal["gpt", "claude"]):
        ai_settings.forget_key(provider)
        return ai_settings.status()

    @app.get("/api/account")
    def account_snapshot(account=Depends(signed_in)):
        return {**account.snapshot(), "user": account.user(), "questions": questions()}

    @app.put("/api/account/profile")
    def profile(body: ProfileBody, account=Depends(signed_in)):
        data = body.data
        if len(json.dumps(data).encode()) > 256 * 1024:
            raise HTTPException(413, "Profile must be 256 KB or smaller.")
        if "form_answers" in data:
            raise HTTPException(422, "Save questionnaire answers separately from your profile.")
        path = data.get("resume_object")
        if path:
            uid = str(UUID(account.user()["id"]))
            try:
                owner, filename = path.split("/")
                if owner != uid or str(UUID(filename.removesuffix(".pdf"))) + ".pdf" != filename:
                    raise ValueError()
            except (AttributeError, ValueError):
                raise HTTPException(422, "Choose a resume belonging to this account.") from None
        try:
            data = validate_account_profile(data)
        except ProfileError:
            raise HTTPException(422, "Check your profile: name, education (school and degree), skills, and preferences are required.") from None
        data.setdefault("onboarding", {})["missing_fields"] = missing_profile_fields(data)
        return account.save_profile(data, body.expected_revision)

    @app.put("/api/account/answers/{key}")
    def save_answer(key: str, body: AnswerBody, account=Depends(signed_in)):
        from pocket_agent.forms.facts import FACT_KEYS, canonicalize, is_fact
        from pocket_agent.forms.policy import SENSITIVE_KEYS

        known = {q["key"] for q in questions()}
        saved = {a.get("key") for a in account.snapshot()["answers"]}
        # A deleted q.* answer can be typed again. Arbitrary names stay rejected.
        learned = bool(re.fullmatch(r"q\.[a-z0-9_]{6,64}", key))
        if key not in known and key not in saved and key not in SENSITIVE_KEYS and key not in FACT_KEYS and not learned:
            raise HTTPException(422, "Choose a questionnaire question.")
        value = body.value
        if is_fact(key):
            canon = canonicalize(key, value)
            if canon:
                value = canon
        account.save_answer(key, value, True, body.label)
        return {"ok": True}

    @app.delete("/api/account/answers/{key}")
    def delete_answer(key: str, account=Depends(signed_in)):
        if len(key) > 200:
            raise HTTPException(422, "Invalid question key.")
        account.delete_answer(key)
        return {"ok": True}

    @app.delete("/api/account")
    def delete_account(account=Depends(signed_in)):
        from pocket_agent.account.client import AccountError

        account.delete_account_data()
        try:
            auth.sign_out()
        except AccountError:
            pass
        desk.cancel()
        return {"ok": True}

    @app.post("/api/account/resume")
    def upload_resume(file: UploadFile = File(), expected_revision: int = Form(default=0, ge=0), account=Depends(signed_in)):
        try:
            content = file.file.read(MAX_RESUME + 1)
        finally:
            file.file.close()
        if len(content) > MAX_RESUME:
            raise HTTPException(413, "Resume must be 10 MB or smaller.")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(422, "Choose a PDF resume.")
        current = account.snapshot().get("profile")
        if (current["revision"] if current else 0) != expected_revision:
            raise HTTPException(409, "Your account changed in another tab. Reload before uploading.")
        try:
            extracted = extract_resume(content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        previous = (current["data"] or {}).get("resume_object") if current else None
        object_path = account.upload_resume(content)
        try:
            draft = prepare_onboarding(current["data"] if current else {}, extracted, object_path)
            row = account.save_profile(draft, expected_revision)
        except BaseException:
            discard_resume(account, object_path)  # Nothing points at it; do not keep the PDF.
            raise
        if previous and previous != object_path:
            discard_resume(account, previous)  # Replaced resumes are personal data; delete them.
        return {"object_path": object_path, "profile": row, "warnings": extracted["warnings"],
                "missing_fields": missing_profile_fields(draft), "parser": extracted["parser"]}

    @app.websocket("/ws")
    async def fill_socket(websocket: WebSocket):
        await websocket.accept()
        events = desk.subscribe()

        async def pump():
            while True:
                await websocket.send_json(await events.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                desk.handle(await websocket.receive_json())
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            sender.cancel()
            desk.unsubscribe(events)

    return app
