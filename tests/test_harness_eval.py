import json
from datetime import date
from pathlib import Path

from pocket_agent.eval.runner import run_eval
from pocket_agent.eval.tasks import all_tasks, tasks_for_suite
from pocket_agent.harness.backend import MockBackend
from pocket_agent.harness.loop import parse_action, run_agent
from pocket_agent.hardware import select_tier
from pocket_agent.mcp_server import handle_message
from pocket_agent.models import SMOKE_MODEL, TIERS, PERMISSIVE_LICENSES
from pocket_agent.paths import CANARY
from pocket_agent.synthetic import generate_profiles
from pocket_agent.tools import ToolContext


def test_tiers_and_licenses() -> None:
    assert select_tier(8) == "8gb"
    assert select_tier(11.9) == "8gb"
    assert select_tier(12) == "16gb"
    assert select_tier(16) == "16gb"
    assert select_tier(23.9) == "16gb"
    assert select_tier(24) == "32gb"
    assert select_tier(64) == "32gb"
    assert select_tier(None) == "16gb"
    for spec in list(TIERS.values()) + [SMOKE_MODEL]:
        assert spec.license in PERMISSIVE_LICENSES


def test_retry_and_trace(home: Path) -> None:
    (home / "profile.yaml").write_text(Path("examples/profile.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    backend = MockBackend(["not json", '{"action":"final","answer":"recovered"}'])
    result = run_agent("hello", backend, ToolContext(home=home, today=date(2026, 11, 4), network=False), model="mock", trace_path=home / "trace.jsonl")
    assert result.final == "recovered"
    assert result.valid_turns == 1
    assert result.assistant_turns == 2
    lines = (home / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line)["canary"] == CANARY for line in lines)
    fenced = parse_action('```json\n{"action":"final","answer":"ok"}\n```')
    assert fenced["answer"] == "ok"
    thought = parse_action('<think>secret</think>\n{"action":"ask_user","question":"Salary?","missing_fields":["salary_expectation"]}')
    assert thought["action"] == "ask_user"


def test_week1_mock_eval() -> None:
    tasks = all_tasks()
    assert len(tasks) >= 50
    skills = {task["skill"] for task in tasks}
    assert skills == {"find_new_jobs", "screen_and_rank", "track", "prefill_answers"}
    assert len(tasks_for_suite("smoke")) == 4
    report = run_eval(suite="week1", backend="mock", model="mock")
    failed = [row for row in report["tasks"] if not row["success"]]
    assert failed == [], failed
    assert report["task_success"] == 1
    assert report["tool_call_accuracy"] == 1
    assert report["json_validity"] == 1
    assert report["restraint_score"] == 1
    smoke = run_eval(suite="smoke", backend="mock", model="mock")
    assert smoke["n"] == 4
    assert smoke["task_success"] == 1


def test_mcp_lists_tools(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_HOME", str(home))
    (home / "profile.yaml").write_text(Path("examples/profile.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    init = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "pocket-agent"
    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert "score_fit" in names
    assert "fetch_feed" in names
    assert "submit_application" not in names
    called = handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "profile_get", "arguments": {"section": "name"}}},
        ToolContext(home=home, network=False),
    )
    assert "Jordan Hale" in called["result"]["content"][0]["text"]
    note = handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note is None


def test_synthetic_profiles_are_fictional() -> None:
    rows = generate_profiles(300, seed=428)
    assert len(rows) == 300
    assert generate_profiles(300, seed=428)[10]["email"] == rows[10]["email"]
    for row in rows:
        assert row["canary"] == CANARY
        assert row["email"].endswith("@example.com")
        assert "555" in row["phone"]
        assert row["fictional"] is True
        assert row["constraints"]["salary_expectation"] is None
