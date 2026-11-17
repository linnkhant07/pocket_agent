"""Regression tests for the bc6a60e bug report. Fictional forms and temp homes only."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pocket_agent.forms.engine import UserAnswer, fill_form
from pocket_agent.forms.guard import classify_button, progress_is_last
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.values import resolve_option


def _fill(html: str, memory: Memory, ask=None, **kwargs):
    from playwright.sync_api import sync_playwright

    ask = ask or (lambda _item: UserAnswer("", skip=True))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
            return fill_form(page, "https://jobs.example.test/apply", memory, None, ask, **kwargs)
        finally:
            browser.close()


def test_a1_form_submit_is_counted_and_the_run_fails(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    html = """
    <form id=f action="/submitted" method="post">
      <p id=progress>Step 1 of 2</p><h2>Contact</h2>
      <label>Email<input name=email type=email required value="ada@example.test"></label>
      <button type=button id=next>Next</button>
    </form>
    <script>document.getElementById('next').onclick = () => document.getElementById('f').submit();</script>
    """
    try:
        result = _fill(html, memory)
        assert result.submits >= 1
        assert result.error
        assert result.status == "blocked"
    finally:
        memory.close()


def test_a1_request_submit_is_counted(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    html = """
    <form id=f action="/submitted" method="post">
      <p id=progress>Step 1 of 2</p>
      <label>Email<input type=email required value="ada@example.test"></label>
      <button type=button id=next>Next</button>
    </form>
    <script>document.getElementById('next').onclick = () => document.getElementById('f').requestSubmit();</script>
    """
    try:
        result = _fill(html, memory)
        assert result.submits >= 1 and result.status == "blocked"
    finally:
        memory.close()


@pytest.mark.parametrize("progress", ["Page 2 of 2", "2/2", "2 of 2", "Step 2 of 2"])
def test_a2_last_page_patterns(progress):
    assert progress_is_last(progress)
    assert not progress_is_last("Page 1 of 2")
    assert not progress_is_last("0 of 0")


@pytest.mark.parametrize("heading", ["Confirm your application", "Confirmation", "Summary", "Reviewing your application", "Send application", "Finalize"])
def test_a2_final_headings_are_not_clicked(heading):
    assert classify_button("Continue", button_type="button", heading=heading) == "submit"
    assert classify_button("Next", button_type="button", heading="Contact") == "forward"


def test_a2_continue_on_confirm_page_does_not_post(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    html = """
    <p id=progress>Page 2 of 2</p><h1>Confirm your application</h1>
    <label>Email<input type=email required value="ada@example.test"></label>
    <button type=button id=next>Continue</button>
    <script>document.getElementById('next').onclick = () => { document.body.dataset.posted = '1'; };</script>
    """
    try:
        result = _fill(html, memory)
        assert result.submits == 0
    finally:
        memory.close()


def test_a3_cancel_application_is_not_dismissed():
    assert classify_button("Cancel application", in_dialog=True) != "dismiss"
    assert classify_button("Close", in_dialog=True) == "dismiss"
    assert classify_button("No thanks", in_dialog=True) == "dismiss"
    assert classify_button("Confirm close", button_type="button") == "submit"


def test_b1_no_does_not_pick_hispanic_or_non_protected_veteran():
    hispanic = ["Hispanic or Latino", "Not Hispanic or Latino"]
    veteran = ["Protected Veteran", "Non-Protected Veteran", "Not a veteran"]
    assert resolve_option("No", hispanic, key="hispanic") != "Hispanic or Latino"
    assert resolve_option("No", veteran, key="veteran_status") != "Non-Protected Veteran"
    assert resolve_option("Yes", ["Yes", "Yes, with sponsorship"]) == "Yes"
    assert resolve_option("Asian", ["East Asian", "South Asian"], key="race") is None
    assert resolve_option("50000", ["$150,000 - $200,000"], key="salary_expectation") is None


def test_b1_ambiguous_yes_asks():
    assert resolve_option("Yes", ["Yes, I will", "Yes, with sponsorship"]) is None


def test_b2_blank_eligibility_is_not_seeded_as_no(tmp_path):
    from pocket_agent.forms.seed import seed_memory

    memory = Memory(tmp_path / "memory.sqlite")
    try:
        seed_memory(memory, {"name": "Ada Example", "citizenship": {}, "location": {}, "education": [], "preferences": {}})
        for key in ("requires_sponsorship", "willing_to_relocate", "remote_ok", "clearance", "work_authorization"):
            assert memory.get(key) is None
    finally:
        memory.close()


def test_b2_profile_does_not_default_relocate_or_remote():
    from pocket_agent.profile import validate_profile

    profile = validate_profile({
        "name": "Ada Example",
        "email": "ada@example.test",
        "education": [{"school": "Example University", "degree": "BS"}],
        "skills": ["Python"],
        "preferences": {"roles": ["Example role"]},
        "canary": "example-canary",
    })
    assert "willing_to_relocate" not in profile.get("location", {})
    assert "remote_ok" not in profile.get("location", {})


def test_b3_start_date_under_questions_is_availability():
    from pocket_agent.forms.match import match_label, section_of

    assert section_of("Additional Questions") == "questions"
    match = match_label("Start date", section="questions")
    assert match is not None and match.key == "available_start"
    work = match_label("Start date", section="work")
    assert work is not None and work.key == "work.start"


def test_b4_generic_explain_is_not_reused_across_pages(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    memory.put("how_did_you_hear", "Referral", always_use=False, source="answer", label="How did you hear about us?")
    html = """
    <h2>Questions</h2><label>How did you learn of this opening?
      <select required><option value="">Select</option><option>Referral</option><option>LinkedIn</option></select>
    </label><button type=submit>Submit</button>
    """
    try:
        result = _fill(html, memory, ask=lambda _item: UserAnswer("", skip=True))
        assert any(item.key == "how_did_you_hear" and item.action == "fill" and item.value == "Referral" for item in result.outcomes)
    finally:
        memory.close()


def test_b4_label_key_uses_the_full_label():
    from pocket_agent.forms.engine import _label_key
    from pocket_agent.forms.reader import ExtractedField

    def field(label):
        return ExtractedField("", label, "text", True, [], "", "", "", "text")

    contractor = _label_key(field("Will you work as a contractor for this role and this team?"), "https://a.example.test/apply")
    employee = _label_key(field("Will you work as a full time employee for this role and this team?"), "https://a.example.test/apply")
    assert contractor != employee
    explain_a = _label_key(field("If yes, please explain"), "https://a.example.test/jobs/1")
    explain_b = _label_key(field("If yes, please explain"), "https://b.example.test/jobs/9")
    assert explain_a != explain_b


def test_b5_unchecked_required_checkbox_is_unresolved(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    html = """
    <h2>Review</h2>
    <label>I acknowledge the statements <input type=checkbox required aria-required=true></label>
    <button type=submit>Submit</button>
    """
    try:
        result = _fill(html, memory)
        assert any("acknowledge" in label.casefold() for label in result.unresolved)
    finally:
        memory.close()


def test_c_canonical_veteran_and_salary(tmp_path):
    from pocket_agent.forms.facts import canonicalize, pick_option, render

    assert canonicalize("veteran_status", "I'm not a veteran") == "not_veteran"
    assert canonicalize("veteran_status", "I am not a protected veteran") == "not_veteran"
    assert pick_option("veteran_status", "not_veteran", ["Yes", "No"], "Are you a protected veteran?") == "No"
    assert pick_option("hispanic", "no", ["Hispanic or Latino", "Not Hispanic or Latino"], "Hispanic or Latino") == "Not Hispanic or Latino"
    assert pick_option("hispanic", "No", ["Hispanic or Latino", "Not Hispanic or Latino"], "Ethnicity") != "Hispanic or Latino"
    picked = pick_option("salary_expectation", "120000", ["$100,000 - $130,000", "$150,000 - $200,000"], "Salary")
    assert picked == "$100,000 - $130,000"
    assert pick_option("salary_expectation", "50000", ["$150,000 - $200,000"], "Salary") is None
    rendered = render("available_start", "immediately", kind="date", options=[], input_type="date")
    assert rendered == "2026-11-04"


def test_c_mapping_payload_has_no_answer_values():
    from pocket_agent.forms.facts import mapping_messages, public_catalog

    class _Entry:
        value = "secret-veteran-answer"

    class _Memory:
        def get(self, key):
            return _Entry() if key == "veteran_status" else None

    catalog = public_catalog(_Memory())
    blob = str(mapping_messages("Are you a veteran?", ["Yes", "No"], catalog))
    assert "secret-veteran-answer" not in blob
    assert "veteran_status" in blob


def test_c5_fieldset_checkboxes_are_one_question():
    from playwright.sync_api import sync_playwright
    from pocket_agent.forms.reader import extract_page

    html = """
    <fieldset><legend>Race (select all that apply)</legend>
      <label><input type=checkbox value=asian> Asian</label>
      <label><input type=checkbox value=white> White</label>
    </fieldset>
    <label><input type=checkbox> I acknowledge the statements</label>
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html)
            fields = extract_page(page).fields
        finally:
            browser.close()
    kinds = {field.label: field.kind for field in fields}
    assert kinds.get("Race (select all that apply)") == "multicheck"
    assert any(field.kind == "checkbox" and "acknowledge" in field.label.casefold() for field in fields)


