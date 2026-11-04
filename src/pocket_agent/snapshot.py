"""Frozen posting snapshots so evals can be repeated without the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pocket_agent.feeds.model import Job
from pocket_agent.tracker import Tracker


def save_snapshot(tracker: Tracker, path: Path) -> int:
    rows = tracker.conn.execute("SELECT * FROM jobs ORDER BY source, id").fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            job = _row_to_job(row)
            handle.write(json.dumps(job.to_public(description_limit=2000), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_snapshot(tracker: Tracker, path: Path) -> dict[str, int]:
    jobs: list[Job] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            jobs.append(job_from_public(json.loads(line)))
    counts = tracker.upsert_jobs(jobs, include_inactive=True)
    counts["loaded"] = len(jobs)
    return counts


def job_from_public(data: dict[str, Any]) -> Job:
    source = data["source"]
    prefix = source + ":"
    external_id = data.get("external_id")
    if not external_id:
        external_id = data["id"][len(prefix):] if str(data["id"]).startswith(prefix) else data["id"]
    return Job(
        id=data["id"],
        source=source,
        external_id=external_id,
        company=data.get("company") or "",
        title=data.get("title") or "",
        location=data.get("location") or "",
        url=data.get("url") or "",
        description=data.get("description") or "",
        posted_at=data.get("posted_at"),
        active=bool(data.get("active", True)),
        category=data.get("category"),
        sponsorship=data.get("sponsorship") or "other",
        degrees=list(data.get("degrees") or []),
        citizenship_required=bool(data.get("citizenship_required")),
        clearance_required=bool(data.get("clearance_required")),
        terms=list(data.get("terms") or []),
        raw=data.get("raw") or {},
    )


def _row_to_job(row: Any) -> Job:
    import json as _json

    return Job(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        company=row["company"],
        title=row["title"],
        location=row["location"],
        url=row["url"],
        description=row["description"],
        posted_at=row["posted_at"],
        active=bool(row["active"]),
        category=row["category"],
        sponsorship=row["sponsorship"],
        degrees=_json.loads(row["degrees_json"] or "[]"),
        citizenship_required=bool(row["citizenship_required"]),
        clearance_required=bool(row["clearance_required"]),
        terms=_json.loads(row["terms_json"] or "[]"),
        raw=_json.loads(row["raw_json"] or "{}"),
    )
