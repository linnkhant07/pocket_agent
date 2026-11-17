"""Dedicated regressions for the bc6a60e items that only had indirect coverage.

Fictional pages and temp homes only. Nothing is submitted.
"""

from __future__ import annotations

import os
import queue
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from pocket_agent.forms.engine import Asked, UserAnswer, fill_form
from pocket_agent.forms.memory import Memory


def _page(html: str):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
    return playwright, browser, page


def test_c2_second_fill_keeps_the_canonical_fact(tmp_path):
    """A later form's option wording must not replace the stored canonical value."""
    memory = Memory(tmp_path / "memory.sqlite")
    memory.put("gender", "male", always_use=True, sensitive=True, source="profile", label="Gender (optional)")
    asked: list[str] = []

    def ask(item: Asked) -> UserAnswer:
        asked.append(item.label)
        return UserAnswer("Gentleman")

    man_woman = """
    <h2>Voluntary Self-Identification</h2><p id=progress>Review</p>
    <label>Gender<select required><option value=""></option><option>Man</option><option>Woman</option></select></label>
    <button type=submit>Submit</button>
    """
    other_wording = """
    <h2>Voluntary Self-Identification</h2><p id=progress>Review</p>
    <label>Gender<select required><option value=""></option><option>Gentleman</option><option>Lady</option></select></label>
    <button type=submit>Submit</button>
    """
    male_female = """
    <h2>Voluntary Self-Identification</h2><p id=progress>Review</p>
    <label>Gender<select required><option value=""></option><option>Male</option><option>Female</option></select></label>
    <button type=submit>Submit</button>
    """
    bodies = {"html": man_woman}
    playwright, browser, page = _page(man_woman)
    page.unroute("**/*")
    page.route("**/*", lambda route: route.fulfill(body=bodies["html"], content_type="text/html"))
    try:
        fill_form(page, "https://jobs.example.test/a", memory, None, ask)
        assert asked == []
        assert page.locator("select").input_value() == "Man"
        assert memory.get("gender").value == "male"

        bodies["html"] = other_wording
        fill_form(page, "https://jobs.example.test/b", memory, None, ask)
        # The option does not match the stored fact, so the user is asked. A blank result is retried.
        assert asked and set(asked) == {"Gender"}
        assert memory.get("gender").value == "male"

        asked.clear()
        bodies["html"] = male_female
        fill_form(page, "https://jobs.example.test/c", memory, None, ask)
        assert asked == []
        assert page.locator("select").input_value() == "Male"
        assert memory.get("gender").value == "male"
    finally:
        browser.close()
        playwright.stop()
        memory.close()


