"""Training mix: 68 gold traces, scripted traces for the 309 teach tasks, and Groq traces.

Invalid teacher turns are dropped before training so the student does not imitate
a first action that failed the schema. Dedupe is on the user prompt plus the
assistant texts. The first copy wins: gold, then teach-gold, then Groq.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pocket_agent.harness.loop import SYSTEM_PROMPT, tool_result_message
from pocket_agent.teacher_tasks import prepare_teach_home, teach_tasks
from pocket_agent.trainset import iter_gold_rows, render_messages

GROQ_PATH = Path(__file__).resolve().parents[2] / "data" / "teacher" / "teach_traces.jsonl"


def teach_gold_rows() -> list[dict[str, Any]]:
    rows = []
    for task in teach_tasks():
        with tempfile.TemporaryDirectory(prefix="pocket-teach-gold-") as temporary:
            ctx = prepare_teach_home(Path(temporary), task)
            rows.append(
                {
                    "source": "teach_gold",
                    "skill": task["skill"],
                    "task_id": task["id"],
                    "messages": render_messages(task, ctx),
                }
            )
    return rows


def groq_rows(path: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    path = path or GROQ_PATH
    rows = []
    dropped_empty = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        messages = groq_trace_to_messages(record["trace"])
        if not any(message["role"] == "assistant" for message in messages):
            dropped_empty += 1
            continue
        rows.append(
            {
                "source": "groq",
                "skill": record.get("skill") or "unknown",
                "task_id": record.get("task_id") or "",
                "model": record.get("model") or "",
                "messages": messages,
            }
        )
    return rows, dropped_empty


def groq_trace_to_messages(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Rebuild the chat the model should imitate. Skip schema-invalid turns."""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    index = 0
    while index < len(steps):
        step = steps[index]
        role = step.get("role")
        if role == "assistant" and step.get("json_valid") is False:
            index += 1
            if index < len(steps) and _is_schema_retry(steps[index]):
                index += 1
            continue
        if role == "system":
            index += 1
            continue
        if role == "user" and step.get("content") and not _is_schema_retry(step):
            messages.append({"role": "user", "content": str(step["content"])})
        elif role == "assistant" and step.get("content"):
            messages.append({"role": "assistant", "content": str(step["content"])})
        elif role == "tool" and step.get("name"):
            messages.append({"role": "user", "content": tool_result_message(step["name"], step.get("result") or {})})
        index += 1
    return messages


def _is_schema_retry(step: dict[str, Any]) -> bool:
    return step.get("role") == "user" and str(step.get("content") or "").startswith("That response was not valid")


def dedupe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    kept = []
    dropped = 0
    for row in rows:
        key = _key(row["messages"])
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped


def build_training_mix(*, include_groq: bool = True, groq_path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gold = [{"source": "gold", "skill": _skill_from_id(row["task_id"]), "task_id": row["task_id"], "messages": row["messages"]} for row in iter_gold_rows()]
    teach = teach_gold_rows()
    groq, dropped_empty = groq_rows(groq_path) if include_groq else ([], 0)
    combined = gold + teach + groq
    kept, dropped_dupes = dedupe(combined)
    summary = {
        "include_groq": include_groq,
        "before_dedupe": _counts(combined),
        "after_dedupe": _counts(kept),
        "dropped_duplicates": dropped_dupes,
        "dropped_empty_groq": dropped_empty,
        "n": len(kept),
    }
    return kept, summary


def _skill_from_id(task_id: str) -> str:
    from pocket_agent.trainset import train_tasks

    for task in train_tasks():
        if task["id"] == task_id:
            return task["skill"]
    return "unknown"


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, Counter] = {}
    for row in rows:
        by_source.setdefault(row["source"], Counter())[row["skill"]] += 1
    return {
        source: {"by_skill": dict(counts), "n": sum(counts.values())}
        for source, counts in by_source.items()
    }


def _key(messages: list[dict[str, str]]) -> str:
    body = json.dumps(
        [{"role": message["role"], "content": message["content"]} for message in messages if message["role"] != "system"],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()
