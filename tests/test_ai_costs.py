from types import SimpleNamespace

import pytest

from pocket_agent.forms.ai_cache import ResponseCache
from pocket_agent.forms.answers import AnsweringDecider
from pocket_agent.forms.cloud import CloudBackend


def backend(monkeypatch, **kwargs):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    return CloudBackend('gpt', log=lambda _: None, **kwargs)


def test_cache_reuses_response_across_runs_but_invalidates_on_changed_facts(tmp_path, monkeypatch):
    cache = ResponseCache(tmp_path / 'ai.sqlite')
    requests = []
    def request(*args, **kwargs):
        requests.append(args)
        return SimpleNamespace(text='{"action":"ask"}', sources=[])
    monkeypatch.setattr(CloudBackend, '_request', request)
    first = backend(monkeypatch, cache=cache)
    messages = [{'role': 'user', 'content': 'profile v1; job A; question X'}]
    first.complete(messages, model='test-model')
    second = backend(monkeypatch, cache=cache)
    second.complete(messages, model='test-model')
    assert second.attempted == 0 and second.cache_hits == 1
    second.complete([{'role': 'user', 'content': 'profile v2; job A; question X'}], model='test-model')
    second.complete(messages, model='other-model')
    assert len(requests) == 3


def test_shared_budget_limits_all_requests_and_cache_still_works(monkeypatch):
    client = backend(monkeypatch, max_calls=1)
    requests = []
    def request(*args, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(text='{}', sources=[])
    monkeypatch.setattr(client, '_request', request)
    client.complete([], model='test-model')
    with pytest.raises(RuntimeError, match='cost controls'):
        client.complete([], model='test-model', web_search=True)
    client.complete([], model='test-model')
    assert client.attempted == 1 and len(requests) == 1 and client.cache_hits == 1


def test_first_failure_stops_further_paid_attempts(monkeypatch):
    client = backend(monkeypatch)
    def fail(*args, **kwargs): raise RuntimeError('HTTP 429')
    monkeypatch.setattr(client, '_request', fail)
    with pytest.raises(RuntimeError): client.complete([], model='test-model')
    with pytest.raises(RuntimeError, match='cost controls'):
        client.complete([{'role': 'user', 'content': 'another question'}], model='test-model')
    assert client.attempted == 1 and client.failed == 1


def test_unknown_facts_and_simple_skill_questions_do_not_call_api():
    class Backend:
        def complete(self, *args, **kwargs): pytest.fail('This question needs no AI')
    decider = AnsweringDecider({'skills': {'languages': ['Python']}}, '', backend=Backend())
    assert decider.generate(label='What is your preferred interview time?', kind='text', options=[], job_context='') is None
    answer = decider.generate(label='Do you have Python experience?', kind='select', options=['Yes', 'No'], job_context='')
    assert answer.value == 'Yes'
    assert decider.generate(label='Do you have Python and Rust experience?', kind='select', options=['Yes', 'No'], job_context='') is None


def test_cache_expiration(tmp_path):
    cache = ResponseCache(tmp_path / 'cache.sqlite', ttl=-1)
    cache.put('expired', {'text': 'old', 'sources': []})
    assert cache.get('expired') is None


def test_known_nonexact_fields_do_not_consult_ai(tmp_path):
    from playwright.sync_api import sync_playwright
    from pocket_agent.forms.engine import fill_form
    from pocket_agent.forms.memory import Memory
    memory = Memory(tmp_path / 'memory.sqlite')
    memory.put('email', 'fictional@example.test')
    class Decider:
        available = True
        def choose(self, **kwargs): pytest.fail('Known rule must not consult AI')
        def generate(self, **kwargs): pytest.fail('Known rule must not consult AI')
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(content_type='text/html',
                    body='<label>Your email address<input type=email required></label><button type=submit>Submit</button>'))
                result = fill_form(page, 'https://example.test', memory, None, lambda _: pytest.fail('Known email'), Decider())
                assert page.locator('input').input_value() == 'fictional@example.test'
                assert result.model_calls == 0 and result.submits == 0
            finally:
                browser.close()
    finally:
        memory.close()
