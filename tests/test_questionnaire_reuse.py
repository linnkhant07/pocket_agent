from playwright.sync_api import sync_playwright

from pocket_agent.account.runner import snapshot_profile
from pocket_agent.forms.engine import UserAnswer, fill_form
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.seed import load_example_profile, seed_memory

# Saved exactly as the app's upfront questionnaire saves them: always_use, with the questionnaire's own wording.
QUESTIONNAIRE = [
    {'key': 'gender', 'value': 'Male', 'always_use': True, 'label': 'Gender (optional)'},
    {'key': 'race', 'value': 'Asian (Not Hispanic or Latino)', 'always_use': True, 'label': 'Race or ethnicity (optional)'},
    {'key': 'hispanic', 'value': 'No', 'always_use': True, 'label': 'Hispanic or Latino (optional)'},
]

HTML = '''<h2>Voluntary Self-Identification</h2>
<label>Gender<select><option></option><option>Male</option><option>Female</option><option>Decline To Self Identify</option></select></label>
<label>Are you Hispanic/Latino?<select><option></option><option>Yes</option><option>No</option><option>Decline To Self Identify</option></select></label>
<label>Please identify your race<select><option></option><option>Asian (Not Hispanic or Latino)</option><option>White (Not Hispanic or Latino)</option><option>Decline To Self Identify</option></select></label>
<p id=progress>Review</p><button type=submit>Submit</button>'''


def test_questionnaire_answers_fill_any_wording_without_asking(tmp_path):
    profile = load_example_profile()
    profile['resume_object'] = '11111111-1111-4111-8111-111111111111/resume.pdf'
    profile, _ = snapshot_profile({'profile': {'data': profile, 'revision': 1}, 'answers': QUESTIONNAIRE})
    memory = Memory(tmp_path / 'memory.sqlite')
    asked = []

    def ask(question):
        asked.append(question.label)
        return UserAnswer('', skip=True)

    try:
        seed_memory(memory, profile)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body=HTML, content_type='text/html'))
                fill_form(page, 'https://example.test', memory, None, ask)
                values = page.locator('select').evaluate_all('els => els.map(el => el.value)')
            finally:
                browser.close()
    finally:
        memory.close()
    assert asked == []
    assert values == ['Male', 'No', 'Asian (Not Hispanic or Latino)']
