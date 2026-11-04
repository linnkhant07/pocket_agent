"""Tool implementations. There is no submit/apply tool, and none of these POST an application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema

from pocket_agent.feeds.cache import CachedHttp
from pocket_agent.feeds.service import fetch_feed
from pocket_agent.paths import data_home, today
from pocket_agent.prefill import draft_answer
from pocket_agent.profile import load_profile, public_view
from pocket_agent.resume import parse_resume
from pocket_agent.scoring import score_fit
from pocket_agent.tracker import Tracker

TOOL_NAMES = (
    "fetch_feed",
    "search_jobs",
    "get_job",
    "parse_resume",
    "profile_get",
    "db_query",
    "db_upsert",
    "score_fit",
    "ask_user",
)


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 50}
_BOOL = {"type": "boolean"}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "fetch_feed": _obj(
        {
            "source": {
                "type": "string",
                "enum": ["simplify", "new-grad", "intern", "simplify-newgrad", "simplify-intern", "greenhouse", "lever"],
                "description": "Default simplify loads both SimplifyJobs lists. greenhouse and lever need company.",
            },
            "company": _STR,
            "list_name": _STR,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        }
    ),
    "search_jobs": _obj(
        {
            "query": _STR,
            "location": _STR,
            "company": _STR,
            "source": _STR,
            "limit": _LIMIT,
            "remote_only": _BOOL,
            "exclude_clearance": _BOOL,
            "exclude_citizenship_required": _BOOL,
        }
    ),
    "get_job": _obj({"job_id": _STR}, ["job_id"]),
    "parse_resume": _obj({"path": _STR}),
    "profile_get": _obj({"section": _STR}),
    "db_query": _obj(
        {
            "kind": {"type": "string", "enum": ["applications", "jobs", "stats"]},
            "status": _STR,
            "company": _STR,
            "query": _STR,
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "sql": _STR,
            "export_csv": _BOOL,
        }
    ),
    "db_upsert": _obj(
        {
            "kind": {"type": "string", "enum": ["application"]},
            "record": _obj(
                {
                    "job_id": _STR,
                    "status": _STR,
                    "notes": _STR,
                    "confirmation_text": _STR,
                    "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                ["job_id"],
            ),
        },
        ["kind", "record"],
    ),
    "score_fit": _obj({"job_id": _STR}, ["job_id"]),
    "ask_user": _obj(
        {
            "question": _STR,
            "missing_fields": {"type": "array", "items": _STR},
        },
        ["question"],
    ),
}

TOOL_DESCRIPTIONS = {
    "fetch_feed": "Load jobs into the tracker. source is simplify, new-grad, intern, greenhouse, or lever. greenhouse and lever need company.",
    "search_jobs": "Search jobs already in the tracker. Arguments: query, company, location, source, remote_only, exclude_clearance, exclude_citizenship_required.",
    "get_job": "Read one stored job. Argument: job_id.",
    "parse_resume": "Read the local resume text. Optional path.",
    "profile_get": "Read profile.yaml before drafting an answer. Optional section.",
    "db_query": "List applications, jobs, or stats. Set export_csv true to write CSV. sql must be one SELECT.",
    "db_upsert": "Save a local application label. record.job_id is required. This does not submit an application.",
    "score_fit": "Score one job_id from 0 to 100 and say whether it is eligible. Call this before telling the user to apply.",
    "ask_user": (
        "Ask for a missing fact. missing_fields must use these canonical names: "
        "salary_expectation, veteran_status, disability_status, notice_period, "
        "willing_to_travel, references, demographic."
    ),
}


@dataclass
class ToolContext:
    home: Path | None = None
    today: date | None = None
    network: bool = True
    fixtures: dict[str, Any] = field(default_factory=dict)

    def root(self) -> Path:
        return self.home or data_home()

    def clock(self) -> date:
        return self.today or today()

    def tracker(self) -> Tracker:
        return Tracker(self.root() / "tracker.db")

    def profile_path(self) -> Path:
        return self.root() / "profile.yaml"


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": TOOL_DESCRIPTIONS[name], "input_schema": TOOL_SCHEMAS[name]}
        for name in TOOL_NAMES
    ]


def validate_arguments(name: str, arguments: dict[str, Any]) -> None:
    if name not in TOOL_SCHEMAS:
        raise jsonschema.ValidationError(f"unknown tool {name}")
    jsonschema.validate(arguments, TOOL_SCHEMAS[name])


def execute(name: str, arguments: dict[str, Any] | None, ctx: ToolContext) -> dict[str, Any]:
    arguments = arguments or {}
    if name not in TOOL_SCHEMAS:
        return {"ok": False, "error": f"unknown tool {name}"}
    try:
        validate_arguments(name, arguments)
        return _HANDLERS[name](ctx, arguments)
    except jsonschema.ValidationError as exc:
        return {"ok": False, "error": f"invalid arguments: {exc.message}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _fetch(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tracker = ctx.tracker()
    http = None
    if ctx.network and not _covered_by_fixture(args, ctx.fixtures):
        http = CachedHttp(ctx.root() / "cache")
    try:
        result = fetch_feed(
            tracker,
            source=args.get("source") or "simplify",
            company=args.get("company"),
            list_name=args.get("list_name"),
            limit=args.get("limit"),
            network=ctx.network,
            fixtures=ctx.fixtures,
            http=http,
        )
    finally:
        if http is not None:
            http.close()
        tracker.close()
    return result


def _covered_by_fixture(args: dict[str, Any], fixtures: dict[str, Any]) -> bool:
    source = (args.get("list_name") or args.get("source") or "simplify").lower()
    if source in {"simplify", "new-grad", "intern", "simplify-newgrad", "simplify-intern"}:
        if source in {"simplify", ""}:
            return "new-grad" in fixtures and "intern" in fixtures
        key = "intern" if "intern" in source else "new-grad"
        return key in fixtures
    company = args.get("company")
    return bool(company) and f"{source}:{company}" in fixtures


def _search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tracker = ctx.tracker()
    try:
        jobs = tracker.search_jobs(
            query=args.get("query") or "",
            location=args.get("location") or "",
            company=args.get("company") or "",
            source=args.get("source") or "",
            limit=args.get("limit") or 20,
            remote_only=bool(args.get("remote_only")),
            exclude_clearance=bool(args.get("exclude_clearance")),
            exclude_citizenship_required=bool(args.get("exclude_citizenship_required")),
        )
    finally:
        tracker.close()
    compact = []
    for job in jobs:
        description = job.get("description") or ""
        if len(description) > 400:
            job = {**job, "description": description[:400] + "..."}
        job.pop("raw", None)
        compact.append(job)
    return {"ok": True, "count": len(compact), "jobs": compact}


def _get(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tracker = ctx.tracker()
    try:
        job = tracker.get_job(args["job_id"])
    finally:
        tracker.close()
    if job is None:
        return {"ok": False, "error": f"job {args['job_id']} not found"}
    job.pop("raw", None)
    return {"ok": True, "job": job}


def _resume(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["path"]) if args.get("path") else None
    return parse_resume(path, home=ctx.root())


def _profile(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    profile = load_profile(ctx.profile_path())
    return public_view(profile, args.get("section"))


def _query(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tracker = ctx.tracker()
    try:
        if args.get("sql"):
            rows = tracker.query_select(args["sql"])
            result: dict[str, Any] = {"ok": True, "rows": rows, "count": len(rows)}
        else:
            kind = args.get("kind") or "applications"
            if kind == "stats":
                result = {"ok": True, **tracker.stats()}
            elif kind == "jobs":
                jobs = tracker.search_jobs(query=args.get("query") or "", company=args.get("company") or "", limit=args.get("limit") or 20)
                for job in jobs:
                    job.pop("raw", None)
                result = {"ok": True, "count": len(jobs), "jobs": jobs}
            else:
                apps = tracker.list_applications(status=args.get("status"), company=args.get("company"), limit=args.get("limit") or 50)
                result = {"ok": True, "count": len(apps), "applications": apps}
        if args.get("export_csv"):
            path = ctx.root() / "exports" / "applications.csv"
            written = tracker.export_csv(path)
            result["export_path"] = str(path)
            result["exported"] = written
        return result
    finally:
        tracker.close()


def _upsert(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("kind") != "application":
        return {"ok": False, "error": "only kind=application can be upserted, and it is a local label"}
    tracker = ctx.tracker()
    try:
        saved = tracker.upsert_application(args["record"])
    finally:
        tracker.close()
    return {
        "ok": True,
        "application": saved,
        "submitted": False,
        "note": "Recorded locally. Pocket Agent did not submit an application.",
    }


def _score(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tracker = ctx.tracker()
    try:
        job = tracker.get_job(args["job_id"])
    finally:
        tracker.close()
    if job is None:
        return {"ok": False, "error": f"job {args['job_id']} not found"}
    profile = load_profile(ctx.profile_path())
    return score_fit(profile, job, today=ctx.clock())


def _ask(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    del ctx
    return {
        "ok": True,
        "status": "ASK_USER",
        "question": args["question"],
        "missing_fields": list(args.get("missing_fields") or []),
    }


_HANDLERS = {
    "fetch_feed": _fetch,
    "search_jobs": _search,
    "get_job": _get,
    "parse_resume": _resume,
    "profile_get": _profile,
    "db_query": _query,
    "db_upsert": _upsert,
    "score_fit": _score,
    "ask_user": _ask,
}


def reference_answer(profile: dict[str, Any], question: str) -> dict[str, Any]:
    """Used by unit tests of the prefill skill. Not a model tool."""
    return draft_answer(profile, question)
