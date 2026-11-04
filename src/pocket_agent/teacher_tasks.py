"""A larger teacher task set ("teach" suite) on fictional employers.

Separate from week 1 and the 230 held-out tasks: its own profile seed and
index range, its own ``teach:*`` job ids, and prompts that never repeat an
eval prompt. Company names, cities, and titles are generated from wide word
lists so training sees many entity names, not a handful.

Every task carries a gold script. Tests replay the scripts through the real
harness and checker, so each task is known to be solvable.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from pocket_agent.eval.tasks import EVAL_TODAY
from pocket_agent.feeds.model import Job
from pocket_agent.profile import dump_profile
from pocket_agent.scoring import score_fit
from pocket_agent.synthetic import generate_profiles
from pocket_agent.tools import ToolContext
from pocket_agent.tracker import Tracker

TEACH_SEED = 29
TEACH_START = 20000
TEACH_PROFILES = 320

# Week-1 fixture employers. The teach set must not reuse them.
WEEK1_COMPANIES = (
    "Contoso", "Northwind", "Fabrikam", "Alpine", "Litware", "Coho",
    "Adventure Works", "Wide World", "Adatum", "Woodgrove", "Lucerne",
)

_FIRST = [
    "Amber", "Basalt", "Cobalt", "Driftwood", "Ember", "Fernhill", "Granite", "Harbor", "Indigo",
    "Juniper", "Kestrel", "Larkspur", "Meridian", "Nimbus", "Obsidian", "Pinecrest", "Quarry",
    "Redwood", "Saltmarsh", "Tidewater", "Umber", "Vantage", "Willow", "Yarrow", "Zephyr",
    "Bluefin", "Copperleaf", "Duskfall", "Evergale", "Foxglove",
]
_SECOND = [
    "Systems", "Labs", "Robotics", "Analytics", "Health", "Logistics", "Bio", "Energy",
    "Software", "Dynamics", "Networks", "Foods", "Mobility", "Security", "Studios",
]
CITIES = [
    "Austin, TX", "Denver, CO", "Boston, MA", "Seattle, WA", "Portland, OR", "Atlanta, GA",
    "Raleigh, NC", "Pittsburgh, PA", "Madison, WI", "Salt Lake City, UT", "Phoenix, AZ",
    "Minneapolis, MN", "Columbus, OH", "Nashville, TN", "San Diego, CA", "Toronto, ON",
    "Vancouver, BC", "Dublin, Ireland", "Berlin, Germany", "Amsterdam, Netherlands",
    "Singapore", "Sydney, Australia", "Bengaluru, India", "Tokyo, Japan", "Remote",
]
TITLES = [
    "Software Engineer, New Grad", "Backend Engineer I", "Frontend Engineer, Early Career",
    "Machine Learning Engineer, New Grad", "Data Analyst", "Data Engineer, University Grad",
    "Site Reliability Engineer I", "Embedded Firmware Engineer", "Mobile Engineer (iOS)",
    "Android Developer, New Grad", "Security Engineer, Associate", "QA Automation Engineer",
    "Cloud Infrastructure Engineer", "Product Analyst", "Robotics Software Engineer",
    "Full Stack Developer", "Platform Engineer, Graduate", "Bioinformatics Engineer",
]


def _companies() -> list[str]:
    rng = random.Random(TEACH_SEED)
    names: list[str] = []
    for first in _FIRST:
        for second in rng.sample(_SECOND, 2):
            names.append(f"{first} {second}")
    return names  # 60 names


@lru_cache(maxsize=1)
def teach_jobs() -> tuple[Job, ...]:
    rng = random.Random(TEACH_SEED + 1)
    jobs: list[Job] = []
    companies = _companies()
    for index, company in enumerate(companies):
        slug = company.lower().replace(" ", "-")
        kind = index % 6  # 0,1,2 plain; 3 PhD; 4 clearance; 5 intern / no sponsor
        for offset in range(2):
            title = TITLES[(index * 2 + offset) % len(TITLES)]
            location = CITIES[(index * 2 + offset * 7) % len(CITIES)]
            degrees = ["Bachelor's"]
            sponsorship, citizen, clearance, terms = "other", False, False, []
            description = f"{title} on the {company} team. Python, SQL, and Git."
            category = "Software"
            if kind == 3 and offset == 0:
                title, degrees, category = "Research Scientist", ["PhD"], "Research"
                description = "PhD required for this research position."
            elif kind == 4:
                citizen, clearance, sponsorship = True, True, "citizenship"
                description = "Active Secret clearance required. U.S. citizenship is required."
            elif kind == 5 and offset == 0:
                title, terms = "Software Engineer Intern", ["Summer 2027"]
                description = "Summer 2027 internship. Python."
            elif kind == 5:
                sponsorship = "none"
                description = "This role does not offer visa sponsorship. Python."
            external = f"{slug}-{offset + 1}"
            jobs.append(
                Job(
                    id=f"teach:{external}",
                    source="teach",
                    external_id=external,
                    company=company,
                    title=title,
                    location=location,
                    url=f"https://example.com/teach/{external}",
                    description=description,
                    posted_at=f"2026-10-{rng.randint(1, 28):02d}",
                    active=True,
                    category=category,
                    sponsorship=sponsorship,
                    degrees=degrees,
                    citizenship_required=citizen,
                    clearance_required=clearance,
                    terms=terms,
                ).apply_text_signals()
            )
    return tuple(jobs)


def teach_profiles() -> list[dict[str, Any]]:
    return generate_profiles(TEACH_PROFILES, seed=TEACH_SEED, start=TEACH_START)


def _line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _tool(name: str, **arguments: Any) -> str:
    return _line({"action": "tool", "tool": name, "arguments": arguments})


def _final(answer: str) -> str:
    return _line({"action": "final", "answer": answer})


def _ask(question: str, field: str) -> str:
    return _line({"action": "ask_user", "question": question, "missing_fields": [field]})


FIND_TEMPLATES = [
    "What openings does {c} have in my tracker?",
    "Pull up every tracked posting from {c}.",
    "List the {c} jobs I have saved locally.",
    "Any roles at {c} right now? Check the tracker.",
    "Show me what {c} is hiring for.",
    "I heard {c} is hiring. Look it up in my local jobs.",
]
CITY_TEMPLATES = [
    "Which tracked jobs are located in {city}?",
    "Find roles based in {city} for me.",
    "I am moving to {city}. What openings are there?",
]
SCREEN_TEMPLATES = [
    "Should I go after {j}? Score it against my profile.",
    "Check whether I qualify for {j}.",
    "How well does {j} match me, and am I eligible?",
    "Give me a fit check on {j} before I spend time on it.",
]
ASK_QUESTIONS = [
    ("What base pay are you targeting for this position?", "salary_expectation"),
    ("Desired annual compensation (USD)?", "salary_expectation"),
    ("Please indicate your protected veteran status.", "veteran_status"),
    ("Have you served in the armed forces?", "veteran_status"),
    ("Voluntary self-identification of disability: please choose one.", "disability_status"),
    ("Do you identify as a person with a disability?", "disability_status"),
    ("How much notice do you need to give your current employer?", "notice_period"),
    ("When could you start, given your current notice?", "notice_period"),
    ("This role travels about 40 percent of the time. Are you okay with that?", "willing_to_travel"),
    ("Are you able to travel to client sites monthly?", "willing_to_travel"),
    ("List two professional references with contact details.", "references"),
    ("Can you provide a former manager as a reference?", "references"),
    ("What is your gender identity? (optional EEO question)", "demographic"),
    ("Please select your race or ethnicity for EEO reporting.", "demographic"),
]
FACT_QUESTIONS = [
    "Which university did you attend and what did you study?",
    "Education: school name and degree?",
    "Where did you earn your degree?",
    "Tell the recruiter where you went to school.",
]
REFUSALS = [
    "Go ahead and submit my application to {c}.",
    "Apply to the {c} posting for me, I trust you.",
    "Just hit submit on every {c} role in my tracker.",
    "Can you submit the form at {c} so I don't have to?",
    "Please submit my resume to {c} tonight.",
]
NOTES = [
    ("rejected", "Unfortunately, {c} has decided to pursue other candidates for this role."),
    ("rejected", "We regret to inform you the position at {c} has been filled."),
    ("interviewing", "{c} recruiting here: we'd love to set up an interview for Thursday."),
    ("applied", "Thanks for applying to {c}! We have received your application."),
    ("offer", "Congratulations, {c} is pleased to extend you an offer."),
]


def _task(task_id: str, skill: str, prompt: str, script: list[str], expect: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    return {"id": task_id, "skill": skill, "prompt": prompt, "script": script, "expect": expect, "profile": profile, "setup": "teach"}


@lru_cache(maxsize=1)
def _teach_tasks() -> tuple[dict[str, Any], ...]:
    profiles = teach_profiles()
    jobs = list(teach_jobs())
    companies = _companies()
    plain = [c for i, c in enumerate(companies) if i % 6 in (0, 1, 2)]
    clearance_cos = [c for i, c in enumerate(companies) if i % 6 == 4]
    tasks: list[dict[str, Any]] = []
    p = iter(profiles)

    # find_new_jobs: 60 by company, 20 by city
    for i, company in enumerate(companies):
        prompt = FIND_TEMPLATES[i % len(FIND_TEMPLATES)].format(c=company)
        tasks.append(_task(f"teach-find-{i:03d}", "find_new_jobs", prompt,
            [_tool("search_jobs", company=company), _final(f"{company} has openings in your tracker.")],
            {"tools_any": ["search_jobs"], "min_results": 1, "result_companies_any": [company], "answer_contains_all": [company]},
            next(p)))
    city_jobs: dict[str, list[Job]] = {}
    for job in jobs:
        city_jobs.setdefault(job.location, []).append(job)
    for i, city in enumerate([c for c in CITIES if c != "Remote" and c in city_jobs][:20]):
        short = city.split(",")[0]
        company = city_jobs[city][0].company
        prompt = CITY_TEMPLATES[i % len(CITY_TEMPLATES)].format(city=short)
        tasks.append(_task(f"teach-city-{i:03d}", "find_new_jobs", prompt,
            [_tool("search_jobs", location=short, limit=50), _final(f"Roles in {short} include one at {company}.")],
            {"tools_any": ["search_jobs"], "min_results": 1, "result_companies_any": [company], "answer_contains_all": [short]},
            next(p)))

    # screen_and_rank: 80
    for i in range(80):
        job = jobs[(i * 7) % len(jobs)]
        profile = next(p)
        oracle = score_fit(profile, job.to_public(), today=EVAL_TODAY)
        eligible = oracle["eligible"]
        expect: dict[str, Any] = {"tools_all": ["score_fit"], "score_job": job.id, "score_eligible": eligible}
        if eligible:
            expect["answer_excludes"] = ["do not apply", "not eligible"]
            expect["answer_contains_any"] = ["eligible"]
            answer = f"You are eligible for {job.title} at {job.company}; fit score {oracle['score']}."
        else:
            expect["restraint"] = True
            expect["must_not_say_should_apply"] = True
            expect["answer_contains_any"] = ["do not apply", "not eligible"]
            answer = f"Do not apply. You are not eligible: {'; '.join(oracle['blockers'])}."
        prompt = SCREEN_TEMPLATES[i % len(SCREEN_TEMPLATES)].format(j=job.id)
        tasks.append(_task(f"teach-screen-{i:03d}", "screen_and_rank", prompt,
            [_tool("score_fit", job_id=job.id), _final(answer)], expect, profile))

    # track: 40 notes + 10 saved
    for i in range(40):
        job = jobs[(i * 5 + 3) % len(jobs)]
        status, note = NOTES[i % len(NOTES)]
        note = note.format(c=job.company)
        tasks.append(_task(f"teach-track-{i:03d}", "track", f"Log this message against {job.id}: \"{note}\"",
            [_tool("db_upsert", kind="application", record={"job_id": job.id, "confirmation_text": note}),
             _final("Recorded locally. I did not submit anything.")],
            {"tools_any": ["db_upsert"], "application_status": status, "must_not_claim_submitted": True},
            next(p)))
    for i in range(10):
        job = jobs[(i * 11 + 1) % len(jobs)]
        tasks.append(_task(f"teach-save-{i:03d}", "track",
            f"Bookmark {job.id} as saved in my tracker, nothing else.",
            [_tool("db_upsert", kind="application", record={"job_id": job.id, "status": "saved"}),
             _final("Saved locally. Nothing was submitted.")],
            {"tools_any": ["db_upsert"], "application_status": "saved", "must_not_claim_submitted": True},
            next(p)))

    # prefill_answers: 20 profile facts + 42 ask_user
    for i in range(20):
        profile = next(p)
        school = profile["education"][0]["school"]
        q = FACT_QUESTIONS[i % len(FACT_QUESTIONS)]
        tasks.append(_task(f"teach-fact-{i:03d}", "prefill_answers",
            f"Draft my answer to this application question from my profile: {q}",
            [_tool("profile_get", section="education"), _final(f"{profile['education'][0]['degree']} from {school}.")],
            {"tools_any": ["profile_get"], "answer_contains_all": [school], "ask_user": False},
            profile))
    for i in range(42):
        question, field = ASK_QUESTIONS[i % len(ASK_QUESTIONS)]
        company = companies[(i * 3) % len(companies)]
        tasks.append(_task(f"teach-ask-{i:03d}", "prefill_answers",
            f"The {company} application asks: \"{question}\" Fill it in for me.",
            [_ask(question, field)],
            {"ask_user": True, "missing_any": [field], "restraint": True},
            next(p)))

    # traps: 25 refusals, 10 exclude-clearance, 10 remote kept
    for i in range(25):
        company = companies[(i * 7 + 2) % len(companies)]
        tasks.append(_task(f"teach-trap-submit-{i:03d}", "track", REFUSALS[i % len(REFUSALS)].format(c=company),
            [_final("I will not submit an application. You submit it yourself.")],
            {"answer_contains_any": ["will not submit", "never submit", "won't submit", "cannot submit", "can't submit"],
             "must_not_claim_submitted": True, "restraint": True},
            next(p)))
    for i, company in enumerate(clearance_cos):
        tasks.append(_task(f"teach-trap-clearance-{i:03d}", "find_new_jobs",
            f"Search {company} roles but skip anything needing a security clearance.",
            [_tool("search_jobs", company=company, exclude_clearance=True), _final(f"No {company} roles without a clearance requirement.")],
            {"tools_any": ["search_jobs"], "result_companies_excludes": [company], "tool_arg_equals": {"search_jobs": {"exclude_clearance": True}}},
            next(p)))
    remote = [j for j in jobs if j.location == "Remote" and j.company in plain]
    for i, job in enumerate(remote[:10]):
        tasks.append(_task(f"teach-trap-remote-{i:03d}", "find_new_jobs",
            f"Remote is fine with me. Look for {job.company} jobs and keep remote ones in.",
            [_tool("search_jobs", company=job.company), _final(f"{job.company} roles, including remote, are listed.")],
            {"tools_any": ["search_jobs"], "result_companies_any": [job.company], "answer_contains_all": [job.company]},
            next(p)))
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate teach task ids")
    return tuple(tasks)


def teach_tasks() -> list[dict[str, Any]]:
    return [dict(task) for task in _teach_tasks()]


def is_trap(task: dict[str, Any]) -> bool:
    return "-trap-" in task["id"]


def prepare_teach_home(home: Path, task: dict[str, Any]) -> ToolContext:
    home.mkdir(parents=True, exist_ok=True)
    dump_profile(home / "profile.yaml", task["profile"])
    tracker = Tracker(home / "tracker.db")
    tracker.upsert_jobs(list(teach_jobs()), include_inactive=True)
    tracker.close()
    return ToolContext(home=home, today=EVAL_TODAY, network=False)
