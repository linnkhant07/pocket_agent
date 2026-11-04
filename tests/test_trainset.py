import json

from pocket_agent.trainset import TRAIN_SEED, TRAIN_START, iter_gold_rows, overlap, train_jobs, train_profiles, train_tasks


def test_train_split_does_not_overlap_eval() -> None:
    shared = overlap()
    leaked = {key: sorted(values) for key, values in shared.items() if values}
    assert leaked == {}
    assert TRAIN_SEED != 428
    assert TRAIN_START >= 5000
    emails = {profile["email"] for profile in train_profiles()}
    assert all(email.endswith("@example.com") for email in emails)
    assert len(emails) == len(train_profiles())


def test_gold_traces_pass_the_checker_and_use_canonical_fields() -> None:
    tasks = {task["id"]: task for task in train_tasks()}
    rows = iter_gold_rows()
    assert len(rows) == len(tasks)
    assert len(rows) >= 60
    ask_fields = []
    for row in rows:
        messages = row["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == tasks[row["task_id"]]["prompt"]
        assert messages[2]["role"] == "assistant"
        for message in messages:
            if message["role"] != "assistant":
                continue
            payload = json.loads(message["content"])
            if payload.get("action") == "ask_user":
                ask_fields.extend(payload["missing_fields"])
    assert "salary_expectation" in ask_fields
    assert "veteran_status" in ask_fields
    assert "FIELD" not in ask_fields
    companies = {job.company for job in train_jobs()}
    assert "Contoso Robotics" not in companies
    assert "Amazon" not in companies
