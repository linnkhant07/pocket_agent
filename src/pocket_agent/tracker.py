"""Local SQLite tracker. Applications are records the user keeps. Nothing is submitted."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pocket_agent.feeds.model import Job

STATUSES = ("saved", "applied", "interviewing", "offer", "rejected", "withdrawn")
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|pragma|replace|create|vacuum|truncate)\b",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def infer_status(text: str) -> str | None:
    """Read a pasted confirmation email and pick a tracker status."""
    lowered = text.lower()
    if re.search(
        r"(not moving forward|unfortunately|regret to|other candidates|we will not be|we cannot move|position has been filled|not selected|declin)",
        lowered,
    ):
        return "rejected"
    if re.search(r"\boffer\b", lowered) and not re.search(r"(cannot offer|can't offer|not an offer|no offer)", lowered):
        return "offer"
    if "interview" in lowered:
        return "interviewing"
    if re.search(r"(received your application|thank you for applying|we have received|application was submitted|thanks for applying)", lowered):
        return "applied"
    return None


class Tracker:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              external_id TEXT NOT NULL,
              company TEXT NOT NULL,
              title TEXT NOT NULL,
              location TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '',
              posted_at TEXT,
              active INTEGER NOT NULL DEFAULT 1,
              category TEXT,
              sponsorship TEXT NOT NULL DEFAULT 'other',
              degrees_json TEXT NOT NULL DEFAULT '[]',
              citizenship_required INTEGER NOT NULL DEFAULT 0,
              clearance_required INTEGER NOT NULL DEFAULT 0,
              terms_json TEXT NOT NULL DEFAULT '[]',
              dedupe_key TEXT NOT NULL,
              raw_json TEXT NOT NULL DEFAULT '{}',
              fetched_at TEXT NOT NULL,
              UNIQUE(source, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
            CREATE TABLE IF NOT EXISTS applications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              fit_score INTEGER,
              notes TEXT NOT NULL DEFAULT '',
              confirmation_text TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )
        self.conn.commit()

    def upsert_job(self, job: Job) -> str:
        now = utc_now()
        existing = self.conn.execute(
            "SELECT id FROM jobs WHERE source = ? AND external_id = ?",
            (job.source, job.external_id),
        ).fetchone()
        payload = (
            job.company,
            job.title,
            job.location,
            job.url,
            job.description,
            job.posted_at,
            1 if job.active else 0,
            job.category,
            job.sponsorship,
            json.dumps(job.degrees),
            1 if job.citizenship_required else 0,
            1 if job.clearance_required else 0,
            json.dumps(job.terms),
            job.dedupe_key,
            json.dumps(job.raw),
            now,
        )
        if existing:
            self.conn.execute(
                """
                UPDATE jobs SET company=?, title=?, location=?, url=?, description=?, posted_at=?,
                  active=?, category=?, sponsorship=?, degrees_json=?, citizenship_required=?,
                  clearance_required=?, terms_json=?, dedupe_key=?, raw_json=?, fetched_at=?
                WHERE id=?
                """,
                (*payload, existing["id"]),
            )
            self.conn.commit()
            return "updated"
        duplicate = self.conn.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)).fetchone()
        if duplicate:
            return "duplicate"
        self.conn.execute(
            """
            INSERT INTO jobs (
              id, source, external_id, company, title, location, url, description, posted_at,
              active, category, sponsorship, degrees_json, citizenship_required, clearance_required,
              terms_json, dedupe_key, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job.id, job.source, job.external_id, *payload),
        )
        self.conn.commit()
        return "inserted"

    def upsert_jobs(self, jobs: list[Job], *, include_inactive: bool = False) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "duplicates": 0, "inactive_skipped": 0}
        for job in jobs:
            if not job.active and not include_inactive:
                counts["inactive_skipped"] += 1
                continue
            outcome = self.upsert_job(job)
            if outcome == "inserted":
                counts["inserted"] += 1
            elif outcome == "updated":
                counts["updated"] += 1
            else:
                counts["duplicates"] += 1
        return counts

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_dict(row) if row else None

    def search_jobs(
        self,
        *,
        query: str = "",
        location: str = "",
        company: str = "",
        source: str = "",
        limit: int = 20,
        remote_only: bool = False,
        exclude_clearance: bool = False,
        exclude_citizenship_required: bool = False,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        clauses = ["1=1"]
        params: list[object] = []
        if active_only:
            clauses.append("active = 1")
        if company:
            clauses.append("company LIKE ?")
            params.append(f"%{company}%")
        if location:
            clauses.append("location LIKE ?")
            params.append(f"%{location}%")
        if source:
            clauses.append("source = ?")
            params.append(source)
        if remote_only:
            clauses.append("lower(location) LIKE '%remote%'")
        if exclude_clearance:
            clauses.append("clearance_required = 0")
        if exclude_citizenship_required:
            clauses.append("citizenship_required = 0")
        if query:
            like = f"%{query}%"
            clauses.append("(title LIKE ? OR company LIKE ? OR description LIKE ? OR category LIKE ? OR location LIKE ?)")
            params.extend([like, like, like, like, like])
        sql = f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} ORDER BY posted_at DESC, company ASC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_job_dict(row) for row in rows]

    def upsert_application(self, record: dict[str, Any]) -> dict[str, Any]:
        job_id = str(record.get("job_id") or "")
        if not job_id:
            raise ValueError("record.job_id is required")
        if self.get_job(job_id) is None:
            raise ValueError(f"unknown job_id {job_id}")
        status = record.get("status")
        confirmation = str(record.get("confirmation_text") or "")
        if status is None and confirmation:
            status = infer_status(confirmation)
        if status is None:
            status = "saved"
        status = str(status).strip().lower()
        if status not in STATUSES:
            raise ValueError(
                f"status {status!r} is not a tracker label. Use one of {', '.join(STATUSES)}. "
                "Pocket Agent never submits applications."
            )
        notes = str(record.get("notes") or "")
        fit_score = record.get("fit_score")
        if fit_score is not None:
            fit_score = int(fit_score)
        now = utc_now()
        existing = self.conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE applications
                SET status=?, fit_score=COALESCE(?, fit_score), notes=CASE WHEN ? != '' THEN ? ELSE notes END,
                    confirmation_text=CASE WHEN ? != '' THEN ? ELSE confirmation_text END, updated_at=?
                WHERE job_id=?
                """,
                (status, fit_score, notes, notes, confirmation, confirmation, now, job_id),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO applications (job_id, status, fit_score, notes, confirmation_text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, status, fit_score, notes, confirmation, now, now),
            )
        self.conn.commit()
        return self.get_application(job_id) or {}

    def get_application(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT a.*, j.company, j.title, j.location, j.url, j.source
            FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE a.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_applications(
        self,
        *,
        status: str | None = None,
        company: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[object] = []
        if status:
            clauses.append("a.status = ?")
            params.append(status)
        if company:
            clauses.append("j.company LIKE ?")
            params.append(f"%{company}%")
        params.append(max(1, min(int(limit), 200)))
        rows = self.conn.execute(
            f"""
            SELECT a.*, j.company, j.title, j.location, j.url, j.source
            FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        jobs = self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        active = self.conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE active = 1").fetchone()["n"]
        applications = self.conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        by_status = {
            row["status"]: row["n"]
            for row in self.conn.execute("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")
        }
        return {"jobs": jobs, "active_jobs": active, "applications": applications, "by_status": by_status}

    def query_select(self, sql: str) -> list[dict[str, Any]]:
        statement = sql.strip().rstrip(";")
        if not statement.lower().startswith("select"):
            raise ValueError("db_query sql must be a single SELECT")
        if ";" in statement or _SQL_FORBIDDEN.search(statement):
            raise ValueError("db_query sql only allows a single read-only SELECT")
        rows = self.conn.execute(statement).fetchall()
        return [dict(row) for row in rows[:200]]

    def export_csv(self, path: Path) -> int:
        rows = self.list_applications(limit=200)
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "id",
            "job_id",
            "status",
            "fit_score",
            "company",
            "title",
            "location",
            "url",
            "source",
            "notes",
            "updated_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return len(rows)


def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["active"] = bool(data["active"])
    data["citizenship_required"] = bool(data["citizenship_required"])
    data["clearance_required"] = bool(data["clearance_required"])
    data["degrees"] = json.loads(data.pop("degrees_json") or "[]")
    data["terms"] = json.loads(data.pop("terms_json") or "[]")
    data["raw"] = json.loads(data.pop("raw_json") or "{}")
    return data
