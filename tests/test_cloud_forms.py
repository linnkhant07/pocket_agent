import json

import httpx
import pytest

from pocket_agent.forms.cloud import CloudBackend, CloudMatcher, create_decider


@pytest.mark.parametrize('provider', ['gpt', 'claude'])
def test_hosted_requests_and_matching(provider, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    requests = []
    def post(client, url, **kwargs):
        requests.append((url, kwargs))
        answer = json.dumps({'action': 'fill', 'key': 'email'})
        payload = ({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': answer}]}]}
                   if provider == 'gpt' else {'content': [{'type': 'text', 'text': answer}]})
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx.Client, 'post', post)
    matcher = CloudMatcher(CloudBackend(provider), 'test-model')
    assert matcher.choose(label='Email', candidates=[('email', 'fictional@example.test')]).key == 'email'
    assert matcher.choose(label='City', candidates=[('city', 'Austin')]) is None
    assert requests[0][1]['json']['model'] == 'test-model'
    if provider == 'gpt':
        assert requests[0][1]['json']['store'] is False
    else:
        assert 'anthropic-version' in requests[0][1]['headers']


def test_missing_key_is_actionable_and_none_needs_no_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert create_decider('none', {}, home=tmp_path) is None
    with pytest.raises(ValueError, match='OPENAI_API_KEY'):
        create_decider('gpt', {}, home=tmp_path)


def test_provider_failure_does_not_expose_body_or_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-key')
    monkeypatch.setattr(httpx.Client, 'post', lambda *args, **kwargs: httpx.Response(401, text='private body'))
    with pytest.raises(RuntimeError, match='HTTP 401') as exc:
        CloudBackend('gpt').complete([], model='test-model')
    assert 'private' not in str(exc.value)


def test_logging_counts_only_actual_requests_and_omits_payload(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-key')
    logs = []
    backend = CloudBackend('gpt', log=logs.append)
    matcher = CloudMatcher(backend, 'test-model')
    assert matcher.choose(label='Email', candidates=[]) is None
    assert backend.stats()['attempted'] == 0
    monkeypatch.setattr(httpx.Client, 'post', lambda *args, **kwargs: httpx.Response(200, json={
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"action":"ask"}'}]}]}))
    matcher.choose(label='Email', candidates=[('email', 'private@example.test')])
    assert backend.stats() == {'attempted': 1, 'responses_received': 1, 'completed': 1, 'failed': 0}
    assert 'field matching' in logs[0] and 'Response #1 received' in logs[1]
    assert 'private@example.test' not in str(logs) and 'secret-test-key' not in str(logs)
    assert 'Requests attempted: 1' in backend.summary()


@pytest.mark.parametrize('failure', ['http', 'timeout'])
def test_failed_requests_are_visible_without_sensitive_error_details(monkeypatch, failure):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-key')
    logs = []
    backend = CloudBackend('gpt', log=logs.append)
    def post(*args, **kwargs):
        if failure == 'timeout':
            raise httpx.ReadTimeout('secret-test-key private payload')
        return httpx.Response(429, text='private payload')
    monkeypatch.setattr(httpx.Client, 'post', post)
    assert CloudMatcher(backend, 'test-model').choose(label='Email', candidates=[('email', 'private@example.test')]) is None
    assert backend.stats() == {'attempted': 1, 'responses_received': int(failure == 'http'), 'completed': 0, 'failed': 1}
    assert 'failed' in logs[-1]
    assert 'secret-test-key' not in str(logs) and 'private payload' not in str(logs)


@pytest.mark.parametrize('provider', ['gpt', 'claude'])
def test_salary_search_tools_and_citations(provider, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    url = 'https://employer.example/salaries'
    bodies = []
    def post(*args, **kwargs):
        bodies.append(kwargs['json'])
        payload = ({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{}',
            'annotations': [{'type': 'url_citation', 'url': url}]}]}]}
            if provider == 'gpt' else {'content': [{'type': 'text', 'text': '{}', 'citations': [{'url': url}]}]})
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx.Client, 'post', post)
    result = CloudBackend(provider).complete([], model='test-model', web_search=True)
    assert result.sources == [url]
    assert bodies[0]['tools'][0]['type'].startswith('web_search')
