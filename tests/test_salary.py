from datetime import date
from types import SimpleNamespace
import json

import pytest

from pocket_agent.forms.salary import MarketSalary

URL = 'https://employer.example/careers/new-grad'


def benchmark(**updates):
    data = dict(action='salary', role='Software engineer, new grad', location='Austin, TX',
                currency='USD', period='annual', low=100000, high=120000,
                source_urls=[URL], evidence_date=date.today().isoformat())
    return {**data, **updates}


def test_sourced_midpoint():
    answer = MarketSalary.validate(benchmark(), [URL], [])
    assert answer.value == '110000' and answer.evidence == [URL]


@pytest.mark.parametrize('updates', [
    {'currency': ''}, {'location': ''}, {'period': 'hourly'},
    {'low': True}, {'high': float('inf')}, {'low': -1}, {'high': 90000},
    {'evidence_date': '2020-01-01'}, {'source_urls': ['https://invented.example']},
    {'source_urls': []}, {'action': 'ask'},
])
def test_unsupported_salary_abstains(updates):
    assert MarketSalary.validate(benchmark(**updates), [URL], []) is None


def test_unknown_dropdown_is_not_guessed():
    assert MarketSalary.validate(benchmark(), [URL], ['100k to 120k']) is None


def test_research_is_cached_and_does_not_send_resume_or_profile():
    class Backend:
        def __init__(self): self.calls = []; self.logs = []
        def log(self, message): self.logs.append(message)
        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return SimpleNamespace(text=json.dumps(benchmark()), sources=[URL])
    backend = Backend()
    salary = MarketSalary(backend, 'test-model')
    payload = dict(label='Expected annual base salary in USD?', kind='text', options=[], job_context='Software engineer, new grad, Austin, TX')
    assert salary.generate(**payload).value == '110000'
    assert salary.generate(**payload).value == '110000'
    assert len(backend.calls) == 1
    assert backend.calls[0][1]['web_search'] is True
    assert URL in backend.logs[0]


def test_salary_fills_and_saved_demographics_bypass_ai(tmp_path):
    from playwright.sync_api import sync_playwright
    from pocket_agent.forms.engine import fill_form
    from pocket_agent.forms.memory import Memory
    from pocket_agent.forms.seed import seed_memory
    from pocket_agent.forms.answers import Answer
    memory = Memory(tmp_path / 'memory.sqlite')
    seed_memory(memory, {'form_answers': {
        'race': {'value': 'Self-identified answer', 'always_use': True},
        'hispanic': {'value': 'No', 'always_use': True},
    }})
    calls = []
    class Salary:
        def generate(self, **kwargs):
            calls.append(kwargs['label'])
            return Answer('110000', [URL], 'market_salary')
    decider = SimpleNamespace(available=True, salary=Salary(), generate=lambda **kwargs: pytest.fail('Demographics must never go to AI'))
    html = '''<label for=race>Race</label><select id=race><option></option><option>Self-identified answer</option></select>
    <label for=hispanic>Are you Hispanic/Latino?</label><select id=hispanic><option></option><option>No</option></select>
    <label>Expected salary<input required></label><button type=submit>Submit</button>'''
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
                result = fill_form(page, 'https://example.test', memory, None,
                    lambda _: pytest.fail('Explicit answers and sourced salary should fill'), decider)
                assert page.get_by_label('Race', exact=True).input_value() == 'Self-identified answer'
                assert page.get_by_label('Are you Hispanic/Latino?', exact=True).input_value() == 'No'
                assert page.get_by_label('Expected salary', exact=True).input_value() == '110000'
                assert calls == ['Expected salary'] and result.submits == 0
                assert memory.get('salary_expectation') is None
            finally:
                browser.close()
    finally:
        memory.close()
