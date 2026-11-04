"""Draft an application answer from the profile, or ask the user.

This is the reference implementation for the prefill skill. The agent is
instructed to call profile_get and ask_user itself. Nothing here invents
salary, demographics, veteran status, disability, references, notice, or travel.
"""

from __future__ import annotations

import re
from typing import Any


def draft_answer(profile: dict[str, Any], question: str) -> dict[str, Any]:
    text = question.strip()
    lowered = text.lower()

    def ask(field: str) -> dict[str, Any]:
        return {
            "status": "ASK_USER",
            "question": text,
            "missing_fields": [field],
            "answer": None,
        }

    def answer(body: str, fields: list[str]) -> dict[str, Any]:
        return {"status": "answer", "answer": body, "used_fields": fields, "missing_fields": []}

    if re.search(r"(salary|compensation|pay expectation|expected pay|desired pay)", lowered):
        return ask("salary_expectation")
    if "veteran" in lowered:
        return ask("veteran_status")
    if re.search(r"disabilit", lowered):
        return ask("disability_status")
    if re.search(r"\b(gender|race|ethnicity|pronouns)\b", lowered):
        return ask("demographic")
    if "notice" in lowered:
        return ask("notice_period")
    if "reference" in lowered:
        return ask("references")
    if "travel" in lowered:
        return ask("willing_to_travel")

    education = (profile.get("education") or [{}])[0]
    citizenship = profile.get("citizenship") or {}
    location = profile.get("location") or {}
    skills = profile.get("skills") or {}
    links = profile.get("links") or {}

    if re.search(r"(graduation|graduate|degree|major|university|school|gpa)", lowered):
        degree = education.get("degree")
        field = education.get("field")
        school = education.get("school")
        grad = education.get("graduation")
        gpa = education.get("gpa")
        sentence = f"{degree} in {field} from {school}, graduating {grad}."
        if gpa:
            sentence += f" GPA {gpa}."
        return answer(sentence, ["education"])

    if re.search(r"sponsor", lowered):
        needs = bool(citizenship.get("requires_sponsorship"))
        body = "Yes, I will need sponsorship." if needs else "No, I do not require sponsorship."
        return answer(body, ["citizenship.requires_sponsorship"])

    if re.search(r"(authorized|authorised|work authorization|eligible to work|citizenship|citizen)", lowered):
        country = citizenship.get("country") or "unspecified"
        places = ", ".join(citizenship.get("authorized_to_work") or []) or country
        return answer(
            f"Citizenship country: {country}. Authorized to work in: {places}.",
            ["citizenship"],
        )

    if "clearance" in lowered:
        clearance = citizenship.get("clearance") or "none"
        if str(clearance).lower() in {"none", "no", ""}:
            return answer("The profile lists no security clearance.", ["citizenship.clearance"])
        return answer(f"The profile lists clearance: {clearance}.", ["citizenship.clearance"])

    if re.search(r"(github|linkedin|portfolio|website)", lowered):
        parts = [f"{key}: {value}" for key, value in links.items() if value]
        if not parts:
            return ask("links")
        return answer("; ".join(parts), ["links"])

    if re.search(r"\b(skill|languages|tech stack|framework)\b", lowered):
        langs = ", ".join(skills.get("languages") or [])
        frames = ", ".join(skills.get("frameworks") or [])
        return answer(f"Languages: {langs}. Frameworks: {frames}.", ["skills"])

    if re.search(r"(relocat|remote|where do you live|location|based)", lowered):
        city = location.get("city")
        region = location.get("region")
        relocate = "open to relocation" if location.get("willing_to_relocate") else "not looking to relocate"
        remote = "remote is fine" if location.get("remote_ok", True) else "not seeking remote"
        return answer(f"Based in {city}, {region}. {relocate}; {remote}.", ["location"])

    if re.search(r"(start date|when can you start|target start)", lowered):
        target = (profile.get("preferences") or {}).get("target_start")
        if not target:
            return ask("target_start")
        return answer(f"Target start: {target}.", ["preferences.target_start"])

    if re.search(r"(phone|email|name)", lowered):
        return answer(
            f"{profile.get('name')} — {profile.get('email')} — {profile.get('phone')}",
            ["name", "email", "phone"],
        )

    if re.search(r"(experience|intern)", lowered):
        lines = []
        for item in profile.get("experience") or []:
            lines.append(f"{item.get('title')} at {item.get('org')} ({item.get('start')} to {item.get('end')})")
        if not lines:
            return ask("experience")
        return answer(" ".join(lines), ["experience"])

    return ask("unspecified")
