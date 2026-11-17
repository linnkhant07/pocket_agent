"""The real local app on uvicorn, driven in Chromium: restore the shared session, Fill, answer inline, reach Review.

Supabase and Keychain are in-memory fakes. The Desk's filler Chromium is the real engine on the fictional demo
form, served at an HTTPS origin because account fills refuse plain HTTP. The check attaches to that filler over
CDP to read its page. Set POCKET_E2E_HEADED=1 to watch it.

The demo's last step is headed "Review" but its progress reads "Step 4 of 4", and the engine reports "review"
only when the progress names it, so the run's recorded status is "stopped".
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from account_fakes import EMAIL, FORMS_ORIGIN, https_forms, install, ready_profile
from pocket_agent.demo import DEMO
from pocket_agent.forms.server import FormServer
from pocket_agent.ui.app import create_app

ARTIFACTS = Path(__file__).resolve().parents[1] / ".pytest_cache" / "ui-artifacts"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("app did not start")


def answer_until_done(page) -> list[str]:
    answered = []
    for _ in range(12):
        page.wait_for_function("() => !document.querySelector('#askCard').hidden || document.querySelector('#fillResult').textContent", timeout=60000)
        if page.locator("#fillResult").inner_text():
            return answered
        question = page.locator("#askQuestion").inner_text()
        answered.append(question)
        if len(answered) == 1:
            page.screenshot(path=str(ARTIFACTS / "app-ask.png"), full_page=True)
        field = page.locator("#askValue")
        tag = field.evaluate("el => el.tagName.toLowerCase() + '/' + (el.type || '')")
        if tag.startswith("select"):
            options = field.locator("option").all_text_contents()
            field.select_option("Referral" if "Referral" in options else options[0])
        elif tag.endswith("/date"):
            field.fill("2026-06-15")
        else:
            field.fill("80000" if "salary" in question.lower() else "Sam Rivera")
        page.locator("#askSend").click()
    raise AssertionError("fill did not finish: " + ", ".join(answered))


def filler_page(playwright, debug_port: int):
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
    pages = [page for context in browser.contexts for page in context.pages if page.url.startswith(FORMS_ORIGIN)]
    assert len(pages) == 1, [page.url for context in browser.contexts for page in context.pages]
    return pages[0]


def test_signed_in_fill_with_inline_questions_reaches_review(tmp_path, monkeypatch) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-test")
    state, ring = install(monkeypatch.setattr, signed_in=True, profile=ready_profile())
    forms = FormServer((DEMO,))
    forms.start()
    port, debug_port = _free_port(), _free_port()
    headed = os.environ.get("POCKET_E2E_HEADED") == "1"
    app = create_app(port=port, launch=https_forms(forms.base, headless=not headed, debug_port=debug_port))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    _wait_http(base + "/")
    env_before = dict(os.environ)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(base + "/")
            expect(page.locator("#identity")).to_have_text(EMAIL)
            page.reload()
            expect(page.locator("#identity")).to_have_text(EMAIL)
            expect(page.locator("#apply")).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / "app-signed-in.png"), full_page=True)

            page.locator("#jobUrl").fill(FORMS_ORIGIN + "/form/prototype-demo")
            page.locator("#fill").click()
            expect(page.locator("#fill")).to_be_disabled()
            answered = answer_until_done(page)
            page.screenshot(path=str(ARTIFACTS / "app-review.png"), full_page=True)

            assert page.locator("#fillResult").get_attribute("data-status") == "stopped", page.locator("#fillLog").inner_text()
            assert len(answered) == 2 and "Phone Device Type" in answered[0] and "How Did You Hear" in answered[1]
            expect(page.locator("#runList")).to_contain_text(FORMS_ORIGIN + "/form/prototype-demo")
            expect(page.locator("#fill")).to_be_enabled()

            filler = filler_page(playwright, debug_port)
            filler.screenshot(path=str(ARTIFACTS / "filler-review.png"), full_page=True)
            assert filler.locator(".step.active h2").inner_text() == "Review"
            assert filler.get_by_label("First Name", exact=True).input_value() == "Jordan"
            assert filler.get_by_label("Resume", exact=True).evaluate("el => el.files.length") == 1
            assert filler.evaluate("() => window.__pocketSubmits") == 0
            assert "/submitted" not in filler.url
            browser.close()
    finally:
        server.should_exit = True
        forms.stop()
    assert [record[1:3] for record in state.records] == [(FORMS_ORIGIN + "/form/prototype-demo", "stopped")]
    assert "how_did_you_hear" in {key for key, *_ in state.saved_answers}
    assert ring.entries["refresh"] in state.refresh
    assert dict(os.environ) == env_before
