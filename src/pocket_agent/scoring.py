"""Eligibility checks and a 0-100 fit score. Deterministic, no model calls."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_DEGREE_RANK = [
    ("phd", 4),
    ("ph.d", 4),
    ("doctorate", 4),
    ("doctor", 4),
    ("master", 3),
    ("mba", 3),
    ("ms", 3),
    ("ma", 3),
    ("bachelor", 2),
    ("bs", 2),
    ("ba", 2),
    ("b.s", 2),
    ("associate", 1),
    ("certificate", 0),
]
_STOP = {
    "with", "from", "that", "this", "your", "have", "will", "about", "their", "using",
    "build", "built", "internal", "experience", "engineer", "engineering", "software",
    "role", "team", "required", "including", "other", "into", "over", "under", "where",
}


def score_fit(profile: dict[str, Any], job: dict[str, Any], *, today: date) -> dict[str, Any]:
    blockers: list[str] = []
    checks: dict[str, dict[str, Any]] = {}

    degree_points, degree_reason, degree_ok, degree_detail = _degree(profile, job)
    checks["degree"] = {"ok": degree_ok, "detail": degree_detail}
    if not degree_ok:
        blockers.append(degree_detail)

    grad_ok, grad_detail = _grad_window(profile, job, today)
    checks["grad_date"] = {"ok": grad_ok, "detail": grad_detail}
    if not grad_ok:
        blockers.append(grad_detail)

    citizen_ok, citizen_detail = _citizenship(profile, job)
    checks["citizenship"] = {"ok": citizen_ok, "detail": citizen_detail}
    if not citizen_ok:
        blockers.append(citizen_detail)

    clearance_ok, clearance_detail = _clearance(profile, job)
    checks["clearance"] = {"ok": clearance_ok, "detail": clearance_detail}
    if not clearance_ok:
        blockers.append(clearance_detail)

    sponsor_ok, sponsor_detail = _sponsorship(profile, job)
    checks["sponsorship"] = {"ok": sponsor_ok, "detail": sponsor_detail}
    if not sponsor_ok:
        blockers.append(sponsor_detail)

    loc_points, loc_reason, loc_ok, loc_detail = _location(profile, job)
    checks["location"] = {"ok": loc_ok, "detail": loc_detail}
    if not loc_ok:
        blockers.append(loc_detail)

    skill_points, skill_reason = _skills(profile, job)
    role_points, role_reason = _role(profile, job)
    exp_points, exp_reason = _experience(profile, job)

    raw = skill_points + role_points + loc_points + degree_points + exp_points
    eligible = not blockers
    score = raw if eligible else min(raw, 35)
    score = max(0, min(100, int(round(score))))
    reasons = [skill_reason, role_reason, loc_reason, degree_reason, exp_reason, grad_detail]
    return {
        "ok": True,
        "job_id": job.get("id"),
        "company": job.get("company"),
        "title": job.get("title"),
        "eligible": eligible,
        "score": score,
        "blockers": blockers,
        "reasons": [item for item in reasons if item],
        "checks": checks,
    }


def _degree_rank(label: str) -> int | None:
    key = (label or "").strip().lower()
    if not key:
        return None
    for name, rank in _DEGREE_RANK:
        if name in key:
            return rank
    return None


def _profile_degree(profile: dict[str, Any]) -> int | None:
    ranks = [_degree_rank(str(item.get("degree") or "")) for item in profile.get("education") or []]
    ranks = [rank for rank in ranks if rank is not None]
    return max(ranks) if ranks else None


def _required_degree(job: dict[str, Any]) -> int | None:
    ranks = [_degree_rank(str(item)) for item in job.get("degrees") or []]
    ranks = [rank for rank in ranks if rank is not None]
    if ranks:
        return min(ranks)
    text = f"{job.get('title') or ''} {job.get('description') or ''}".lower()
    if re.search(r"ph\.?d\b.{0,30}required|required.{0,30}ph\.?d", text):
        return 4
    if re.search(r"master'?s?.{0,30}required|required.{0,20}master", text):
        return 3
    return None


def _degree(profile: dict[str, Any], job: dict[str, Any]) -> tuple[int, str, bool, str]:
    have = _profile_degree(profile)
    need = _required_degree(job)
    if have is None:
        return 0, "Profile has no parseable degree (+0)", False, "Profile has no parseable degree"
    if need is None:
        return 6, "Posting does not list a degree requirement (+6)", True, "No degree requirement listed"
    if have < need:
        return 0, "Degree is below the posting requirement (+0)", False, "Degree is below the posting requirement"
    return 10, "Degree meets the listed requirement (+10)", True, "Degree meets the listed requirement"


def _parse_month(value: str) -> date | None:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return date(parsed.year, parsed.month, 1)
        except ValueError:
            continue
    match = re.search(r"(20\d{2})", text)
    if match:
        return date(int(match.group(1)), 6, 1)
    return None


def _graduation(profile: dict[str, Any]) -> date | None:
    dates = [_parse_month(str(item.get("graduation") or "")) for item in profile.get("education") or []]
    dates = [item for item in dates if item is not None]
    return max(dates) if dates else None


def _internship(job: dict[str, Any]) -> bool:
    title = (job.get("title") or "").lower()
    if "intern" in title or "co-op" in title or "coop" in title:
        return True
    return job.get("source") == "simplify-intern"


def _new_grad(job: dict[str, Any]) -> bool:
    if _internship(job):
        return False
    title = (job.get("title") or "").lower()
    if "new grad" in title or "early career" in title:
        return True
    return job.get("source") == "simplify-newgrad"


def _internship_years(job: dict[str, Any]) -> list[int]:
    blob = " ".join([*(job.get("terms") or []), job.get("title") or ""])
    return [int(item) for item in re.findall(r"(20\d{2})", blob)]


def _grad_window(profile: dict[str, Any], job: dict[str, Any], today: date) -> tuple[bool, str]:
    grad = _graduation(profile)
    if grad is None:
        return True, "Profile has no graduation date to check"
    if _internship(job):
        years = _internship_years(job)
        if years and grad < date(min(years), 1, 1):
            return False, f"Graduation {grad.isoformat()} is before the {min(years)} internship"
        if not years and grad < today.replace(day=1):
            return False, "Internship expects a current student and the profile graduation is already past"
        return True, "Graduation timing fits the internship"
    if _new_grad(job):
        days = (today - grad).days
        if days > 548:
            return False, "Graduation is more than 18 months ago for a new-grad role"
        return True, "Graduation is inside the new-grad window"
    return True, "No graduation-window rule for this role"


def _citizenship(profile: dict[str, Any], job: dict[str, Any]) -> tuple[bool, str]:
    if not job.get("citizenship_required"):
        return True, "No U.S. citizenship requirement"
    country = str((profile.get("citizenship") or {}).get("country") or "").strip().lower()
    if country in {"us", "usa", "united states", "united states of america"}:
        return True, "Profile lists U.S. citizenship"
    return False, "Role requires U.S. citizenship and the profile does not"


def _clearance(profile: dict[str, Any], job: dict[str, Any]) -> tuple[bool, str]:
    if not job.get("clearance_required"):
        return True, "No clearance requirement"
    clearance = str((profile.get("citizenship") or {}).get("clearance") or "none").strip().lower()
    if clearance in {"", "none", "no", "n/a"}:
        return False, "Role requires a security clearance the profile does not list"
    return True, f"Profile lists clearance {clearance}"


def _sponsorship(profile: dict[str, Any], job: dict[str, Any]) -> tuple[bool, str]:
    needs = bool((profile.get("citizenship") or {}).get("requires_sponsorship"))
    if job.get("sponsorship") == "none" and needs:
        return False, "Role does not offer sponsorship and the profile requires it"
    if needs and job.get("sponsorship") == "none":
        return False, "Role does not offer sponsorship"
    return True, "Sponsorship check passed"


def _location(profile: dict[str, Any], job: dict[str, Any]) -> tuple[int, str, bool, str]:
    loc = (job.get("location") or "").lower()
    location = profile.get("location") or {}
    prefs = [str(item).lower() for item in (profile.get("preferences") or {}).get("locations") or []]
    city = str(location.get("city") or "").lower()
    remote = "remote" in loc
    if remote and location.get("remote_ok", True):
        return 15, "Remote role and the profile allows remote (+15)", True, "Location matches preferences"
    if city and city in loc:
        return 15, f"Location matches {location.get('city')} (+15)", True, "Location matches preferences"
    if any(pref and (pref in loc or loc in pref) for pref in prefs):
        return 15, "Location matches a preferred place (+15)", True, "Location matches preferences"
    if location.get("willing_to_relocate"):
        return 8, "Not a preferred city, but the profile allows relocation (+8)", True, "Relocation is allowed"
    if remote and not location.get("remote_ok", True):
        return 0, "Remote role but the profile declines remote work (+0)", False, "Profile declines remote work"
    return 0, "Location is outside preferences and the profile declines relocation (+0)", False, "Location is outside preferences and the profile declines relocation"


def _skill_list(profile: dict[str, Any]) -> list[str]:
    skills = profile.get("skills") or {}
    values = list(skills.get("languages") or []) + list(skills.get("frameworks") or [])
    return [str(item) for item in values if item]


def _skills(profile: dict[str, Any], job: dict[str, Any]) -> tuple[int, str]:
    skills = _skill_list(profile)
    if not skills:
        return 0, "No skills listed on the profile (+0)"
    text = f"{job.get('title') or ''} {job.get('description') or ''} {job.get('category') or ''}".lower()
    hits = [skill for skill in skills if skill.lower() in text]
    points = {0: 0, 1: 15, 2: 28}.get(len(hits), 40)
    if hits:
        return points, "Skills mentioned: " + ", ".join(hits) + f" (+{points})"
    return 0, "None of the profile skills appear in the posting (+0)"


def _role(profile: dict[str, Any], job: dict[str, Any]) -> tuple[int, str]:
    title = (job.get("title") or "").lower()
    prefs = [str(item).lower() for item in (profile.get("preferences") or {}).get("roles") or []]
    hits = [pref for pref in prefs if pref and pref in title]
    if hits:
        return 25, f"Title matches preferred role ({hits[0]}) (+25)"
    wants_swe = any(any(token in pref for token in ("software", "engineer", "developer", "swe")) for pref in prefs)
    if wants_swe and any(token in title for token in ("software", "engineer", "developer", "backend", "frontend", "full stack", "fullstack")):
        return 22, "Title is in the software engineering family (+22)"
    return 6, "Title is a weak match for preferred roles (+6)"


def _experience(profile: dict[str, Any], job: dict[str, Any]) -> tuple[int, str]:
    chunks: list[str] = []
    for item in profile.get("experience") or []:
        chunks.append(str(item.get("title") or ""))
        chunks.extend(str(line) for line in item.get("highlights") or [])
    words = {word for word in re.findall(r"[a-z][a-z0-9+]{3,}", " ".join(chunks).lower()) if word not in _STOP}
    job_words = set(re.findall(r"[a-z][a-z0-9+]{3,}", f"{job.get('title') or ''} {job.get('description') or ''}".lower()))
    overlap = sorted(words & job_words)
    points = min(10, len(overlap) * 2)
    if overlap:
        return points, "Experience overlap: " + ", ".join(overlap[:6]) + f" (+{points})"
    return 0, "No experience-term overlap (+0)"
