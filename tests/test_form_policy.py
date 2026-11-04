"""Policy for realistic application questions. These must not be guessed."""

from pocket_agent.forms.guard import classify_button
from pocket_agent.forms.hard_catalog import HARD_FORMS, hard_forms_for
from pocket_agent.forms.match import _RAW_PHRASES, match_label
from pocket_agent.forms.policy import ask_key


def test_authorization_is_not_sponsorship() -> None:
    auth = "Are you legally authorized to work in the country where the job is located?"
    sponsor = "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B)?"
    assert match_label(auth).key == "work_authorization"
    assert match_label(sponsor).key == "requires_sponsorship"
    assert ask_key(auth) is None
    assert ask_key(sponsor) is None


def test_always_ask_categories() -> None:
    samples = {
        "What are your salary expectations?": "salary_expectation",
        "When can you start?": "available_start",
        "Earliest date you are available to start": "available_start",
        "Languages you speak, and your proficiency": "spoken_language",
        "Preferred programming language for a technical interview": "interview_language",
        "Are you a citizen or permanent resident of any of the following countries: Cuba or Iran?": "export_control",
        "Are you a 'protected individual' as defined by 8 U.S.C. 1324b(a)(3)?": "export_control",
        "Have you been employed by a federal, state, or local government or public institution in the past 5 years?": "gov_employment",
        "Will you have completed your degree by your start date?": "degree_by_start",
        "This role is hybrid, three days on-site. Can you work that schedule?": "onsite",
        "Preferred office": "office_choice",
        "Cover letter": "cover_letter",
        "Why do you want to work here?": "essay",
        "Type your full legal name as your electronic signature": "signature",
        "I acknowledge the arbitration agreement": "arbitration",
        "I certify that the information on this application is true": "certify",
        "Gender": "gender",
        "Are you Hispanic or Latino?": "hispanic",
    }
    for label, key in samples.items():
        assert ask_key(label) == key, label
    assert ask_key("Start date", section="work") is None
    assert match_label("Start date", section="work").key == "work.start"


def test_held_out_wording_is_not_copied_into_the_synonym_table() -> None:
    dev = {field.label.casefold() for form in hard_forms_for("dev") for step in form.steps for field in step.fields}
    held = [field.label for form in hard_forms_for("heldout") for step in form.steps for field in step.fields]
    assert len(HARD_FORMS) >= 20
    assert len(hard_forms_for("heldout")) >= 8
    for label in held:
        assert label.casefold() not in dev
        assert label not in _RAW_PHRASES
        assert label.casefold() not in {item.casefold() for item in _RAW_PHRASES}


def test_review_continue_and_account_buttons_are_not_forward() -> None:
    assert classify_button("Continue", button_type="button", heading="Review") == "submit"
    assert classify_button("Continue", button_type="button", heading="My information") == "forward"
    assert classify_button("Submit application", button_type="button") == "submit"
    assert classify_button("Finish", button_type="button") == "submit"
    assert classify_button("Create account", button_type="submit") == "submit"
    assert classify_button("Sign in", button_type="button") == "account"
    assert classify_button("Next", button_type="submit") == "submit"
