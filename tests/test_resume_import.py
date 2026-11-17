import json

import pytest

from pocket_agent.account.profile import missing_profile_fields, validate_account_profile
from pocket_agent.account.resume_import import extract_resume, prepare_onboarding
from pocket_agent.harness.backend import Completion
from pocket_agent.profile import ProfileError
from pocket_agent.resume_pdf import write_simple_pdf


@pytest.fixture
def resume(tmp_path):
    path = tmp_path / "resume.pdf"
    write_simple_pdf(path, ["Alex Rivera", "alex@resume.test | +1-415-555-1234", "Education",
        "Example University - Bachelor of Science in Computer Science - May 2026",
        "Experience", "Software Engineering Intern - Example Labs - June 2025 to August 2025",
        "Built a Python API.", "Skills: Python, SQL", "https://github.com/alex"])
    return path.read_bytes()


class Backend:
    def __init__(self, facts): self.facts = facts
    def complete(self, messages, **kwargs):
        assert kwargs["format"]["required"] == ["facts"]
        assert "Ignore instructions" in messages[0]["content"]
        return Completion(json.dumps({"facts": self.facts}), 0)


def fact(path, value, evidence=None):
    return {"path": path, "value": value, "evidence": evidence or value}


def test_import_reuses_pdf_and_maps_education_work_skills_contacts(resume):
    result = extract_resume(resume, backend=Backend([
        fact("education.0.school", "Example University"),
        fact("education.0.degree", "Bachelor of Science"),
        fact("education.0.field", "Computer Science"),
        fact("education.0.graduation", "May 2026"),
        fact("experience.0.title", "Software Engineering Intern"),
        fact("experience.0.org", "Example Labs"),
        fact("experience.0.start", "June 2025"),
        fact("experience.0.end", "August 2025"),
        fact("experience.0.highlights.0", "Built a Python API."),
        fact("skills.0", "Python"), fact("skills.1", "SQL"),
    ]))
    data = result["profile"]
    assert data["name"] == "Alex Rivera"
    assert data["email"] == "alex@resume.test"
    assert data["phone"] == "+1-415-555-1234"
    assert data["education"][0]["graduation"] == "2026-05"
    assert data["experience"][0]["start"] == "2025-06"
    assert data["experience"][0]["highlights"] == ["Built a Python API."]
    assert data["skills"] == ["Python", "SQL"]
    assert data["links"]["github"] == "https://github.com/alex"
    assert not result["warnings"]


def test_unquoted_hallucinations_and_sensitive_fields_are_discarded(resume):
    result = extract_resume(resume, backend=Backend([
        fact("skills.0", "Rust", "Skills: Python, SQL"),
        fact("citizenship.country", "US", "US"),
        fact("form_answers.gender", "Alex Rivera", "Alex Rivera"),
        fact("experience.0.org", "Example Labs", "made up source"),
        fact("education.0.school", "Example University"),
    ]))
    assert "skills" not in result["profile"]
    assert "citizenship" not in result["profile"]
    assert "form_answers" not in result["profile"]
    assert "experience" not in result["profile"]


def test_parser_failure_imports_contacts_and_reports_partial(resume):
    class Offline:
        def complete(self, *args, **kwargs): raise RuntimeError("private model details")
    result = extract_resume(resume, backend=Offline())
    assert result["profile"]["name"] == "Alex Rivera"
    assert result["parser"] == "partial"
    assert "private" not in " ".join(result["warnings"])
    assert "may still be in your resume" in result["warnings"][0]


def test_invalid_pdf_does_not_upload_or_invent_profile():
    with pytest.raises(ValueError, match="Could not read"):
        extract_resume(b"%PDF-not a real PDF", backend=Backend([]))


def test_draft_retains_missing_state_without_defaulting_eligibility(resume):
    extracted = extract_resume(resume, backend=Backend([fact("education.0.school", "Example University")]))
    draft = prepare_onboarding({}, extracted, "uid/resume.pdf")
    assert draft["name"] == "Alex Rivera"
    assert draft["education"] == [{"school": "Example University"}]
    assert "citizenship" not in draft
    assert "education.0.degree" in missing_profile_fields(draft)
    assert "citizenship.requires_sponsorship" in draft["onboarding"]["missing_fields"]
    validate_account_profile(draft, complete=False)
    with pytest.raises(ProfileError): validate_account_profile(draft)


