import json
from types import SimpleNamespace

import pytest

from pocket_agent.forms.answers import AnsweringDecider

PROFILE={'experience':[{'title':'Software Engineering Intern','highlights':['Built a React dashboard for hazard data.']}], 'skills':{'languages':['Python']},'personal':{'password':'must-never-be-sent'}}

class Backend:
    def __init__(self, output): self.output=output; self.requests=[]
    def complete(self, messages, **kwargs):
        self.requests.append(messages)
        return SimpleNamespace(text=json.dumps(self.output))


def answerer(data):
    backend=Backend(data)
    return AnsweringDecider(PROFILE,'Fictional resume',backend=backend),backend


def test_narrative_generation_has_evidence_and_does_not_include_secrets():
    a,b=answerer({'action':'answer','value':'I built a React dashboard for hazard data. I am interested in applying this experience to your visualization work.', 'kind':'compose','evidence':['profile.experience.0.highlights.0']})
    result=a.generate(label='Why are you interested in this role?',kind='textarea',options=[],job_context='Visualization role')
    assert result and result.kind=='compose'
    assert 'must-never-be-sent' not in str(b.requests)


@pytest.mark.parametrize('question',['What is your salary expectation?','Do you require visa sponsorship?','What is your citizenship?','Do you agree to these terms?'])
def test_sensitive_questions_never_call_generator(question):
    a,b=answerer({'action':'answer','value':'Yes','kind':'extract','evidence':['profile.skills.languages.0']})
    assert a.generate(label=question,kind='text',options=[],job_context='') is None
    assert not b.requests


@pytest.mark.parametrize('output',[
 {'action':'answer','value':'Expert in Rust','kind':'extract','evidence':['profile.skills.languages.0']},
 {'action':'answer','value':'Python','kind':'extract','evidence':['profile.nonexistent']},
 {'action':'answer','value':'I improved performance 90%.','kind':'compose','evidence':['profile.experience.0.highlights.0']},
 {'action':'answer','value':'Yes','kind':'extract','evidence':['profile.skills.languages.0']},
])
def test_unsupported_answers_are_rejected(output):
    a,_=answerer(output)
    assert a.generate(label='Describe your experience with Rust',kind='textarea',options=[],job_context='') is None


def test_exact_skill_yes_requires_named_skill_and_valid_option():
    a,_=answerer({'action':'answer','value':'Yes','kind':'extract','evidence':['profile.skills.languages.0']})
    assert a.generate(label='Do you have Python experience?',kind='select',options=['Yes','No'],job_context='')
    assert a.generate(label='Do you have Rust experience?',kind='select',options=['Yes','No'],job_context='') is None


def test_backend_failure_abstains():
    a,b=answerer({})
    def fail(*_,**__): raise RuntimeError('unavailable')
    b.complete=fail
    assert a.generate(label='Why this role?',kind='textarea',options=[],job_context='') is None
    assert a.last_error


def test_generated_answer_fills_form_then_continues_without_user(tmp_path):
    from playwright.sync_api import sync_playwright
    from pocket_agent.forms.engine import fill_form
    from pocket_agent.forms.memory import Memory
    a,b=answerer({'action':'answer','value':'I built a React dashboard for hazard data. This role fits my visualization experience.', 'kind':'compose','evidence':['profile.experience.0.highlights.0']})
    memory=Memory(tmp_path/'answers.sqlite')
    html='''<p id=progress>Step 1 of 2</p><h2>Questions</h2><label>Why are you interested in this role?<textarea required></textarea></label>
    <button type=button id=next>Next</button><script>document.getElementById('next').onclick=()=>{document.body.innerHTML='<p id=progress>Step 2 of 2 Review</p><button type=submit>Submit</button>'}</script>'''
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
                def ask(_): pytest.fail('Supported written answer should be generated')
                result=fill_form(page,'https://example.test',memory,None,ask,a)
                assert result.status=='review' and result.submits==0
                assert len(result.ai_answers)==1 and result.outcomes[0].via_model
                assert memory.get('essay') is None
            finally: browser.close()
    finally: memory.close()


def test_resume_only_evidence_is_available():
    backend=Backend({'action':'answer','value':'I built a Python dashboard. This role fits that experience.','kind':'compose','evidence':['resume.lines.0']})
    a=AnsweringDecider({},'Built a Python dashboard for weather data.',backend=backend)
    assert a.generate(label='Why this role?',kind='textarea',options=[],job_context='')


def test_model_compose_action_alias_still_requires_valid_evidence():
    a,_=answerer({'action':'compose','kind':'compose','value':'I built a React dashboard.','evidence':['profile.experience.0.highlights.0']})
    assert a.generate(label='Why this role?',kind='textarea',options=[],job_context='')