def test_d2_ai_context_drops_contact_and_demographics(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    seen = {}

    class Decider:
        model = "none"

        def generate(self, **kwargs):
            seen["context"] = kwargs.get("job_context") or ""
            return None

    html = """
    <h2>Equal Employment</h2><p>Gender Female Race Two or More Races</p>
    <h2>Contact</h2><p>Phone 415-555-0100 Email ada@example.test</p>
    <h2>Experience</h2><p>Built the example project with Python.</p>
    <label>Why this role?<textarea required></textarea></label>
    <button type=submit>Submit</button>
    """
    try:
        _fill(html, memory, llm=Decider())
    finally:
        memory.close()
    context = seen.get("context", "")
    assert "ada@example.test" not in context
    assert "415-555-0100" not in context
    assert "Female" not in context


def test_d5_api_key_error_keeps_the_refresh_token(tmp_path, monkeypatch):
    from pocket_agent.account.auth import _access_token
    from pocket_agent.account.client import AccountError

    class Ring:
        def __init__(self):
            self.deleted = False
            self.token = "refresh-token"

        def get_password(self, *_args):
            return self.token

        def delete_password(self, *_args):
            self.deleted = True

    class Boom:
        def __init__(self, *_args, **_kwargs):
            pass

        def refresh(self, _token):
            raise AccountError("Invalid API key", 401, provider_code="invalid_api_key")

        def close(self):
            pass

    monkeypatch.setattr("pocket_agent.account.auth.AccountClient", Boom)
    ring = Ring()
    with pytest.raises(AccountError):
        _access_token(ring, "https://example.test", "anon")
    assert ring.deleted is False


def test_e6_serialization_failure_is_a_conflict():
    from pocket_agent.account.client import AccountClient, AccountError

    class Response:
        is_error = True
        status_code = 500

        def json(self):
            return {"code": "PT409", "message": "conflict"}

    class Http:
        def request(self, *_args, **_kwargs):
            return Response()

        def close(self):
            pass

    client = AccountClient.__new__(AccountClient)
    client._http = Http()
    with pytest.raises(AccountError) as caught:
        client._request("POST", "/rest/v1/rpc/save_account_profile")
    assert caught.value.status_code == 409


def test_e9_headless_interactive_is_rejected(monkeypatch):
    from types import SimpleNamespace
    from pocket_agent.cli import cmd_fill

    monkeypatch.setattr("pocket_agent.cli.load_config", lambda: {})
    args = SimpleNamespace(url="https://jobs.example.test/apply", model="none", resume=None,
                           workday_accounts=False, unattended=False, headless=True, close_on_finish=False)
    assert cmd_fill(args) == 2


def test_e10_review_event_only_when_status_is_review(tmp_path):
    memory = Memory(tmp_path / "memory.sqlite")
    html = "<p id=progress>Step 3 of 3 My Information</p><h2>My Information</h2>"
    events = []
    try:
        result = _fill(html, memory, events=events.append)
        assert result.status == "stopped"
        assert not any(event.get("type") == "review" for event in events)
    finally:
        memory.close()


def test_e11_blank_pdf_says_no_readable_text(tmp_path, monkeypatch):
    from pocket_agent.account.resume_import import extract_resume

    def blank(_path):
        return {"text": "   "}

    monkeypatch.setattr("pocket_agent.account.resume_import.parse_resume", blank)
    with pytest.raises(ValueError, match="No readable text found"):
        extract_resume(b"%PDF-1.4 fictional")


def test_f_uncited_tool_urls_are_not_sources():
    from pocket_agent.forms.salary import MarketSalary

    data = {
        "action": "salary", "period": "annual", "role": "Example Engineer", "location": "London, UK",
        "currency": "USD", "low": 100, "high": 120, "evidence_date": "2026-01-01",
        "source_urls": ["https://example.test/salary"],
    }
    assert MarketSalary.validate(data, ["https://example.test/salary"], []) is None
    data["currency"] = "GBP"
    data["location"] = "Austin, TX"
    options = ["90000 USD"]
    assert MarketSalary.validate(data, ["https://example.test/salary"], options) is None


def test_g_post_routes_are_allowed_and_a_submit_tool_is_not():
    app = Path("src/pocket_agent/ui/app.py").read_text(encoding="utf-8")
    cloud = Path("src/pocket_agent/forms/cloud.py").read_text(encoding="utf-8")
    assert "@app.post(" in app
    assert "client.post(" in cloud
    assert "submit_application" not in app and "submit_application" not in cloud
