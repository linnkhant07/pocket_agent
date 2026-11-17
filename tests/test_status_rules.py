import json
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from pocket_agent.forms import status
from pocket_agent.forms.engine import UserAnswer, fill_form
from pocket_agent.forms.memory import Memory

PR = {'country': 'MM', 'permanent_residence': ['US'], 'authorized_to_work': ['US'], 'requires_sponsorship': False}
F1 = {'country': 'IN', 'permanent_residence': [], 'authorized_to_work': ['US'], 'requires_sponsorship': True}
STATUS_OPTIONS = ['Select...', 'U.S. Citizen', 'Green Card Holder', 'F-1 OPT', 'H-1B', 'Other']


def field(label, options=(), kind='dropdown', required=True):
    return SimpleNamespace(label=label, kind=kind, required=required, options=[{'label': o} for o in options])


@pytest.fixture
def memory(tmp_path):
    memory = Memory(tmp_path / 'memory.sqlite')
    yield memory
    memory.close()


def answer(memory, label, options=('Yes', 'No'), facts=PR, reader=None):
    status.seed_facts(memory, facts)
    answerer = status.StatusAnswerer(memory, reader=reader)
    try:
        verdict = answerer.answer(field(label, options))
    finally:
        answerer.close()
    return verdict.value if isinstance(verdict, status.Derived) else type(verdict).__name__


# The real Twitch/Amazon wordings, inverted-polarity twins, and a status dropdown, for a US permanent resident.
@pytest.mark.parametrize('label,options,expected', [
    ('If offered employment by Amazon, would you be legally eligible to begin employment immediately?', ('Yes', 'No'), 'Yes'),
    ('Your response is mandatory when applying for a U.S.-based position. Do you need, or will you need in the future, '
     'any immigration related support or sponsorship from Amazon in order to begin or continue employment with Amazon?',
     ('Yes', 'No'), 'No'),
    ('In which country/region do you have citizenship?', (), 'Myanmar'),
    ('Since obtaining your most recent citizenship, did you afterwards become a permanent resident in any other '
     'country/region? This does not include temporary statuses such as student visas or time-limited work permits.',
     ('Yes', 'No'), 'Yes'),
    ('For the sole purpose of determining export licensing requirements, please provide your country of citizenship '
     'or legal permanent residence, whichever was obtained last.', (), 'United States'),
    ('Will you be authorized to work lawfully in the United States after graduation?', ('Yes', 'No'), 'Yes'),
    ('Are you a U.S. citizen or permanent resident?', ('Yes', 'No'), 'Yes'),
    ('Are you a US citizen?', ('Yes', 'No'), 'No'),
    ('Are you able to work in the U.S. without visa sponsorship?', ('Yes', 'No'), 'Yes'),
    ('Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?', ('Yes', 'No'), 'No'),
    ('Do you hold citizenship in any country other than the United States?', ('Yes', 'No'), 'Yes'),
    ('What is your current work authorization status?', STATUS_OPTIONS, 'Green Card Holder'),
    # History never follows from present status: H-1B usually comes before a green card.
    ('Have you held H-1B status, or had an H-1B petition approved on your behalf, within the preceding 6 years for '
     'an employer other than a cap exempt institution?', ('Yes', 'No'), 'Ask'),
])
def test_permanent_resident(memory, label, options, expected):
    assert answer(memory, label, options) == expected


@pytest.mark.parametrize('label,options,expected', [
    ('Will you now or in the future require sponsorship for employment visa status?', ('Yes', 'No'), 'Yes'),
    ('Are you able to work in the U.S. without visa sponsorship?', ('Yes', 'No'), 'No'),
    ('Are you legally authorized to work in the United States?', ('Yes', 'No'), 'Yes'),
    # OPT may cover the start; a student's long-term authorization is theirs to state.
    ('Will you be authorized to work lawfully in the United States after graduation?', ('Yes', 'No'), 'Ask'),
    ('What is your current work authorization status?', STATUS_OPTIONS, 'Ask'),
    ('In which country/region do you have citizenship?', (), 'India'),
])
def test_student_visa_holder(memory, label, options, expected):
    assert answer(memory, label, options, facts=F1) == expected


def test_unstated_facts_are_asked_never_guessed(memory):
    assert answer(memory, 'Are you a US citizen?', facts={}) == 'Ask'
    assert answer(memory, 'Do you require visa sponsorship?', facts={}) == 'Ask'


def test_other_fields_are_not_status(memory):
    assert answer(memory, 'Have you previously applied to Amazon?') == 'NotStatus'
    assert answer(memory, 'LinkedIn profile', options=()) == 'NotStatus'


