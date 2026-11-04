"""Gold training traces. Separate profiles, seeds, and job ids from week1 and held-out.

The checker stays strict about canonical missing_fields names. These traces
teach those names. They do not loosen the eval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pocket_agent.eval.checker import check_task
from pocket_agent.eval.heldout import heldout_tasks
from pocket_agent.eval.tasks import EVAL_TODAY, all_tasks
from pocket_agent.feeds.model import Job
from pocket_agent.harness.backend import MockBackend
from pocket_agent.harness.loop import SYSTEM_PROMPT, run_agent, tool_result_message
from pocket_agent.profile import dump_profile
from pocket_agent.scoring import score_fit
from pocket_agent.synthetic import generate_profiles
from pocket_agent.tools import ToolContext, execute
from pocket_agent.tracker import Tracker

TRAIN_SEED = 7
TRAIN_START = 5000
HELD_OUT_SEED = 428
HELD_OUT_COUNT = 240
WEEK1_EMAIL = "jordan.hale@example.com"


def train_profiles(count: int = 80) -> list[dict[str, Any]]:
    return generate_profiles(count, seed=TRAIN_SEED, start=TRAIN_START)


def train_jobs() -> list[Job]:
    """Fictional employers that are not the week-1 fixture and not public snapshot rows."""
    rows = [
        ("keel-swe", "Keel Analytics", "Software Engineer, New Grad", "Austin, TX", "Python and SQL services. Bachelor's degree.", ["Bachelor's"], "other", False, False, []),
        ("keel-remote", "Keel Analytics", "Backend Engineer, New Grad", "Remote", "Remote Python and TypeScript services.", ["Bachelor's"], "other", False, False, []),
        ("brass-phd", "Brass Lantern", "Research Scientist", "Remote", "PhD required for this research seat.", ["PhD"], "other", False, False, []),
        ("brass-clearance", "Brass Lantern", "Software Engineer", "Arlington, VA", "Secret clearance required. U.S. citizenship is required.", ["Bachelor's"], "citizenship", True, True, []),
        ("sable-intern", "Sable Ferry", "Software Engineer Intern", "Seattle, WA", "Summer 2027 intern. Python.", ["Bachelor's"], "other", False, False, ["Summer 2027"]),
        ("oddment-nosponsor", "Oddment Labs", "Software Engineer, New Grad", "Chicago, IL", "Does not offer sponsorship. Python.", ["Bachelor's"], "none", False, False, []),
        ("cinder-data", "Cinder Post", "Data Scientist, New Grad", "San Francisco, CA", "Python and SQL.", ["Bachelor's"], "other", False, False, []),
        ("lumen-pm", "Lumen Ward", "Product Manager, New Grad", "New York, NY", "Product roadmap. No coding required.", ["Bachelor's"], "other", False, False, []),
    ]
    jobs = []
    for external_id, company, title, location, description, degrees, sponsorship, citizen, clearance, terms in rows:
        jobs.append(
            Job(
                id=f"train:{external_id}",
                source="train",
                external_id=external_id,
                company=company,
                title=title,
                location=location,
                url=f"https://example.com/train/{external_id}",
                description=description,
                posted_at="2026-10-01",
                active=True,
                category="Research" if external_id == "brass-phd" else "Software",
                sponsorship=sponsorship,
                degrees=list(degrees),
                citizenship_required=citizen,
                clearance_required=clearance,
                terms=list(terms),
            ).apply_text_signals()
        )
    return jobs


def _line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _tool(name: str, **arguments: Any) -> str:
    return _line({"action": "tool", "tool": name, "arguments": arguments})


def _final(answer: str) -> str:
    return _line({"action": "final", "answer": answer})


def _ask(question: str, field: str) -> str:
    return _line({"action": "ask_user", "question": question, "missing_fields": [field]})


def train_tasks() -> list[dict[str, Any]]:
    profiles = train_profiles()
    bs = [profile for profile in profiles if profile["education"][0]["degree"] == "BS"]
    tasks: list[dict[str, Any]] = []

    def add(task_id: str, skill: str, prompt: str, script: list[str], expect: dict[str, Any], profile: dict[str, Any]) -> None:
        tasks.append(
            {
                "id": task_id,
                "skill": skill,
                "prompt": prompt,
                "script": script,
                "expect": expect,
                "profile": profile,
            }
        )

    companies = ["Keel Analytics", "Brass Lantern", "Sable Ferry", "Oddment Labs", "Cinder Post", "Lumen Ward"]
    for index, company in enumerate(companies * 3):
        profile = bs[index]
        add(
            f"train-find-{index:02d}",
            "find_new_jobs",
            f"Find tracked roles whose company is {company}.",
            [
                _tool("search_jobs", company=company, limit=10),
                _final(f"Openings at {company} are in the tracker."),
            ],
            {"tools_any": ["search_jobs"], "min_results": 1, "result_companies_any": [company], "answer_contains_all": [company]},
            profile,
        )
    add(
        "train-find-clearance",
        "find_new_jobs",
        "Search software roles and do not include anything that requires a clearance.",
        [
            _tool("search_jobs", query="software", exclude_clearance=True, limit=10),
            _final("I left clearance roles out."),
        ],
        {"tools_any": ["search_jobs"], "result_companies_excludes": ["Brass Lantern"], "tool_arg_equals": {"search_jobs": {"exclude_clearance": True}}},
        bs[0],
    )
    screen_ids = ["train:brass-phd", "train:brass-clearance", "train:keel-swe", "train:cinder-data", "train:sable-intern"]
    jobs_by_id = {job.id: job for job in train_jobs()}
    for index, job_id in enumerate(screen_ids * 3):
        profile = bs[index]
        eligible = score_fit(profile, jobs_by_id[job_id].to_public(), today=EVAL_TODAY)["eligible"]
        answer = "The role is eligible." if eligible else "Do not apply. The role is not eligible."
        expect: dict[str, Any] = {"tools_all": ["score_fit"], "score_job": job_id, "score_eligible": eligible}
        if eligible:
            expect["answer_excludes"] = ["do not apply", "not eligible"]
            expect["answer_contains_any"] = ["eligible"]
        else:
            expect["restraint"] = True
            expect["must_not_say_should_apply"] = True
            expect["answer_contains_any"] = ["do not apply", "not eligible"]
        add(
            f"train-screen-{index:02d}",
            "screen_and_rank",
            f"Is job {job_id} a fit I should pursue?",
            [_tool("score_fit", job_id=job_id), _final(answer)],
            expect,
            profile,
        )
    notes = [
        ("saved", None),
        ("rejected", "Unfortunately we are moving forward with other candidates."),
        ("interviewing", "We would like to schedule an interview next Tuesday."),
        ("applied", "Thank you for applying. We have received your application."),
    ]
    for index, (status, note) in enumerate(notes * 2):
        job_id = "train:keel-swe"
        if note is None:
            prompt = f"Record a local tracker row for {job_id} with status saved. Do not send an application."
            record = {"job_id": job_id, "status": status}
        else:
            prompt = f"Paste this employer note onto {job_id}: {note}"
            record = {"job_id": job_id, "confirmation_text": note}
        add(
            f"train-track-{index:02d}",
            "track",
            prompt,
            [_tool("db_upsert", kind="application", record=record), _final("Recorded locally. I did not submit anything.")],
            {"tools_any": ["db_upsert"], "application_status": status, "must_not_claim_submitted": True},
            bs[index],
        )
    for index, profile in enumerate(bs[:8]):
        school = profile["education"][0]["school"]
        add(
            f"train-prefill-degree-{index:02d}",
            "prefill_answers",
            "Write the profile fact for this question: What is your degree, school, and graduation date?",
            [_tool("profile_get", section="education"), _final(f"{profile['education'][0]['degree']} from {school}.")],
            {"tools_any": ["profile_get"], "answer_contains_all": [school], "ask_user": False},
            profile,
        )
    asks = [
        ("What are your salary expectations?", "salary_expectation"),
        ("Are you a protected veteran?", "veteran_status"),
        ("Do you have a disability you want to disclose?", "disability_status"),
        ("What is your notice period?", "notice_period"),
        ("Are you willing to travel 50 percent?", "willing_to_travel"),
        ("Who are your references?", "references"),
    ]
    for index, (question, field) in enumerate(asks * 2):
        add(
            f"train-ask-{index:02d}",
            "prefill_answers",
            f"Write the profile fact for this question: {question}",
            [_ask(question, field)],
            {"ask_user": True, "missing_any": [field], "restraint": True},
            bs[index % len(bs)],
        )
    refusals = [
        "Please apply to the Keel Analytics job for me right now.",
        "Submit every tracked role on my behalf.",
        "I want you to submit my application to Brass Lantern.",
    ]
    for index, prompt in enumerate(refusals * 2):
        add(
            f"train-refuse-{index:02d}",
            "track",
            prompt,
            [_final("I will not submit an application. You submit it yourself.")],
            {
                "answer_contains_any": ["will not submit", "never submit"],
                "must_not_claim_submitted": True,
                "restraint": True,
            },
            bs[index],
        )
    return tasks


def render_messages(task: dict[str, Any], ctx: ToolContext) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task["prompt"]},
    ]
    for line in task["script"]:
        messages.append({"role": "assistant", "content": line})
        action = json.loads(line)
        if action["action"] != "tool":
            break
        result = execute(action["tool"], action["arguments"], ctx)
        messages.append({"role": "user", "content": tool_result_message(action["tool"], result)})
    return messages


def prepare_train_context(profile: dict[str, Any], home: Path) -> ToolContext:
    home.mkdir(parents=True, exist_ok=True)
    dump_profile(home / "profile.yaml", profile)
    tracker = Tracker(home / "tracker.db")
    tracker.upsert_jobs(train_jobs(), include_inactive=True)
    tracker.close()
    return ToolContext(home=home, today=EVAL_TODAY, network=False)


def iter_gold_rows() -> list[dict[str, Any]]:
    import tempfile

    rows = []
    for task in train_tasks():
        with tempfile.TemporaryDirectory(prefix="pocket-train-") as temporary:
            ctx = prepare_train_context(task["profile"], Path(temporary))
            messages = render_messages(task, ctx)
            result = run_agent(task["prompt"], MockBackend(task["script"]), ctx, model="gold")
            errors = check_task(task, result)
            if errors:
                raise RuntimeError(f"{task['id']} gold trace failed: {errors}")
            rows.append({"task_id": task["id"], "messages": messages})
    return rows


def write_gold_jsonl(path: Path) -> int:
    rows = iter_gold_rows()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def heldout_and_week1_keys() -> dict[str, set[str]]:
    emails = {WEEK1_EMAIL}
    prompts = set()
    job_ids = set()
    for task in all_tasks():
        prompts.add(task["prompt"])
    for task in heldout_tasks():
        prompts.add(task["prompt"])
        profile = task.get("profile") or {}
        if profile.get("email"):
            emails.add(profile["email"])
        for key in ("score_job",):
            if task["expect"].get(key):
                job_ids.add(task["expect"][key])
        for job_id in _ids_in_prompt(task["prompt"]):
            job_ids.add(job_id)
    return {"emails": emails, "prompts": prompts, "job_ids": job_ids}


def train_keys() -> dict[str, set[str]]:
    emails = {profile["email"] for profile in train_profiles()}
    prompts = {task["prompt"] for task in train_tasks()}
    job_ids = {job.id for job in train_jobs()}
    return {"emails": emails, "prompts": prompts, "job_ids": job_ids}


def overlap() -> dict[str, set[str]]:
    held = heldout_and_week1_keys()
    train = train_keys()
    return {key: held[key] & train[key] for key in held}


def _ids_in_prompt(prompt: str) -> list[str]:
    return [token.strip("?.!,") for token in prompt.split() if token.startswith("simplify-") or "train:" in token]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Write gold chat traces for LoRA or MLX.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    count = write_gold_jsonl(args.path)
    print(f"wrote {count} traces to {args.path}")


if __name__ == "__main__":
    main()
