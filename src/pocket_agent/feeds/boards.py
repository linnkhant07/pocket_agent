"""Optional Greenhouse and Lever adapters. A company slug is required."""

from __future__ import annotations

import html
import re
from datetime import datetime

from pocket_agent.feeds.model import Job, unix_to_iso

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def require_company(company: str | None, source: str) -> str:
    slug = (company or "").strip()
    if not slug:
        raise ValueError(
            f"{source} is an optional extra source. Pass a company board slug "
            "(Greenhouse example: stripe, Lever example: spotify)."
        )
    if not _SLUG.match(slug):
        raise ValueError(f"company slug {slug!r} has unexpected characters")
    return slug


def greenhouse_url(company: str) -> str:
    slug = require_company(company, "greenhouse")
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def greenhouse_job_url(company: str, job_id: str) -> str:
    slug = require_company(company, "greenhouse")
    if not re.fullmatch(r"\d+", str(job_id)):
        raise ValueError("greenhouse job id must be numeric")
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"


def lever_url(company: str) -> str:
    slug = require_company(company, "lever")
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def parse_greenhouse(payload: object, company: str) -> list[Job]:
    slug = require_company(company, "greenhouse")
    if isinstance(payload, dict) and "jobs" in payload:
        rows = payload.get("jobs") or []
    elif isinstance(payload, dict) and payload.get("id"):
        rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("unexpected Greenhouse payload")
    jobs: list[Job] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not row.get("title"):
            continue
        location = row.get("location") or {}
        if isinstance(location, dict):
            location_name = str(location.get("name") or "")
        else:
            location_name = str(location or "")
        external_id = str(row["id"])
        content = html_to_text(str(row.get("content") or ""))
        updated = row.get("first_published") or row.get("updated_at")
        job = Job(
            id=f"greenhouse:{slug}:{external_id}",
            source="greenhouse",
            external_id=external_id,
            company=str(row.get("company_name") or slug),
            title=str(row["title"]),
            location=location_name,
            url=str(row.get("absolute_url") or ""),
            description=content,
            posted_at=_iso_date(updated),
            active=True,
            category=None,
            raw={"board": slug},
        )
        jobs.append(job.apply_text_signals())
    return jobs


def parse_lever(payload: object, company: str) -> list[Job]:
    slug = require_company(company, "lever")
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        rows = payload.get("postings") or payload.get("data") or []
    jobs: list[Job] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        categories = row.get("categories") or {}
        location = ""
        if isinstance(categories, dict):
            locs = categories.get("allLocations") or []
            location = str(categories.get("location") or "")
            if not location and isinstance(locs, list) and locs:
                location = ", ".join(str(item) for item in locs)
        description = str(row.get("descriptionPlain") or html_to_text(str(row.get("description") or "")))
        extra = str(row.get("openingPlain") or "")
        if extra:
            description = (description + "\n" + extra).strip()
        external_id = str(row["id"])
        commitment = categories.get("commitment") if isinstance(categories, dict) else None
        team = categories.get("team") if isinstance(categories, dict) else None
        job = Job(
            id=f"lever:{slug}:{external_id}",
            source="lever",
            external_id=external_id,
            company=str(row.get("company_name") or slug),
            title=str(row.get("text") or row.get("title") or "Untitled"),
            location=location,
            url=str(row.get("hostedUrl") or row.get("applyUrl") or ""),
            description=description[:8000],
            posted_at=unix_to_iso(row.get("createdAt")),
            active=True,
            category=str(team or commitment or "") or None,
            terms=[str(commitment)] if commitment else [],
            raw={"board": slug, "workplaceType": row.get("workplaceType")},
        )
        jobs.append(job.apply_text_signals())
    return jobs


def html_to_text(value: str) -> str:
    if not value:
        return ""
    text = html.unescape(html.unescape(value))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _iso_date(value: object) -> str | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10]
