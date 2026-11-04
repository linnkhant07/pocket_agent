import json
from datetime import date
from pathlib import Path

from pocket_agent.paths import resources_dir
from pocket_agent.profile import load_profile
from pocket_agent.resume import parse_resume
from pocket_agent.resume_pdf import write_simple_pdf
from pocket_agent.scoring import score_fit
from pocket_agent.snapshot import job_from_public
from pocket_agent.prefill import draft_answer
from pocket_agent.tools import ToolContext, execute
from pocket_agent.tracker import Tracker


def _jobs() -> dict[str, dict]:
    rows = json.loads((resources_dir() / "fixture_jobs.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def test_score_cases() -> None:
    profile = load_profile(Path("examples/profile.yaml"))
    jobs = _jobs()
    today = date(2026, 11, 4)
    contoso = score_fit(profile, jobs["simplify-newgrad:contoso-swe"], today=today)
    assert contoso["eligible"] is True
    assert contoso["score"] >= 70
    alpine = score_fit(profile, jobs["simplify-newgrad:alpine-clearance"], today=today)
    assert alpine["eligible"] is False
    assert alpine["score"] <= 35
    assert any("clearance" in blocker.lower() for blocker in alpine["blockers"])
    phd = score_fit(profile, jobs["simplify-newgrad:fabrikam-phd"], today=today)
    assert phd["eligible"] is False
    intern = score_fit(profile, jobs["simplify-intern:adventure-intern"], today=today)
    assert intern["eligible"] is False
    assert any("2027" in blocker for blocker in intern["blockers"])
    sponsor_profile = json.loads(json.dumps(profile))
    sponsor_profile["citizenship"]["requires_sponsorship"] = True
    coho = score_fit(sponsor_profile, jobs["simplify-newgrad:coho-nosponsor"], today=today)
    assert coho["eligible"] is False
    local = json.loads(json.dumps(profile))
    local["location"]["willing_to_relocate"] = False
    london = score_fit(local, jobs["simplify-newgrad:adatum-london"], today=today)
    assert london["eligible"] is False
    us_litware = score_fit(profile, jobs["simplify-newgrad:litware-citizen"], today=today)
    assert us_litware["eligible"] is True


def test_prefill_asks_when_missing() -> None:
    profile = load_profile(Path("examples/profile.yaml"))
    covered = draft_answer(profile, "What is your degree and graduation date?")
    assert covered["status"] == "answer"
    assert "Northwind" in covered["answer"]
    salary = draft_answer(profile, "What are your salary expectations?")
    assert salary["status"] == "ASK_USER"
    assert "salary_expectation" in salary["missing_fields"]
    assert draft_answer(profile, "Are you a protected veteran?")["status"] == "ASK_USER"
    assert draft_answer(profile, "Do you have a disability?")["status"] == "ASK_USER"
    assert "do not require" in draft_answer(profile, "Do you require sponsorship?")["answer"].lower()


def test_tools_roundtrip_and_no_submit(home: Path) -> None:
    profile_src = Path("examples/profile.yaml").read_text(encoding="utf-8")
    (home / "profile.yaml").write_text(profile_src, encoding="utf-8")
    tracker = Tracker(home / "tracker.db")
    rows = json.loads((resources_dir() / "fixture_jobs.json").read_text(encoding="utf-8"))
    tracker.upsert_jobs([job_from_public(row) for row in rows], include_inactive=True)
    tracker.close()
    ctx = ToolContext(home=home, today=date(2026, 11, 4), network=False)
    found = execute("search_jobs", {"query": "software", "location": "Austin"}, ctx)
    assert found["count"] >= 1
    closed = [job for job in found["jobs"] if job["company"] == "Closed Example Co"]
    assert closed == []
    scored = execute("score_fit", {"job_id": "simplify-newgrad:contoso-swe"}, ctx)
    saved = execute(
        "db_upsert",
        {"kind": "application", "record": {"job_id": "simplify-newgrad:contoso-swe", "status": "saved"}},
        ctx,
    )
    assert saved["submitted"] is False
    bad = execute(
        "db_upsert",
        {"kind": "application", "record": {"job_id": "simplify-newgrad:contoso-swe", "status": "submit"}},
        ctx,
    )
    assert bad["ok"] is False
    asked = execute("ask_user", {"question": "Salary?", "missing_fields": ["salary_expectation"]}, ctx)
    assert asked["status"] == "ASK_USER"
    assert scored["eligible"] is True
    resume_pdf = home / "resume.pdf"
    write_simple_pdf(resume_pdf, ["Jordan Hale", "jordan.hale@example.com", "Northwind University"])
    parsed = parse_resume(resume_pdf)
    assert "Jordan Hale" in parsed["text"]
    listed = execute("db_query", {"kind": "stats"}, ctx)
    assert listed["applications"] == 1


def test_source_has_no_submit_tool() -> None:
    root = Path("src/pocket_agent")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "submit_application" not in text
        if path.name != "backend.py":
            assert ".post(" not in text
