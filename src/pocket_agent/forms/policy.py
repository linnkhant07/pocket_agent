"""Sensitive-field policy. These are asked, never guessed."""

from __future__ import annotations

import re

# (pattern, canonical key). First match wins. Patterns are general, not form ids.
SENSITIVE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(gender|sex)\b", re.I), "gender"),
    (re.compile(r"\b(race|ethnicity|hispanic|latino)\b", re.I), "race"),
    (re.compile(r"veteran", re.I), "veteran_status"),
    (re.compile(r"disabilit", re.I), "disability_status"),
    (re.compile(r"\b(salary|compensation|pay expectations?|desired pay|expected pay|wage)\b", re.I), "salary_expectation"),
    (re.compile(r"\b(felony|felonies|criminal|conviction|convicted)\b", re.I), "criminal_history"),
    (re.compile(r"\b(signature|e-signature|sign your name|i certify|certify that|attestation)\b", re.I), "signature"),
    (re.compile(r"\b(ssn|social security)\b", re.I), "ssn"),
    (re.compile(r"\b(date of birth|birth date|birthdate|\bdob\b)\b", re.I), "dob"),
    (re.compile(r"\b(passport|driver'?s license|government id|national id)\b", re.I), "government_id"),
)

SENSITIVE_KEYS = {key for _, key in SENSITIVE_RULES}


def sensitive_key(label: str) -> str | None:
    for pattern, key in SENSITIVE_RULES:
        if pattern.search(label):
            return key
    return None


def is_sensitive_key(key: str | None) -> bool:
    return bool(key) and key in SENSITIVE_KEYS


def question_for(label: str) -> str:
    cleaned = label.strip().rstrip("?").rstrip("*").strip()
    return f"What should I put for \"{cleaned}\"?"
