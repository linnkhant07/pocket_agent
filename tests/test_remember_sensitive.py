"""Sensitive answers typed during a fill are remembered locally and reused for the same fact.

They stay on this computer: the ask card's checkbox is an opt-out, and remembering one
does not upload it or place it in a model prompt. Fictional forms only.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pocket_agent.account.runner import learned_answers
from pocket_agent.cli import sensitive_opt_out
from pocket_agent.forms.answers import AnsweringDecider
from pocket_agent.forms.engine import Asked, FormRun, UserAnswer, fill_form
from pocket_agent.forms.facts import mapping_messages, public_catalog
from pocket_agent.forms.memory import Memory

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "pocket_agent" / "ui" / "static"


def _choice(label: str, options: tuple[str, ...]) -> str:
    choices = "".join(f"<option>{option}</option>" for option in options)
    return f"<label>{label}<select required><option></option>{choices}</select></label>"


def _page(*fields: str) -> str:
    body = "".join(fields)
    return f"<h2>Questions</h2>{body}<button type=submit>Submit</button>"


def _run(memory: Memory, pages: dict[str, str], ask):
    from playwright.sync_api import sync_playwright

    def fulfill(route):
        body = pages.get(route.request.url.split("?")[0])
        if body is None:
            route.abort()
            return
        route.fulfill(body=body, content_type="text/html")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", fulfill)
            runs = [fill_form(page, url, memory, None, ask) for url in pages]
            values = page.locator("select, textarea, input").evaluate_all("els => els.map(el => el.value)")
            return runs, values
        finally:
            browser.close()


def test_sensitive_answer_is_remembered_by_default(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    asked = []

    def ask(item):
        asked.append(item.label)
        return UserAnswer("I am not a protected veteran")

    pages = {
        "https://first.example.test/apply": _page(_choice("Veteran status", ("I am a protected veteran", "I am not a protected veteran"))),
        "https://second.example.test/apply": _page(_choice("Are you a protected veteran?", ("Yes", "No"))),
    }
    try:
        runs, values = _run(memory, pages, ask)
        saved = memory.get("veteran_status")
    finally:
        memory.close()
    assert asked == ["Veteran status"]
    assert saved is not None and saved.always_use and saved.sensitive and saved.value == "not_veteran"
    assert values == ["No"]
    assert runs[1].submits == 0 and all(not item.asked for item in runs[1].outcomes)


def test_opting_out_of_remember_asks_again(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    asked = []

    def ask(item):
        asked.append(item.label)
        return UserAnswer("80000", forget=True)

    pages = {
        "https://first.example.test/apply": _page("<label>Desired annual salary (USD)<input required></label>"),
        "https://second.example.test/apply": _page("<label>What are your pay expectations?<input required></label>"),
    }
    try:
        runs, _values = _run(memory, pages, ask)
        saved = memory.get("salary_expectation")
    finally:
        memory.close()
    assert asked == ["Desired annual salary (USD)", "What are your pay expectations?"]
    assert saved is not None and saved.always_use is False
    assert runs[0].submits == 0 and runs[1].submits == 0


def test_remembered_sensitive_answer_is_reused_on_a_different_wording(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    asked = []

    def ask(item):
        asked.append(item.label)
        return UserAnswer("No")

    pages = {
        "https://first.example.test/apply": _page(_choice("Are you Hispanic or Latino?", ("Yes", "No"))),
        "https://second.example.test/jobs/other": _page(_choice("Hispanic or Latino?", ("Hispanic or Latino", "Not Hispanic or Latino"))),
    }
    try:
        runs, values = _run(memory, pages, ask)
    finally:
        memory.close()
    assert asked == ["Are you Hispanic or Latino?"]
    assert values == ["Not Hispanic or Latino"]
    assert runs[1].submits == 0


def test_sponsorship_answer_does_not_fill_work_authorization(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    asked = []

    def ask(item):
        asked.append(item.label)
        return UserAnswer("Yes")

    pages = {
        "https://first.example.test/apply": _page(_choice("Will you now or in the future require sponsorship?", ("Yes", "No"))),
        "https://second.example.test/apply": _page(
            _choice("Will you require visa sponsorship for employment?", ("Yes", "No")),
            _choice("Are you legally authorized to work in the United States?", ("Yes", "No")),
        ),
    }
    try:
        runs, values = _run(memory, pages, ask)
        claims = [key for key in memory.keys() if key.startswith("status.claim.")]
    finally:
        memory.close()
    assert asked[0] == "Will you now or in the future require sponsorship?"
    assert "Will you require visa sponsorship for employment?" not in asked
    assert "Are you legally authorized to work in the United States?" in asked
    assert values[0] == "Yes"
    assert any("needs_sponsorship" in key for key in claims)
    assert runs[0].submits == 0 and runs[1].submits == 0


def test_remembered_sensitive_answers_stay_local_and_out_of_model_prompts(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    memory.put("gender", "GenderSecretValue", always_use=True, sensitive=True, source="answer", label="Gender")
    memory.put("salary_expectation", "SalarySecret80000", always_use=True, sensitive=True, source="answer")
    memory.put("requires_sponsorship", "SponsorSecretYes", always_use=True, sensitive=True, source="answer")
    memory.put("status.claim.needs_sponsorship:US", "Yes", always_use=True, sensitive=True, source="answer")
    memory.put("referrer_name", "Sam Rivera", source="answer", label="Referrer name")
    path = Path(memory.path)
    memory.close()

    uploaded = learned_answers(path)
    assert uploaded == [("referrer_name", "Sam Rivera", False, "referrer name")]
    assert not any(key in {"gender", "salary_expectation", "requires_sponsorship"} or key.startswith("status.") for key, *_ in uploaded)

    class _Entry:
        def __init__(self, value):
            self.value = value

    class _Memory:
        def get(self, key):
            return _Entry({"veteran_status": "secret-veteran-answer", "salary_expectation": "SalarySecret80000",
                           "requires_sponsorship": "SponsorSecretYes"}.get(key))

    catalog = public_catalog(_Memory())
    payload = str(mapping_messages("Will you require sponsorship?", ["Yes", "No"], catalog))
    assert "secret-veteran-answer" not in payload and "SalarySecret80000" not in payload and "SponsorSecretYes" not in payload

    decider = AnsweringDecider(
        {
            "experience": [{"title": "Library aide", "highlights": ["Shelved books."]}],
            "form_answers": {
                "gender": {"value": "GenderSecretValue"},
                "salary_expectation": {"value": "SalarySecret80000"},
                "requires_sponsorship": {"value": "SponsorSecretYes"},
                "status.claim.needs_sponsorship:US": {"value": "SponsorClaimYes"},
                "skills_note": {"value": "Python"},
            },
        },
        "Built small tools.",
        backend=SimpleNamespace(),
    )
    facts = str(decider.facts)
    for secret in ("GenderSecretValue", "SalarySecret80000", "SponsorSecretYes", "SponsorClaimYes"):
        assert secret not in facts
    assert "Python" in facts


def test_generic_explain_is_not_reused_on_another_site(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    asked = []

    def ask(item):
        asked.append((item.label, item.key))
        return UserAnswer("Because the tides", always_use=True)

    pages = {
        "https://a.example.test/jobs/1": _page("<label>If yes, please explain<textarea required></textarea></label>"),
        "https://b.example.test/jobs/9": _page("<label>If yes, please explain<textarea required></textarea></label>"),
    }
    try:
        runs, _values = _run(memory, pages, ask)
    finally:
        memory.close()
    assert [label for label, _key in asked] == ["If yes, please explain", "If yes, please explain"]
    assert asked[0][1] != asked[1][1]
    assert runs[0].submits == 0 and runs[1].submits == 0


def test_cli_sensitive_prompt_remembers_unless_opt_out():
    assert sensitive_opt_out("") is False
    assert sensitive_opt_out("n") is False
    assert sensitive_opt_out("y") is True
    assert sensitive_opt_out("YES") is True


def test_ask_card_dont_remember_is_unchecked_by_default():
    from playwright.sync_api import sync_playwright

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace('<script defer src="/app.js"></script>', "")
    html = html.replace(
        "</body>",
        "<script>" + script + "\nwindow.__desk = desk; window.__showAsk = showAsk; window.__sendAnswer = sendAnswer;</script></body>",
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(status=401, body="{}", content_type="application/json"))
            page.set_content(html)
            page.wait_for_function("() => window.__showAsk")
            sent = page.evaluate(
                """() => {
                  const sent = [];
                  __desk.socket = {send: text => sent.push(JSON.parse(text))};
                  __showAsk({label: 'Gender', question: 'Gender?', kind: 'dropdown', options: ['Woman', 'Man'], sensitive: true, question_id: 'q1'});
                  const box = document.querySelector('#askRemember');
                  const before = {text: document.querySelector('#askRememberLabel').textContent, checked: box.checked, hidden: document.querySelector('#askRememberLabel').hidden};
                  document.querySelector('#askValue').value = 'Woman';
                  __sendAnswer(false);
                  box.checked = true;
                  __showAsk({label: 'Salary', question: 'Salary?', kind: 'text', options: [], sensitive: true, question_id: 'q2'});
                  const opted = {checked: document.querySelector('#askRemember').checked};
                  document.querySelector('#askValue').value = '80000';
                  document.querySelector('#askRemember').checked = true;
                  __sendAnswer(false);
                  __showAsk({label: 'Referrer', question: 'Who?', kind: 'text', options: [], sensitive: false, question_id: 'q3'});
                  const plain = {hidden: document.querySelector('#askRememberLabel').hidden};
                  document.querySelector('#askValue').value = 'Sam Rivera';
                  __sendAnswer(false);
                  return {before, opted, plain, sent};
                }"""
            )
        finally:
            browser.close()
    assert "Don't remember this" in sent["before"]["text"]
    assert sent["before"]["checked"] is False and sent["before"]["hidden"] is False
    assert sent["opted"]["checked"] is False
    assert sent["plain"]["hidden"] is True
    assert [item["always_use"] for item in sent["sent"]] == [True, False, False]
    assert all(item["type"] == "answer" for item in sent["sent"])


def test_ui_fill_remembers_a_sensitive_answer_and_does_not_upload_it(monkeypatch, tmp_path):
    from pocket_agent.ui import desk as desk_module
    from test_ui import make_client, receive_until

    recorded = []

    def fill_form(page, url, memory, resume, ask, decider, **options):
        answer = ask(Asked("salary_expectation", "Salary", "Salary?", sensitive=True))
        recorded.append(answer)
        if not answer.skip:
            memory.put("salary_expectation", answer.value, always_use=answer.always_use, sensitive=True, source="answer", label="Salary")
        return FormRun(url=url, status="review")

    monkeypatch.setattr(desk_module, "fill_form", fill_form)
    from account_fakes import ready_profile

    with make_client(monkeypatch, tmp_path, profile=ready_profile()) as client:
        with client.websocket_connect("ws://127.0.0.1:8000/ws") as socket:
            socket.send_json({"type": "start", "url": "https://jobs.example.test/apply", "model": "none"})
            receive_until(socket, "ask")
            socket.send_json({"type": "answer", "value": "80000", "always_use": True})
            assert receive_until(socket, "done")[-1]["status"] == "review"
        assert client.state.saved_answers == []
    assert recorded[0].always_use is True and recorded[0].forget is False and recorded[0].value == "80000"
