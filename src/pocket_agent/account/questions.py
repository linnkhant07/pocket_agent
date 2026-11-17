"""Questionnaire keys use the filler's existing sensitive-answer policy."""
from pocket_agent.forms.policy import is_sensitive_key

_QUESTIONS = (
    ("available_start", "When are you available to start?"),
    ("how_did_you_hear", "How did you hear about the role?"),
    ("preferred_name", "Preferred name"),
    ("pronouns", "Pronouns (optional)"),
    ("gender", "Gender (optional)"),
    ("race", "Race or ethnicity (optional)"),
    ("hispanic", "Hispanic or Latino (optional)"),
    ("veteran_status", "Veteran status (optional)"),
    ("disability_status", "Disability status (optional)"),
    ("salary_expectation", "Salary expectations (include currency and period)"),
    ("willing_to_relocate", "Are you willing to relocate?"),
    ("remote_ok", "Are you open to remote work?"),
    ("spoken_language", "Spoken languages and proficiency"),
    ("interview_language", "Preferred programming language for interviews"),
)


def questions() -> list[dict]:
    from pocket_agent.forms.facts import choices_for

    rows = []
    for key, label in _QUESTIONS:
        row = {"key": key, "label": label, "sensitive": is_sensitive_key(key)}
        choices = choices_for(key)
        if choices:
            row["choices"] = choices
        rows.append(row)
    return rows