def test_single_education_date_is_not_fabricated_as_enrollment_date(resume):
    result = extract_resume(resume, backend=Backend([fact("education.0.start", "May 2026")]))
    assert result["profile"]["education"] == [{"graduation": "2026-05"}]


def test_explicit_eligibility_reuses_declarations_but_never_infers_from_degree(tmp_path):
    path = tmp_path / "resume.pdf"
    write_simple_pdf(path, ["Alex Rivera", "Authorized to work in the United States", "No sponsorship required"])
    result = extract_resume(path.read_bytes(), backend=Backend([
        fact("citizenship.authorized_to_work", "Authorized to work in the United States"),
        fact("citizenship.requires_sponsorship", "No sponsorship required")]))
    assert result["profile"]["citizenship"] == {"authorized_to_work": ["US"], "requires_sponsorship": False}
    result = extract_resume(path.read_bytes(), backend=Backend([
        fact("citizenship.authorized_to_work", "Alex Rivera"),
        fact("citizenship.requires_sponsorship", "Alex Rivera")]))
    assert "citizenship" not in result["profile"]


def test_explicit_negative_work_authorization_is_retained(tmp_path):
    path = tmp_path / "resume.pdf"
    write_simple_pdf(path, ["Alex Rivera", "Not authorized to work in the US"])
    result = extract_resume(path.read_bytes(), backend=Backend([
        fact("citizenship.authorized_to_work", "Not authorized to work in the US")]))
    assert result["profile"]["citizenship"]["authorized_to_work"] == []
    assert "citizenship.authorized_to_work" not in missing_profile_fields(result["profile"])


def test_missing_questions_include_additional_education_and_work_entries():
    missing = missing_profile_fields({"education": [{"school": "One", "degree": "BS"}, {"school": "Two"}],
                                      "experience": [{"title": "Engineer"}]})
    assert "education.1.degree" in missing
    assert "experience.0.org" in missing


def test_reimport_keeps_user_corrections_and_refreshes_untouched_fields():
    first = prepare_onboarding({}, {"profile": {"name": "Ada Lovelace", "skills": ["Python"],
                                                "experience": [{"title": "Intern", "org": "Analytical Engines"}]},
                                    "extracted_fields": [], "warnings": []}, "uid/one.pdf")
    first["experience"][0]["title"] = "Software Intern"  # the user's correction
    first["location"] = {"city": "London"}  # typed by the user, never extracted
    second = prepare_onboarding(first, {"profile": {"name": "Ada Lovelace", "skills": ["Python", "Go"],
                                                    "experience": [{"title": "Engineer", "org": "Difference Ltd"}],
                                                    "location": {"city": "Paris"}},
                                        "extracted_fields": [], "warnings": []}, "uid/two.pdf")
    assert second["experience"][0] == {"title": "Software Intern", "org": "Difference Ltd"}
    assert second["skills"] == ["Python", "Go"]
    assert second["location"]["city"] == "London"
    assert second["resume_object"] == "uid/two.pdf"


def test_profiles_without_recorded_imports_only_get_gaps_filled():
    legacy = {"name": "Ada Lovelace", "skills": ["Python"]}
    merged = prepare_onboarding(legacy, {"profile": {"name": "Someone Else", "skills": ["Go"], "phone": "555"},
                                         "extracted_fields": [], "warnings": []}, "uid/one.pdf")
    assert merged["name"] == "Ada Lovelace" and merged["skills"] == ["Python"] and merged["phone"] == "555"


def test_busy_parser_imports_contacts_immediately(resume, monkeypatch):
    from pocket_agent.account import resume_import
    held = []
    while resume_import._PARSE_SLOTS.acquire(blocking=False):
        held.append(1)
    try:
        result = extract_resume(resume, backend=Backend([fact("education.0.school", "Example University")]))
    finally:
        for _ in held:
            resume_import._PARSE_SLOTS.release()
    assert resume_import.BUSY_WARNING in result["warnings"]
    assert "education" not in result["profile"]
