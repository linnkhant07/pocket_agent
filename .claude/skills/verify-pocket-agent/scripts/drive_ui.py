"""Drive the app like a user: paste a fictional form URL, click Fill, answer inline questions, wait for the result.

Usage (after `source $RUN/env`, launched with --ui):
    uv run python .claude/skills/verify-pocket-agent/scripts/drive_ui.py FORM_ID [--answer TEXT] [--expect review|blocked|stopped]
Writes screenshots and result.json to $RUN/evidence/ui-<FORM_ID>/. Exit 0 when the fill ends with the expected
status and the filler page shows no submission.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

FORMS_ORIGIN = "https://forms.pocket.test"


def answer(page, question: str, text: str) -> None:
    field = page.locator("#askValue")
    kind = field.evaluate("el => el.tagName.toLowerCase() + '/' + (el.type || '')")
    if kind.startswith("select"):
        options = field.locator("option").all_text_contents()
        field.select_option("Referral" if "Referral" in options else options[0])
    elif kind.endswith("/date"):
        field.fill("2026-06-15")
    else:
        field.fill("80000" if "salary" in question.lower() else text)
    page.locator("#askSend").click()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("form_id")
    parser.add_argument("--answer", default="Sam Rivera", help="Text for free-text questions")
    parser.add_argument("--expect", choices=["review", "blocked", "stopped"], default="stopped",
                        help="Fictional forms end on 'Step N of N', which the engine reports as stopped")
    args = parser.parse_args()
    ui, run, debug_port = os.environ["UI"], Path(os.environ["RUN"]), os.environ["FILLER_CDP"]
    out = run / "evidence" / f"ui-{args.form_id}"
    out.mkdir(parents=True, exist_ok=True)
    url = f"{FORMS_ORIGIN}/form/{args.form_id}"
    answered: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(ui + "/")
        expect(page.locator("#apply")).to_be_visible(timeout=15000)
        page.locator("#jobUrl").fill(url)
        page.screenshot(path=str(out / "01-before-fill.png"), full_page=True)
        page.locator("#fill").click()
        for _ in range(16):
            page.wait_for_function("() => !document.querySelector('#askCard').hidden || document.querySelector('#fillResult').textContent",
                                   timeout=90000)
            if page.locator("#fillResult").inner_text():
                break
            question = page.locator("#askQuestion").inner_text()
            answered.append(question)
            page.screenshot(path=str(out / f"02-question-{len(answered)}.png"), full_page=True)
            answer(page, question, args.answer)
        page.screenshot(path=str(out / "03-end.png"), full_page=True)
        status = page.locator("#fillResult").get_attribute("data-status")
        expect(page.locator("#runList")).to_contain_text(url)
        filler_browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
        filler = [p for c in filler_browser.contexts for p in c.pages if p.url.startswith(url)][-1]
        filler.screenshot(path=str(out / "04-filler.png"), full_page=True)
        result = {
            "form": args.form_id, "expect": args.expect, "status": status,
            "filler_step": filler.locator(".step.active h2").first.inner_text() if filler.locator(".step.active h2").count() else None,
            "submits": filler.evaluate("() => window.__pocketSubmits || 0"),
            "questions_answered": answered,
            "fill_log": page.locator("#fillLog").inner_text(),
            "result_text": page.locator("#fillResult").inner_text(),
            "history": page.locator("#runList").inner_text(),
            "saved_answers": page.locator("#questionList").inner_text(),
        }
        browser.close()
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("form", "status", "filler_step", "submits", "questions_answered")}))
    return 0 if status == args.expect and result["submits"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