def test_d6_ui_server_starts_with_the_access_log_off(monkeypatch):
    """The callback URL is not written to the access log. The server is not started."""
    import uvicorn

    captured: dict = {}

    def run(app, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", run)
    from pocket_agent.cli import cmd_ui

    assert cmd_ui(SimpleNamespace(port=8765, no_open=True)) == 0
    assert captured["kwargs"]["access_log"] is False
    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 8765


def test_d8_private_files_and_sigterm_handler(tmp_path):
    """Account files are mode 0600, and the previous SIGTERM handler is put back."""
    from pocket_agent.account.runner import account_fill
    from pocket_agent.config import save_config
    from account_fakes import ready_profile

    memory = Memory(tmp_path / "forms.sqlite")
    try:
        assert memory.path.stat().st_mode & 0o777 == 0o600
    finally:
        memory.close()
    config = save_config({"fill": {"unattended": False}}, tmp_path)
    assert config.stat().st_mode & 0o777 == 0o600

    class Client:
        def snapshot(self):
            return {"profile": ready_profile(), "answers": []}

        def download_resume(self, object_path):
            return b"%PDF-1.4\n% fictional resume\n"

        def save_answer(self, *args, **kwargs):
            raise AssertionError(args)

        def record_run(self, *args, **kwargs):
            return None

    previous = signal.getsignal(signal.SIGTERM)
    raised = False
    with account_fill(Client(), "https://jobs.example.test/apply") as run:
        assert (run.home / "profile.yaml").stat().st_mode & 0o777 == 0o600
        assert (run.home / "resume.pdf").stat().st_mode & 0o777 == 0o600
        assert signal.getsignal(signal.SIGTERM) is not previous
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except SystemExit:
            raised = True
    assert raised
    assert signal.getsignal(signal.SIGTERM) == previous


def test_d8_old_account_folders_are_swept(tmp_path):
    import tempfile
    from uuid import uuid4

    from pocket_agent.account.runner import sweep_old_account_folders

    root = Path(tempfile.gettempdir())
    token = uuid4().hex
    old = root / f"pocket-account-old-{token}"
    fresh = root / f"pocket-account-fresh-{token}"
    old.mkdir()
    fresh.mkdir()
    (old / "profile.yaml").write_text("name: Ada Example\n", encoding="utf-8")
    aged = time.time() - 7200
    os.utime(old, (aged, aged))
    try:
        sweep_old_account_folders()
        assert not old.exists()
        assert fresh.is_dir()
    finally:
        if fresh.exists():
            fresh.rmdir()
        if old.exists():
            for child in old.iterdir():
                child.unlink()
            old.rmdir()


def test_e2_handled_and_skipped_survive_a_click_that_does_not_advance(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    memory.put("email", "ada@example.test", source="resume")
    asked: list[str] = []

    def ask(item: Asked) -> UserAnswer:
        asked.append(item.label)
        return UserAnswer("", skip=True)

    html = """
    <p id=progress>Step 1 of 3</p><h2>Contact</h2>
    <label>Email<input type=email required></label>
    <label>Favorite cipher<input></label>
    <button type=button id=next>Next</button>
    """
    playwright, browser, page = _page(html)
    try:
        result = fill_form(page, "https://jobs.example.test/apply", memory, None, ask)
        assert asked == []
        labels = [item.label for item in result.outcomes]
        assert labels.count("Email") == 1
        assert labels.count("Favorite cipher") == 1
        assert any(item.action == "skip" and item.label == "Favorite cipher" for item in result.outcomes)
        assert result.error is None or "action limit" not in result.error
        assert result.status == "blocked"
        assert result.submits == 0
    finally:
        browser.close()
        playwright.stop()
        memory.close()


def test_e2_unattended_missing_answer_exits_2(home: Path):
    import json

    from pocket_agent.cli import cmd_fill

    (home / "profile.yaml").write_text(Path("examples/profile.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    html = b"""
    <p id=progress>Step 1 of 2</p>
    <label>Favorite cipher<input required></label>
    <button type=submit>Submit</button>
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/apply"
        args = SimpleNamespace(
            url=url, model="none", resume=None, workday_accounts=False,
            unattended=True, headless=True, close_on_finish=True,
        )
        assert cmd_fill(args) == 2
        report_path = home / "last-fill-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert "Favorite cipher" in report["unresolved"]
        assert report_path.stat().st_mode & 0o777 == 0o600
    finally:
        server.shutdown()


def test_e3_fifty_field_page_does_not_hit_the_action_limit(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    memory.put("email", "ada@example.test", source="resume")
    notes = "\n".join(
        f"<label>Internal note {index}<input name=n{index}></label>" for index in range(50)
    )
    html = f"""
    <p id=progress>Step 1 of 1 Review</p><h2>Application</h2>
    <label>Email<input type=email required></label>
    {notes}
    <button type=submit>Submit</button>
    """
    playwright, browser, page = _page(html)
    try:
        result = fill_form(page, "https://jobs.example.test/apply", memory, None, lambda _item: UserAnswer("", skip=True))
        assert result.error is None
        assert result.submits == 0
        assert page.locator("input[type=email]").input_value() == "ada@example.test"
        assert len(result.outcomes) >= 51
    finally:
        browser.close()
        playwright.stop()
        memory.close()


def test_e5_playwright_launch_failure_resets_the_phase(monkeypatch):
    from pocket_agent.ui.desk import Desk, Done, Error, Phase

    class Playwright:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: Playwright())
    events = []
    desk = Desk(lambda: (_ for _ in ()).throw(AssertionError("account should not be opened")))
    desk._launch = lambda playwright: (_ for _ in ()).throw(RuntimeError("chromium missing"))
    desk.publish = lambda event: events.append(event)
    desk.handle({"type": "start", "url": "https://jobs.example.test/apply", "model": "none"})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(isinstance(event, Done) for event in events):
        time.sleep(0.05)
    assert desk._phase is Phase.IDLE
    assert any(isinstance(event, Error) and "RuntimeError" in event.message for event in events)
    assert any(isinstance(event, Done) and event.status == "stopped" for event in events)


def test_e12_answer_expires_after_30_minutes():
    from pocket_agent.ui.desk import Desk

    class Expire(queue.Queue):
        def get(self, block=True, timeout=None):
            assert timeout == 30 * 60
            raise queue.Empty

    desk = Desk(lambda: None)
    desk._answers = Expire()
    answer = desk._ask(Asked("notes", "Notes", "What should I put for \"Notes\"?"))
    assert answer.skip is True
    assert answer.value == ""


def test_e12_config_lock_serializes_saves(tmp_path, monkeypatch):
    from pocket_agent.config import load_config, save_config

    state = {"open": 0, "max": 0}
    guard = threading.Lock()
    original = Path.write_text

    def slow_write(self, data, encoding=None, errors=None, newline=None):
        if self.name == "config.yaml":
            with guard:
                state["open"] += 1
                state["max"] = max(state["max"], state["open"])
            time.sleep(0.05)
            with guard:
                state["open"] -= 1
        kwargs = {}
        if encoding is not None:
            kwargs["encoding"] = encoding
        if errors is not None:
            kwargs["errors"] = errors
        if newline is not None:
            kwargs["newline"] = newline
        return original(self, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", slow_write)
    errors = []

    def worker(index: int) -> None:
        try:
            save_config({"writer": index, "pad": "x" * 400}, tmp_path)
        except Exception as exc:  # noqa: BLE001 — the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert state["max"] == 1
    saved = load_config(tmp_path)
    assert saved["writer"] in range(6)
    assert saved["pad"] == "x" * 400


def test_company_question_without_an_employer_name_is_filled_no(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    memory.put("work.0.org", "Contoso Robotics", source="resume")
    asked: list[str] = []
    html = """
    <p id=progress>Step 1 of 1 Review</p>
    <fieldset>
      <legend>Have you ever worked for this company?</legend>
      <label><input type=radio name=prev value=Yes required> Yes</label>
      <label><input type=radio name=prev value=No required> No</label>
    </fieldset>
    <button type=submit>Submit</button>
    """
    playwright, browser, page = _page(html)
    try:
        result = fill_form(page, "https://jobs.example.test/apply", memory, None, lambda item: asked.append(item.label) or UserAnswer("", skip=True))
        assert asked == []
        assert page.locator("input[value=No]").is_checked()
        assert any(item.action == "fill" and item.value == "No" for item in result.outcomes)
        assert result.submits == 0
    finally:
        browser.close()
        playwright.stop()
        memory.close()


def test_e8_focused_answer_survives_reload_and_profile_edits_stay(tmp_path, monkeypatch):
    """A finished fill reloads answers. A focused draft and an unsaved profile edit stay on screen."""
    import socket
    import urllib.request

    import uvicorn

    from pocket_agent.forms.engine import FormRun
    from pocket_agent.ui.app import create_app
    from account_fakes import install, ready_profile

    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    state, _ring = install(monkeypatch.setattr, signed_in=True, profile=ready_profile())
    state.answers = [{"key": "preferred_name", "value": "Ada", "always_use": True, "label": "Preferred name"}]
    gate, release = threading.Event(), threading.Event()

    def fake_fill(*_args, **_kwargs):
        gate.set()
        assert release.wait(20)
        return FormRun(url="https://jobs.example.test/apply", status="review")

    monkeypatch.setattr("pocket_agent.ui.desk.fill_form", fake_fill)

    def launch(playwright):
        return playwright.chromium.launch(headless=True).new_context()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(port=port, launch=launch)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("app did not start")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.locator("#questionList .question").filter(has_text="Preferred name").locator("input").wait_for()
            page.get_by_role("button", name="Edit extracted details").click()
            page.locator("#profileForm input[name=name]").fill("Edited Locally")
            page.locator("#jobUrl").fill("https://jobs.example.test/apply")
            page.wait_for_timeout(500)
            page.locator("#fill").click()
            if not gate.wait(15):
                raise AssertionError(page.locator("#notice").inner_text() + " | " + page.locator("#fillLog").inner_text())
            preferred = page.locator("#questionList .question").filter(has_text="Preferred name").locator("input")
            salary = page.locator("#questionList .question").filter(has_text="Salary expectations").locator("input")
            preferred.fill("Local draft")
            preferred.evaluate("el => { el.dataset.kept = 'yes'; }")
            salary.evaluate("el => { el.dataset.kept = 'yes'; }")
            state.answers = [
                {"key": "preferred_name", "value": "Ada", "always_use": True, "label": "Preferred name"},
                {"key": "salary_expectation", "value": "90000", "always_use": True, "label": "Salary expectations (include currency and period)"},
            ]
            release.set()
            page.wait_for_function(
                "() => (document.querySelector('#fillResult').textContent || '').includes('Ready for your review')",
                timeout=20000,
            )
            assert page.locator("#profileForm input[name=name]").input_value() == "Edited Locally"
            assert state.profile["data"]["name"] == "Jordan Hale"
            assert preferred.input_value() == "Local draft"
            assert preferred.get_attribute("data-kept") == "yes"
            # The focused list was not rebuilt, so the server's salary is not painted over the empty field.
            assert salary.input_value() == ""
            assert salary.get_attribute("data-kept") == "yes"

            gate.clear()
            release.clear()
            preferred.evaluate("el => el.blur()")
            page.wait_for_function(
                "() => [...document.querySelectorAll('#questionList [role=status]')].some(el => el.textContent === 'Saved')",
                timeout=10000,
            )
            release.clear()
            page.locator("#fill").click()
            assert gate.wait(20)
            release.set()
            page.wait_for_function(
                "() => (document.querySelector('#fillResult').textContent || '').includes('Ready for your review')",
                timeout=20000,
            )
            salary = page.locator("#questionList .question").filter(has_text="Salary expectations").locator("input")
            assert salary.input_value() == "90000"
            assert salary.get_attribute("data-kept") is None
            assert page.locator("#profileForm input[name=name]").input_value() == "Edited Locally"
            browser.close()
    finally:
        server.should_exit = True
