"""Validate untrusted account profiles before portal saves and runner use."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from pocket_agent.profile import ProfileError, validate_profile

_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": _TEXT}
_SCHEMA = {
    "type": "object",
    "required": ["name", "education", "skills", "preferences", "citizenship"],
    "properties": {
        "name": {"type": "string", "minLength": 1}, "email": _TEXT, "phone": _TEXT,
        "resume_object": _TEXT,
        "onboarding": {"type": "object", "properties": {
            "missing_fields": _TEXTS, "extracted_fields": _TEXTS, "warnings": _TEXTS}},
        "location": {"type": "object", "properties": {
            **{k: _TEXT for k in ("city", "region", "country", "postal_code", "address_line_1", "address_line_2", "county")},
            "willing_to_relocate": {"type": "boolean"}, "remote_ok": {"type": "boolean"}}},
        "personal": {"type": "object", "additionalProperties": _TEXT},
        "links": {"type": "object", "additionalProperties": _TEXT},
        "constraints": {"type": "object"}, "preferences": {"type": "object", "minProperties": 1},
        "citizenship": {"type": "object", "required": ["authorized_to_work", "requires_sponsorship"], "properties": {
            "country": _TEXT, "clearance": _TEXT, "authorized_to_work": _TEXTS,
            "permanent_residence": _TEXTS, "requires_sponsorship": {"type": "boolean"}}},
        "education": {"type": "array", "minItems": 1, "items": {"type": "object",
            "required": ["school", "degree"], "properties": {
                "school": {"type": "string", "minLength": 1}, "degree": {"type": "string", "minLength": 1},
                **{k: _TEXT for k in ("field", "start", "end", "graduation")},
                "gpa": {"type": ["string", "number"]}}}},
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            **{k: _TEXT for k in ("title", "org", "start", "end")}, "highlights": _TEXTS}}},
        "skills": {"anyOf": [{"type": "array", "minItems": 1, "items": _TEXT},
                               {"type": "object", "minProperties": 1, "additionalProperties": _TEXTS}]},
    },
}
_VALIDATOR = Draft202012Validator(_SCHEMA)


def validate_account_profile(data: dict, *, complete: bool = True) -> dict:
    """Account JSON is untrusted even when it bypasses the hosted API via PostgREST."""
    if not isinstance(data, dict) or "form_answers" in data:
        raise ProfileError("Save questionnaire answers separately from your profile.")
    if len(json.dumps(data).encode()) > 256 * 1024:
        raise ProfileError("Profile must be 256 KiB or smaller.")
    validator = _VALIDATOR
    if not complete:
        schema = deepcopy(_SCHEMA)
        schema.pop("required")
        schema["properties"]["education"]["items"].pop("required")
        schema["properties"]["citizenship"].pop("required")
        validator = Draft202012Validator(schema)
    if next(validator.iter_errors(data), None) is not None or (complete and not str(data.get("name", "")).strip()):
        raise ProfileError("Check the types of your profile, education, skills, location, and work eligibility fields.")
    return validate_profile(deepcopy(data)) if complete else deepcopy(data)


def missing_profile_fields(data: dict) -> list[str]:
    """Required facts/decisions, without converting unknown eligibility into No."""
    missing = []
    for field in ("name", "email", "skills"):
        if not data.get(field):
            missing.append(field)
    for index, education in enumerate(data.get("education") or [{}]):
        for field in ("school", "degree"):
            if not education.get(field):
                missing.append(f"education.{index}.{field}")
    for index, job in enumerate(data.get("experience") or []):
        for field in ("title", "org"):
            if not job.get(field):
                missing.append(f"experience.{index}.{field}")
    citizenship = data.get("citizenship") or {}
    if "authorized_to_work" not in citizenship:
        missing.append("citizenship.authorized_to_work")
    if not isinstance(citizenship.get("requires_sponsorship"), bool):
        missing.append("citizenship.requires_sponsorship")
    return missing
