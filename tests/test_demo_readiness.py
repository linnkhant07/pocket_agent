"""Regression checks for completion, transition verification, and reporting."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from pocket_agent.cli import cmd_fill
from pocket_agent.forms.engine import FormRun, UserAnswer, fill_form
from pocket_agent.forms.memory import Memory


@pytest.mark.parametrize('status,expected', [('stopped', 2), ('review', 0)])
def test_unattended_exit_requires_review(home, monkeypatch, status, expected):
    (home / 'profile.yaml').write_text(Path('examples/profile.yaml').read_text())
    class Page:
        pass
    class Browser:
        def new_page(self): return Page()
        def close(self): pass
    class PW:
        chromium = SimpleNamespace(launch=lambda **_: Browser())
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: PW())
    monkeypatch.setattr('pocket_agent.forms.engine.fill_form', lambda *_, **__: FormRun(url='https://example.test', status=status))
    args = SimpleNamespace(url='https://example.test', model='none', resume=None, workday_accounts=False,
                           unattended=True, headless=True, close_on_finish=True)
    assert cmd_fill(args) == expected
    assert json.loads((home/'last-fill-report.json').read_text())['status'] == status


@pytest.mark.parametrize('html', [
    '<p id=progress>Step 2 of 2 Review</p><label>Email<input type=email required></label><button type=submit>Submit</button>',
    '<p id=progress>Step 2 of 2 Review</p><label>Email<input type=email required value=invalid></label><button type=submit>Submit</button>',
])
def test_incomplete_review_is_blocked(tmp_path, html):
    memory = Memory(tmp_path/'answers.sqlite')
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
                result = fill_form(page, 'https://example.test', memory, None, lambda _: UserAnswer('', skip=True))
                assert result.status == 'blocked'
                assert result.unresolved or result.validation_errors
                assert result.submits == 0
            finally: browser.close()
    finally: memory.close()


def test_generic_navigation_waits_for_delayed_next_step(tmp_path):
    memory = Memory(tmp_path/'answers.sqlite')
    memory.put('first_name','Alex')
    html = '''<h2>Contact</h2><label>First Name<input required></label>
    <button type=button id=next>Next</button><script>document.getElementById("next").onclick=()=>setTimeout(()=>document.body.innerHTML='<p id=progress>Step 2 of 2 Review</p><button type=submit>Submit</button>',1200)</script>'''
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
                result = fill_form(page,'https://example.test',memory,None,lambda _: UserAnswer('',skip=True))
                assert result.status == 'review' and not result.error
                assert result.submits == 0
            finally: browser.close()
    finally: memory.close()


def test_final_numbered_step_without_review_or_submit_is_not_success(tmp_path):
    memory = Memory(tmp_path/'answers.sqlite')
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body='<p id=progress>Step 3 of 3 My Information</p><h2>My Information</h2>', content_type='text/html'))
                events=[]
                result=fill_form(page,'https://example.test',memory,None,lambda _:UserAnswer('',skip=True),events=events.append)
                assert result.status == 'stopped'
                assert not any(event['type']=='review' for event in events)
            finally: browser.close()
    finally: memory.close()


def test_reinitializing_preserves_resume_and_account_settings(home):
    import yaml
    from pocket_agent.cli import cmd_init
    config = {'resume_path': '/private/tmp/my-real-resume.pdf', 'accounts': {'password': 'test-only-password'}, 'fill': {'unattended': True}}
    (home/'config.yaml').write_text(yaml.safe_dump(config))
    assert cmd_init(SimpleNamespace(model=None, no_pull=True, yes=True)) == 0
    saved=yaml.safe_load((home/'config.yaml').read_text())
    for key,value in config.items():
        assert saved[key] == value
