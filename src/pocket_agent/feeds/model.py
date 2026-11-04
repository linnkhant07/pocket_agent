"""Normalized job records shared by every feed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

CLEARANCE_RE = re.compile(
    r"\b(ts/sci|top secret|secret clearance|security clearance|public trust)\b",
    re.IGNORECASE,
)
CITIZEN_RE = re.compile(
    r"(u\.?s\.?\s+citizenship|united states citizen|must be a u\.?s\.?\s+citizen|requires u\.?s\.?\s+citizenship)",
    re.IGNORECASE,
)
NO_SPONSOR_RE = re.compile(
    r"(does not offer sponsorship|no sponsorship|will not sponsor|not sponsor)",
    re.IGNORECASE,
)


def make_dedupe_key(company: str, title: str, location: str) -> str:
    def norm(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
        return " ".join(cleaned.split())

    return "|".join([norm(company), norm(title), norm(location)])


def unix_to_iso(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value[:10]
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 10**12:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def normalize_sponsorship(value: str | None) -> tuple[str, bool]:
    text = (value or "").strip().lower()
    if "citizenship" in text:
        return "citizenship", True
    if "does not offer" in text or text in {"no", "none"}:
        return "none", False
    if "offer" in text:
        return "offers", False
    return "other", False


@dataclass
class Job:
    id: str
    source: str
    external_id: str
    company: str
    title: str
    location: str = ""
    url: str = ""
    description: str = ""
    posted_at: str | None = None
    active: bool = True
    category: str | None = None
    sponsorship: str = "other"
    degrees: list[str] = field(default_factory=list)
    citizenship_required: bool = False
    clearance_required: bool = False
    terms: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return make_dedupe_key(self.company, self.title, self.location)

    def apply_text_signals(self) -> Job:
        blob = "\n".join([self.title or "", self.description or "", self.category or "", " ".join(self.terms)])
        if not self.clearance_required and CLEARANCE_RE.search(blob):
            self.clearance_required = True
        if not self.citizenship_required and CITIZEN_RE.search(blob):
            self.citizenship_required = True
        if self.sponsorship == "other" and NO_SPONSOR_RE.search(blob):
            self.sponsorship = "none"
        if self.sponsorship == "citizenship":
            self.citizenship_required = True
        return self

    def to_public(self, description_limit: int = 1200) -> dict:
        description = self.description or ""
        if len(description) > description_limit:
            description = description[:description_limit] + "..."
        return {
            "id": self.id,
            "source": self.source,
            "external_id": self.external_id,
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "url": self.url,
            "description": description,
            "posted_at": self.posted_at,
            "active": self.active,
            "category": self.category,
            "sponsorship": self.sponsorship,
            "degrees": list(self.degrees),
            "citizenship_required": self.citizenship_required,
            "clearance_required": self.clearance_required,
            "terms": list(self.terms),
        }
