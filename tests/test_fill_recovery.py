from pathlib import Path

import pytest
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from pocket_agent.cli import cmd_fill
from pocket_agent.forms.engine import FormRun, UserAnswer, fill_form
from pocket_agent.forms.memory import Memory


def test_cli_resumes_same_page_after_user_fixes_error(home, monkeypatch):
    (home/'profile.yaml').write_text(Path('examples/profile.yaml').read_text())
    page=object()
    class Browser:
        def new_page(self): return page
        def close(self): pass
    class PW:
        chromium=SimpleNamespace(launch=lambda **_:Browser())
        def __enter__(self): return self
        def __exit__(self,*_): pass
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:PW())
    calls=[]
    def fill(actual,*_,**kwargs):
        assert actual is page
        calls.append(kwargs)
        if len(calls)==1:
            return FormRun(url='https://example.test',status='blocked',error='City rejected')
        return FormRun(url='https://example.test',status='review')
    monkeypatch.setattr('pocket_agent.forms.engine.fill_form',fill)
    monkeypatch.setattr('builtins.input',lambda *_:'')
    args=SimpleNamespace(url='https://example.test',model='none',resume=None,workday_accounts=False,unattended=False,close_on_finish=True)
    assert cmd_fill(args)==0
    assert calls==[{}, {'resume':True,'preserve_existing':True}]


@pytest.mark.parametrize("saved", [False, True])
def test_rejected_answer_is_reasked_and_valid_answer_continues(tmp_path, saved):
    memory=Memory(tmp_path/'memory.sqlite')
    if saved:
        memory.put('email','previous-invalid-email',always_use=True)
    replies=iter(['not-an-email','alex@example.test'])
    questions=[]
    html='''<p id=progress>Step 1 of 2</p><h2>Contact</h2><label>Email<input type=email required></label>
    <button type=button id=next>Next</button><script>document.getElementById('next').onclick=()=>{document.body.innerHTML='<p id=progress>Step 2 of 2 Review</p><button type=submit>Submit</button>'}</script>'''
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
                def ask(item):
                    questions.append(item.question)
                    return UserAnswer(next(replies))
                result=fill_form(page,'https://example.test',memory,None,ask)
                assert len(questions)==2
                assert result.status=='review' and result.submits==0
                assert memory.get('email').value=='alex@example.test'
            finally: browser.close()
    finally: memory.close()


def test_resume_preserves_manual_correction_instead_of_bad_saved_value(tmp_path):
    memory=Memory(tmp_path/'memory.sqlite')
    memory.put('city','Wrong City')
    html='''<p id=progress>Step 1 of 2</p><h2>Contact</h2><label>City<input required></label>
    <button type=button id=next>Next</button><script>document.getElementById('next').onclick=()=>{document.body.innerHTML='<p id=progress>Step 2 of 2 Review</p><button type=submit>Submit</button>'}</script>'''
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
                page.goto('https://example.test')
                page.get_by_label('City').fill('Correct City')
                result=fill_form(page,'https://example.test',memory,None,lambda _:UserAnswer('',skip=True),resume=True,preserve_existing=True)
                assert result.status=='review' and result.submits==0
                assert result.outcomes[0].value=='Correct City'
                assert result.outcomes[0].action=='keep'
            finally: browser.close()
    finally: memory.close()


def test_answer_uses_fresh_field_after_page_changes_while_waiting(tmp_path):
    memory=Memory(tmp_path/'memory.sqlite')
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.fulfill(body='<h2>Contact</h2><label>Email<input type=email required></label><button type=submit>Submit</button>',content_type='text/html'))
                def ask(_):
                    page.locator('body').evaluate("el => el.insertAdjacentHTML('afterbegin', '<label>Phone Extension<input></label>')")
                    return UserAnswer('alex@example.test')
                result=fill_form(page,'https://example.test',memory,None,ask)
                assert page.get_by_label('Email',exact=True).input_value()=='alex@example.test'
                assert page.get_by_label('Phone Extension',exact=True).input_value()==''
                assert result.status=='review' and result.submits==0
            finally: browser.close()
    finally: memory.close()


@pytest.mark.parametrize('html,expected', [
    ('<h1>Software Engineering Internship</h1><p>Build two-factor authentication and verification code services.</p><label>First Name<input required></label>', ''),
    ('<h1>Verify your email</h1><p>Check your inbox to continue.</p>', 'verification'),
    ('<h1>Authentication</h1><label>Enter code<input autocomplete=one-time-code></label>', 'verification'),
    ('<h1>Application</h1><input autocomplete=one-time-code hidden>', ''),
])
def test_verification_requires_visible_prompt_or_code_field(html, expected):
    from pocket_agent.forms.reader import extract_page
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.set_content(html)
            assert extract_page(page).gate == expected
        finally: browser.close()


@pytest.mark.parametrize('html,expected', [
    ('<h1>Application</h1><label>First Name<input required></label><footer>Already have an account? Sign in to apply</footer>', ''),
    ('<h1>Software Engineer</h1><p>Create an account to receive job alerts</p><button>Apply</button>', 'start'),
    ('<h1>Sign in to apply</h1><label>Email<input type=email></label>', 'login'),
    ('<h1>Application</h1><label>Password<input type=password></label>', 'login'),
])
def test_login_requires_auth_controls_not_body_copy(html,expected):
    from pocket_agent.forms.reader import extract_page
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.set_content(html)
            snapshot=extract_page(page)
            assert snapshot.gate==expected
            if expected=='login': assert snapshot.gate_reason
        finally: browser.close()
