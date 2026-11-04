import json

from pocket_agent.mix import dedupe, groq_trace_to_messages


def test_groq_conversion_drops_invalid_first_action() -> None:
    steps = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Find jobs at Amber Software."},
        {"role": "assistant", "content": '{"action":"search_jobs"}', "json_valid": False},
        {"role": "user", "content": "That response was not valid. Reply with one JSON object matching the schema."},
        {"role": "assistant", "content": '{"action":"tool","tool":"search_jobs","arguments":{"company":"Amber Software"}}', "json_valid": True},
        {"role": "tool", "name": "search_jobs", "result": {"ok": True, "count": 1, "jobs": []}},
        {"role": "assistant", "content": '{"action":"final","answer":"Amber Software has openings."}', "json_valid": True},
    ]
    messages = groq_trace_to_messages(steps)
    assistants = [message["content"] for message in messages if message["role"] == "assistant"]
    assert len(assistants) == 2
    assert "search_jobs" in assistants[0]
    assert '"tool"' in assistants[0]
    assert not any(message["content"].startswith("That response was not valid") for message in messages)
    assert any(message["content"].startswith("TOOL_RESULT search_jobs") for message in messages)


def test_dedupe_keeps_the_first_copy() -> None:
    rows = [
        {"source": "gold", "skill": "track", "messages": [{"role": "user", "content": "same"}, {"role": "assistant", "content": "a"}]},
        {"source": "groq", "skill": "track", "messages": [{"role": "user", "content": "same"}, {"role": "assistant", "content": "a"}]},
        {"source": "groq", "skill": "find_new_jobs", "messages": [{"role": "user", "content": "other"}, {"role": "assistant", "content": "b"}]},
    ]
    kept, dropped = dedupe(rows)
    assert dropped == 1
    assert [row["source"] for row in kept] == ["gold", "groq"]


def test_real_groq_file_has_forty_traces() -> None:
    from pathlib import Path

    path = Path("data/teacher/teach_traces.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 40
    assert sum(row["model"] == "openai/gpt-oss-120b" for row in rows) == 33
    assert sum(row["model"] == "openai/gpt-oss-20b" for row in rows) == 7
