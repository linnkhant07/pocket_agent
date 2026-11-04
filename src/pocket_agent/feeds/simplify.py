"""SimplifyJobs lists.

Primary sources, in this order:
  https://github.com/SimplifyJobs/New-Grad-Positions
  https://github.com/SimplifyJobs/Summer2027-Internships

Both publish `.github/scripts/listings.json`. That file is preferred.
The README (HTML tables today, markdown tables historically) is only a fallback.
"""

from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser

from pocket_agent.feeds.model import Job, normalize_sponsorship, unix_to_iso

LISTS: dict[str, dict[str, str]] = {
    "new-grad": {
        "source": "simplify-newgrad",
        "repo": "SimplifyJobs/New-Grad-Positions",
        "listings_url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
        "readme_url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    },
    "intern": {
        "source": "simplify-intern",
        "repo": "SimplifyJobs/Summer2027-Internships",
        "listings_url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "readme_url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
    },
}

ALIASES = {
    "simplify": None,
    "new-grad": "new-grad",
    "intern": "intern",
    "simplify-newgrad": "new-grad",
    "simplify-intern": "intern",
}


def list_names_for(source: str | None) -> list[str]:
    if source is None or source == "simplify":
        return ["new-grad", "intern"]
    name = ALIASES.get(source)
    if name is None:
        raise ValueError(f"unknown Simplify list {source!r}")
    return [name]


def parse_listings(payload: object, source: str) -> list[Job]:
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("listings") or payload.get("jobs") or []
    if not isinstance(rows, list):
        raise ValueError("listings.json must be a list")
    jobs: list[Job] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        external_id = str(row.get("id") or "")
        title = str(row.get("title") or "").strip()
        company = str(row.get("company_name") or row.get("company") or "").strip()
        if not external_id or not title or not company:
            continue
        locations = row.get("locations") or []
        if isinstance(locations, str):
            location = locations
        else:
            location = ", ".join(str(item) for item in locations if item)
        sponsorship, citizen = normalize_sponsorship(str(row.get("sponsorship") or ""))
        degrees = [str(item) for item in (row.get("degrees") or []) if item]
        terms = [str(item) for item in (row.get("terms") or []) if item]
        active = bool(row.get("active", True))
        if row.get("is_visible") is False:
            active = False
        description = _listing_description(row, location, degrees, terms, sponsorship)
        job = Job(
            id=f"{source}:{external_id}",
            source=source,
            external_id=external_id,
            company=company,
            title=title,
            location=location,
            url=str(row.get("url") or ""),
            description=description,
            posted_at=unix_to_iso(row.get("date_posted")),
            active=active,
            category=str(row.get("category") or "") or None,
            sponsorship=sponsorship,
            degrees=degrees,
            citizenship_required=citizen,
            clearance_required=False,
            terms=terms,
            raw={"listings": {key: row.get(key) for key in ("sponsorship", "degrees", "terms", "category")}},
        )
        jobs.append(job.apply_text_signals())
    return jobs


def _listing_description(row: dict, location: str, degrees: list[str], terms: list[str], sponsorship: str) -> str:
    parts = [
        f"{row.get('title')} at {row.get('company_name')}.",
        f"Location: {location or 'unspecified'}.",
    ]
    if row.get("category"):
        parts.append(f"Category: {row['category']}.")
    if degrees:
        parts.append("Degrees: " + ", ".join(degrees) + ".")
    if terms:
        parts.append("Terms: " + ", ".join(terms) + ".")
    if sponsorship == "none":
        parts.append("Does not offer sponsorship.")
    elif sponsorship == "citizenship":
        parts.append("U.S. citizenship is required.")
    elif sponsorship == "offers":
        parts.append("Offers sponsorship.")
    return " ".join(parts)


def parse_readme(text: str, source: str) -> list[Job]:
    if "<td" in text.lower() or "<th" in text.lower():
        return _parse_html(text, source)
    return _parse_markdown(text, source)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._row: list[dict] = []
        self._cell: list[str] = []
        self._hrefs: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._in_cell = True
            self._cell = []
            self._hrefs = []
        elif tag == "a" and self._in_cell and attr.get("href"):
            self._hrefs.append(attr["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            text = " ".join("".join(self._cell).split())
            self._row.append({"text": text, "hrefs": list(self._hrefs)})
            self._in_cell = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell.append(data)


def _parse_html(text: str, source: str) -> list[Job]:
    parser = _TableParser()
    parser.feed(text)
    jobs: list[Job] = []
    for row in parser.rows:
        job = _row_to_job(row, source)
        if job is not None:
            jobs.append(job)
    return jobs


def _parse_markdown(text: str, source: str) -> list[Job]:
    jobs: list[Job] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|\s*-+", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        row = [{"text": _md_text(cell), "hrefs": _md_hrefs(cell)} for cell in cells]
        job = _row_to_job(row, source)
        if job is not None:
            jobs.append(job)
    return jobs


def _md_text(cell: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cell).replace("**", "").strip()


def _md_hrefs(cell: str) -> list[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", cell)


def _row_to_job(row: list[dict], source: str) -> Job | None:
    if len(row) < 4:
        return None
    company_raw, flags_company = _strip_marks(row[0]["text"])
    title_raw, flags_title = _strip_marks(row[1]["text"])
    location_raw, flags_location = _strip_marks(row[2]["text"])
    header = company_raw.lower()
    if header in {"company", "role"} or not company_raw or not title_raw:
        return None
    def _usable(hrefs: list[str]) -> list[str]:
        kept = []
        for href in hrefs:
            if not href or "simplify.jobs/c/" in href or href.endswith((".png", ".jpg", ".svg", ".gif")):
                continue
            kept.append(href)
        return kept

    apply_hrefs = _usable(row[3]["hrefs"]) if len(row) > 3 else []
    fallback_hrefs = _usable([href for cell in row for href in cell["hrefs"]])
    url = (apply_hrefs or fallback_hrefs or [""])[0]
    flags = {
        key: flags_company.get(key) or flags_title.get(key) or flags_location.get(key)
        for key in ("citizenship_required", "no_sponsorship", "advanced_degree", "closed")
    }
    sponsorship = "none" if flags["no_sponsorship"] else "other"
    degrees: list[str] = []
    if flags["advanced_degree"]:
        degrees = ["Master's", "PhD"]
    external_id = hashlib.sha256(f"{company_raw}|{title_raw}|{url}".encode()).hexdigest()[:16]
    description = f"{title_raw} at {company_raw}. Location: {location_raw}."
    if flags["citizenship_required"]:
        description += " U.S. citizenship is required."
    if flags["no_sponsorship"]:
        description += " Does not offer sponsorship."
    job = Job(
        id=f"{source}:{external_id}",
        source=source,
        external_id=external_id,
        company=company_raw,
        title=title_raw,
        location=location_raw,
        url=html.unescape(url),
        description=description,
        active=not flags["closed"],
        sponsorship=sponsorship,
        degrees=degrees,
        citizenship_required=bool(flags["citizenship_required"]),
    )
    return job.apply_text_signals()


def _strip_marks(text: str) -> tuple[str, dict[str, bool]]:
    flags = {
        "citizenship_required": "🇺🇸" in text,
        "no_sponsorship": "🛂" in text,
        "advanced_degree": "🎓" in text,
        "closed": "🔒" in text,
    }
    cleaned = re.sub(r"[🔥🎓🛂🇺🇸🔒↳]", "", text)
    return " ".join(cleaned.split()), flags
