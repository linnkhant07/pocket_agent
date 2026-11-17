"""The Desk runs account fills for the local app, one at a time, in a visible Chromium window.

Phases: idle -> filling <-> asking -> idle. Every fill ends with exactly one `done` event.
The filler's browser outlives each fill so the user can review and submit it themselves.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from pocket_agent.account.client import AccountClient
from pocket_agent.account.runner import AccountFill, account_fill
from pocket_agent.forms.engine import Asked, UserAnswer, fill_form

Model = Literal["none", "gpt", "claude"]


@dataclass(frozen=True)
class Status:
    text: str
    type: Literal["status"] = "status"


@dataclass(frozen=True)
class Ask:
    key: str
    label: str
    question: str
    kind: str
    options: list[str]
    sensitive: bool
    question_id: str = ""
    type: Literal["ask"] = "ask"


@dataclass(frozen=True)
class Error:
    message: str
    type: Literal["error"] = "error"


@dataclass(frozen=True)
class Done:
    status: Literal["review", "blocked", "stopped"]
    unresolved: list[str] = field(default_factory=list)
    type: Literal["done"] = "done"


Event = Status | Ask | Error | Done


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Start(_Message):
    type: Literal["start"]
    url: str = Field(max_length=4000)
    model: Model = "none"


class Continue(_Message):
    """Resume on the page already open, after the user cleared a sign-in or CAPTCHA step by hand."""
    type: Literal["continue"]
    model: Model = "none"


class Answer(_Message):
    type: Literal["answer"]
    value: str = Field(default="", max_length=20000)
    always_use: bool = False
    skip: bool = False
    question_id: str = ""


class Stop(_Message):
    type: Literal["stop"]


Message = TypeAdapter(Annotated[Start | Continue | Answer | Stop, Field(discriminator="type")])


class Phase(Enum):
    IDLE = "idle"
    FILLING = "filling"
    ASKING = "asking"


def visible_browser(playwright):
    return playwright.chromium.launch(headless=False).new_context()


class Desk:
    def __init__(self, account: Callable[[], AccountClient], *, launch=visible_browser) -> None:
        self._account = account
        self._launch = launch
        self._lock = threading.Lock()
        self._phase = Phase.IDLE
        self._asking: Ask | None = None
        self._last_url = ""
        self._commands: queue.Queue[Start | Continue] = queue.Queue()
        self._answers: queue.Queue[Answer] = queue.Queue()
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._worker: threading.Thread | None = None
        self._cancel = False

    def subscribe(self) -> asyncio.Queue:
        """A queue of events for one websocket. A reconnecting tab gets the open question again."""
        events: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers[events] = asyncio.get_running_loop()
            if self._phase is Phase.FILLING:
                events.put_nowait(asdict(Status("A fill is running in the browser window.")))
            if self._asking is not None:
                events.put_nowait(asdict(self._asking))
        return events

    def unsubscribe(self, events: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(events, None)

    def publish(self, event: Event) -> None:
        payload = asdict(event)
        with self._lock:
            targets = list(self._subscribers.items())
        for events, loop in targets:
            if not loop.is_closed():
                loop.call_soon_threadsafe(events.put_nowait, payload)

    def handle(self, raw: object) -> None:
        try:
            message = Message.validate_python(raw)
        except ValidationError:
            self.publish(Error("Unknown message."))
            return
        mismatch = False
        idle_stop = False
        with self._lock:
            phase = self._phase
            if isinstance(message, Answer) and phase is Phase.ASKING:
                if message.question_id and self._asking is not None and message.question_id != self._asking.question_id:
                    mismatch = True
                else:
                    self._phase, self._asking = Phase.FILLING, None
            elif isinstance(message, Stop):
                self._cancel = True
                self._last_url = ""
                if self._worker is None or not self._worker.is_alive():
                    self._phase, self._asking, self._cancel = Phase.IDLE, None, False
                    idle_stop = True
            elif not isinstance(message, Answer) and phase is Phase.IDLE:
                self._phase = Phase.FILLING
            else:
                phase = None
        if mismatch:
            self.publish(Error("That answer was for a different question."))
            return
        if idle_stop:
            return
        if phase is None:
            self.publish(Error("No question is waiting." if isinstance(message, Answer)
                               else "A fill is already running. Finish it before starting another."))
        elif isinstance(message, Answer):
            self._answers.put(message)
        else:
            self._ensure_worker()
            self._commands.put(message)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="pocket-desk-fill", daemon=True)
            self._worker.start()

    def cancel(self) -> None:
        """Ask the worker to stop. Playwright is closed on the worker thread, which owns it."""
        with self._lock:
            asking = self._phase is Phase.ASKING
        self.handle({"type": "stop"})
        if asking:
            try:
                self._answers.put_nowait(Answer(value="", skip=True))
            except Exception:
                pass

    def _work(self) -> None:
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                context = page = None
                while True:
                    command = self._commands.get()
                    try:
                        if isinstance(command, Stop):
                            if context is not None:
                                context.close()
                            context = page = None
                            self._cancel = False
                            self._finish(Done("stopped"))
                            continue
                        if context is None or not context.browser.is_connected():
                            context, page = self._launch(playwright), None
                        fresh = isinstance(command, Start) or page is None or page.is_closed()
                        if fresh:
                            page = context.new_page()
                        self._fill(page, command, navigate=fresh)
                    except Exception as exc:  # noqa: BLE001 — every fill must end with done so the tab can recover
                        self.publish(Error(_describe(exc)))
                        self._finish(Done("stopped"))
        except Exception as exc:  # noqa: BLE001 — a browser that never starts must not leave the desk stuck filling
            self.publish(Error(_describe(exc)))
            self._finish(Done("stopped"))

    def _fill(self, page, command: Start | Continue, *, navigate: bool) -> None:
        from pocket_agent.forms.cloud import create_decider
        from pocket_agent.forms.memory import Memory
        from pocket_agent.forms.seed import seed_memory
        from pocket_agent.profile import load_profile
        from pocket_agent.resume import parse_resume

        url = command.url if isinstance(command, Start) else self._last_url
        if not url:
            raise ValueError("Paste an application URL and click Fill first.")
        self._last_url = url
        client = self._account()
        try:
            with account_fill(client, url) as run:
                profile = load_profile(run.home / "profile.yaml")
                memory = Memory(run.home / "forms.sqlite")
                try:
                    seed_memory(memory, profile, resume_path=str(run.resume))
                    resume_text = parse_resume(run.resume).get("text", "") if command.model != "none" else ""
                    decider = create_decider(command.model, profile, resume_text, home=run.home,
                                             log=lambda text: self.publish(Status(text)))
                    self.publish(Status("Filling in the browser window. Nothing is submitted."))
                    result = fill_form(page, url, memory, run.resume, self._ask, decider,
                                       events=self._engine_event, navigate=navigate, preserve_existing=not navigate,
                                       stop=lambda: self._cancel)
                    if decider is not None:
                        self.publish(Status(decider.backend.summary()))
                    done = _done(result, run)
                    if result.error:
                        self.publish(Error(result.error))
                finally:
                    memory.close()
        finally:
            client.close()
        if run.sync_error:
            self.publish(Error("Answers from this fill could not be saved to your account. " + run.sync_error))
        self._finish(done)

    def _finish(self, done: Done) -> None:
        with self._lock:
            self._phase, self._asking = Phase.IDLE, None
        self.publish(done)

    def _ask(self, asked: Asked) -> UserAnswer:
        import uuid

        question = Ask(
            asked.key, asked.label, asked.question, asked.kind, list(asked.options), asked.sensitive,
            question_id=uuid.uuid4().hex,
        )
        with self._lock:
            self._phase, self._asking = Phase.ASKING, question
        self.publish(question)
        try:
            answer = self._answers.get(timeout=30 * 60)
        except queue.Empty:
            return UserAnswer("", skip=True)
        if answer.skip or self._cancel:
            return UserAnswer("", skip=True)
        # Sensitive answers are saved only when the ask card's checkbox is on.
        return UserAnswer(answer.value, always_use=answer.always_use if asked.sensitive else False)

    def _engine_event(self, event: dict) -> None:
        if event.get("type") == "step" and event.get("heading"):
            self.publish(Status(f"Section: {event['heading']}"))
        elif event.get("type") == "activity" and event.get("action") in {"fill", "ask"} and event.get("value"):
            self.publish(Status(f"Filled {event['label']} (from {event['source']})"))
        elif event.get("type") == "gate":
            self.publish(Status(f"This page needs you: {event['gate']}. Finish that step in the browser window, then click Continue."))


def _done(result, run: AccountFill) -> Done:
    status = "blocked" if result.error or result.gate or result.submits else result.status
    run.status, run.unresolved = status, list(dict.fromkeys(result.unresolved))
    return Done(status, run.unresolved)


def _describe(exc: Exception) -> str:
    from pocket_agent.account.client import AccountError

    if isinstance(exc, (AccountError, ValueError)):
        return str(exc)
    return f"The fill stopped ({type(exc).__name__}). Check the browser window and try again."