def test_a_typed_answer_becomes_a_fact_for_every_wording_and_polarity(memory):
    status.seed_facts(memory, {})
    answerer = status.StatusAnswerer(memory)
    try:
        asked = answerer.answer(field('Will you require visa sponsorship?'))
        assert isinstance(asked, status.Ask)
        memory.put(asked.key, asked.fact('No'), always_use=True, sensitive=True, source='answer')
        inverted = answerer.answer(field('Are you able to work without sponsorship?'))
        assert isinstance(inverted, status.Ask)  # authorization itself is still unstated
        again = answerer.answer(field('Do you need, or will you in the future need, sponsorship to work here?'))
        assert isinstance(again, status.Derived) and again.value == 'No'
    finally:
        answerer.close()


def test_exact_saved_answer_wins_over_derivation(memory):
    status.seed_facts(memory, PR)
    memory.put('q.are_you_a_us_citizen', 'Yes', source='answer', label='Are you a US citizen?')
    answerer = status.StatusAnswerer(memory)
    try:
        assert answerer.answer(field('Are you a US citizen?')).value == 'Yes'
    finally:
        answerer.close()


class Reader:
    def __init__(self, readings):
        self.readings, self.requests = readings, []

    def complete(self, messages, **_):
        self.requests.append(messages)
        return SimpleNamespace(text=json.dumps({'readings': self.readings}))


def test_model_reads_meaning_without_facts_and_the_reading_is_cached(memory):
    label = 'Do you possess unrestricted rights to be employed by any company in the US with no visa paperwork?'
    backend = Reader({'q1': {'kind': 'yes_no', 'claim': {'all': [
        {'atom': 'authorized_open', 'country': 'US'}, {'not': {'atom': 'needs_sponsorship', 'country': 'US'}}]}}})
    # The offline grammar cannot read this wording, so the model is asked what it means.
    assert status.read_offline(status.Question(label, ('Yes', 'No'))) is None
    reader = status.ModelReader(backend, 'm')
    assert answer(memory, label, reader=reader) == 'NotStatus'  # no "sponsor"/"authorized" cue: atoms ungrounded


def test_model_reading_is_grounded_cached_and_never_sees_facts(memory):
    label = 'Do you currently possess the legal right to work in the US for any employer, permanently?'
    backend = Reader({'q1': {'kind': 'yes_no', 'claim': {'atom': 'authorized_open', 'country': 'US'}}})
    assert answer(memory, label, reader=status.ModelReader(backend, 'm')) == 'Yes'
    sent = json.dumps(backend.requests)
    assert 'MM' not in sent and 'Myanmar' not in sent and 'permanent_resident_of' not in sent
    # A question read once costs nothing on the next form.
    again = Reader({})
    assert answer(memory, label, reader=status.ModelReader(again, 'm')) == 'Yes'
    assert again.requests == []


def test_model_cannot_unlock_history(memory):
    label = 'Have you ever held F-1 student status?'
    backend = Reader({'q1': {'kind': 'yes_no', 'claim': {'atom': 'citizen', 'country': 'US'}}})
    assert answer(memory, label, reader=status.ModelReader(backend, 'm')) == 'Ask'
    assert backend.requests == []  # recognized offline as history: nothing to read


HTML = '''<h2>Questions</h2>
<label>If offered employment by Amazon, would you be legally eligible to begin employment immediately?<select required><option></option><option>Yes</option><option>No</option></select></label>
<label>Are you able to work in the U.S. without visa sponsorship?<select required><option></option><option>Yes</option><option>No</option></select></label>
<label>What is your current work authorization status?<select required><option></option><option>U.S. Citizen</option><option>Green Card Holder</option><option>F-1 OPT</option></select></label>
<label>Have you held H-1B status within the preceding 6 years?<select required><option></option><option>Yes</option><option>No</option></select></label>
<p id=progress>Review</p><button type=submit>Submit</button>'''


def test_fill_answers_status_questions_from_facts_and_asks_only_history(memory):
    status.seed_facts(memory, PR)
    asked = []

    def ask(question):
        asked.append(question.label)
        return UserAnswer('No')

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(body=HTML, content_type='text/html'))
            result = fill_form(page, 'https://example.test', memory, None, ask)
            values = page.locator('select').evaluate_all('els => els.map(el => el.value)')
        finally:
            browser.close()
    assert values == ['Yes', 'Yes', 'Green Card Holder', 'No']
    assert asked == ['Have you held H-1B status within the preceding 6 years?']
    assert result.submits == 0
    because = [o.because for o in result.outcomes if o.because]
    assert because and all('permanent resident of United States' in text for text in because)
