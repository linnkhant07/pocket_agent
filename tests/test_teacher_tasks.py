import tempfile
from collections import Counter
from pathlib import Path

from pocket_agent.eval.checker import check_task
from pocket_agent.harness.backend import MockBackend
from pocket_agent.harness.loop import run_agent
from pocket_agent.teacher_tasks import (
    WEEK1_COMPANIES, TEACH_SEED, is_trap, prepare_teach_home, teach_jobs, teach_profiles, teach_tasks,
)
from pocket_agent.trainset import HELD_OUT_SEED, heldout_and_week1_keys, train_keys


def test_teach_set_size_and_coverage() -> None:
    tasks = teach_tasks()
    assert len(tasks) >= 300
    skills = Counter(task["skill"] for task in tasks)
    assert set(skills) == {"find_new_jobs", "screen_and_rank", "track", "prefill_answers"}
    assert sum(is_trap(task) for task in tasks) >= 30
    assert any(task["expect"].get("ask_user") for task in tasks)
    assert len({job.company for job in teach_jobs()}) >= 50
    assert len({job.location for job in teach_jobs()}) >= 20


def test_teach_set_does_not_overlap_eval_or_gold_train() -> None:
    held = heldout_and_week1_keys()
    gold = train_keys()
    tasks = teach_tasks()
    assert TEACH_SEED not in (HELD_OUT_SEED, 7)
    emails = {task["profile"]["email"] for task in tasks}
    prompts = {task["prompt"] for task in tasks}
    job_ids = {job.id for job in teach_jobs()}
    assert not emails & held["emails"] and not emails & gold["emails"]
    assert not prompts & held["prompts"] and not prompts & gold["prompts"]
    assert not job_ids & held["job_ids"] and not job_ids & gold["job_ids"]
    from pocket_agent.eval.heldout import heldout_tasks
    from pocket_agent.eval.tasks import all_tasks

    eval_ids = {task["id"] for task in all_tasks()} | {task["id"] for task in heldout_tasks()}
    assert not {task["id"] for task in tasks} & eval_ids
    for job in teach_jobs():
        assert not any(name.lower() in job.company.lower() for name in WEEK1_COMPANIES)
    assert all(profile["fictional"] and profile["email"].endswith("@example.com") for profile in teach_profiles())


def test_teach_gold_scripts_pass_the_checker() -> None:
    for task in teach_tasks():
        with tempfile.TemporaryDirectory() as temporary:
            ctx = prepare_teach_home(Path(temporary), task)
            result = run_agent(task["prompt"], MockBackend(list(task["script"])), ctx, model="gold")
            assert check_task(task, result) == [], task["id"]
