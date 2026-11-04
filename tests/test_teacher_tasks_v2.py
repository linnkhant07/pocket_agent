import json
import tempfile
from pathlib import Path

from pocket_agent.eval.checker import check_task
from pocket_agent.eval.heldout import heldout_tasks
from pocket_agent.eval.tasks import all_tasks
from pocket_agent.harness.backend import MockBackend
from pocket_agent.harness.loop import run_agent
from pocket_agent.teacher_tasks import WEEK1_COMPANIES, teach_jobs, teach_tasks
from pocket_agent.teacher_tasks_v2 import (
    _A, is_restraint, prepare_v2_home, v2_companies, v2_jobs, v2_profiles, v2_tasks,
)
from pocket_agent.trainset import heldout_and_week1_keys, train_keys


def test_v2_size_diversity_and_restraint_share() -> None:
    tasks = v2_tasks()
    assert len(tasks) >= 300
    assert len({job.company for job in v2_jobs()}) >= 90
    assert len({job.location for job in v2_jobs()}) >= 25
    assert len({job.title for job in v2_jobs()}) >= 100
    share = sum(map(is_restraint, tasks)) / len(tasks)
    assert 0.35 <= share <= 0.45


def test_v2_no_overlap_with_eval_teach_v1_or_gold() -> None:
    held = heldout_and_week1_keys()
    gold = train_keys()
    tasks = v2_tasks()
    emails = {t["profile"]["email"] for t in tasks}
    prompts = {t["prompt"] for t in tasks}
    assert not emails & held["emails"] and not emails & gold["emails"]
    assert not emails & {t["profile"]["email"] for t in teach_tasks()}
    assert not prompts & held["prompts"] and not prompts & gold["prompts"]
    assert not prompts & {t["prompt"] for t in teach_tasks()}
    eval_text = " ".join((t["prompt"] + json.dumps(t["expect"])).lower() for t in heldout_tasks() + all_tasks())
    assert [w for w in _A if w.lower() in eval_text] == []
    v1 = {job.company for job in teach_jobs()}
    for company in v2_companies():
        assert company not in v1
        assert not any(name.lower() in company.lower() for name in WEEK1_COMPANIES)
    assert all(p["fictional"] and p["email"].endswith("@example.com") for p in v2_profiles())


def test_v2_gold_scripts_pass_the_checker() -> None:
    for task in v2_tasks():
        with tempfile.TemporaryDirectory() as temporary:
            ctx = prepare_v2_home(Path(temporary), task)
            result = run_agent(task["prompt"], MockBackend(list(task["script"])), ctx, model="gold")
            assert check_task(task, result) == [], task["id"]
