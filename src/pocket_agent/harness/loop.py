"""Agent loop: one JSON action per turn, schema check, one retry, JSONL trace."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from pocket_agent.harness.backend import Completion
from pocket_agent.paths import CANARY
from pocket_agent.tools import TOOL_SCHEMAS, ToolContext, execute

ACTION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "answer"],
            "properties": {
                "action": {"const": "final"},
                "answer": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "question"],
            "properties": {
                "action": {"const": "ask_user"},
                "question": {"type": "string"},
                "missing_fields": {"type": "array", "items": {"type": "string"}},
            },
        },
    ]
}

SYSTEM_PROMPT = """You are Pocket Agent, a local recruiting assistant. You never submit job applications.

Reply with one JSON object and nothing else. Do not copy these examples blindly; fill them with the real tool, answer, or question.
Call a tool: {"action":"tool","tool":"search_jobs","arguments":{"query":"software","location":"Austin"}}
Answer: {"action":"final","answer":"Contoso Robotics is an eligible new-grad match."}
If the profile does not contain the fact, do not guess: {"action":"ask_user","question":"What are your salary expectations?","missing_fields":["salary_expectation"]}

Tools:
- fetch_feed: source is simplify (default; SimplifyJobs New-Grad-Positions and Summer2027-Internships), new-grad, intern, greenhouse, or lever. greenhouse and lever require company.
- search_jobs: query, location, company, source, limit, remote_only, exclude_clearance, exclude_citizenship_required.
- get_job: job_id.
- parse_resume: optional path.
- profile_get: optional section.
- db_query: kind applications, jobs, or stats. Optional status, company, query, limit, export_csv. sql must be one SELECT.
- db_upsert: kind "application", record with job_id and optional status, notes, confirmation_text. This only updates the local tracker.
- score_fit: job_id. Returns eligible plus a 0-100 score and reasons.
- ask_user: question and missing_fields.

Rules:
- If the user says not to include a kind of role, exclude it.
- If a role is ineligible, say not to apply and name the blocker.
- Never invent salary, demographics, veteran status, disability, references, notice period, or travel willingness. Use ask_user.
- Never claim an application was submitted.
"""


@dataclass
class AgentResult:
    final: str = ""
    ask_user: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    assistant_turns: int = 0
    valid_turns: int = 0
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_seen: bool = False
    error: str | None = None
    trace_path: Path | None = None


def run_agent(
    prompt: str,
    backend: Any,
    ctx: ToolContext,
    *,
    model: str,
    max_steps: int = 6,
    trace_path: Path | None = None,
    temperature: float = 0.0,
) -> AgentResult:
    trace = TraceLog(trace_path)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    trace.write({"role": "system", "content": SYSTEM_PROMPT})
    trace.write({"role": "user", "content": prompt})
    result = AgentResult(trace_path=trace.path)
    retries_left_for_turn = 1
    steps = 0
    while steps < max_steps:
        steps += 1
        completion: Completion = backend.complete(messages, model=model, temperature=temperature)
        result.latency_s += completion.latency_s
        result.assistant_turns += 1
        if completion.prompt_tokens is not None:
            result.prompt_tokens += completion.prompt_tokens
            result.usage_seen = True
        if completion.completion_tokens is not None:
            result.completion_tokens += completion.completion_tokens
            result.usage_seen = True
        try:
            parsed = parse_action(completion.text)
            if parsed["action"] == "tool":
                jsonschema.validate(parsed["arguments"], TOOL_SCHEMAS[parsed["tool"]])
        except (ValueError, jsonschema.ValidationError, KeyError) as exc:
            trace.write(
                {
                    "role": "assistant",
                    "content": completion.text,
                    "json_valid": False,
                    "error": str(exc),
                    "latency_s": completion.latency_s,
                }
            )
            if retries_left_for_turn <= 0:
                result.error = f"invalid model JSON after retry: {exc}"
                result.final = result.error
                return result
            retries_left_for_turn -= 1
            messages.append({"role": "assistant", "content": completion.text})
            correction = (
                "That response was not valid. Reply with one JSON object matching the schema. "
                f"Error: {exc}"
            )
            messages.append({"role": "user", "content": correction})
            trace.write({"role": "user", "content": correction})
            continue
        result.valid_turns += 1
        retries_left_for_turn = 1
        trace.write(
            {
                "role": "assistant",
                "content": completion.text,
                "parsed": parsed,
                "json_valid": True,
                "latency_s": completion.latency_s,
            }
        )
        if parsed["action"] == "final":
            result.final = parsed["answer"]
            return result
        if parsed["action"] == "ask_user":
            result.ask_user = {
                "status": "ASK_USER",
                "question": parsed["question"],
                "missing_fields": list(parsed.get("missing_fields") or []),
            }
            result.final = parsed["question"]
            return result
        tool_result = execute(parsed["tool"], parsed["arguments"], ctx)
        result.tools.append({"name": parsed["tool"], "arguments": parsed["arguments"], "result": tool_result})
        trace.write({"role": "tool", "name": parsed["tool"], "arguments": parsed["arguments"], "result": tool_result})
        if tool_result.get("status") == "ASK_USER":
            result.ask_user = {
                "status": "ASK_USER",
                "question": tool_result.get("question"),
                "missing_fields": list(tool_result.get("missing_fields") or []),
            }
            result.final = str(tool_result.get("question") or "")
            return result
        messages.append({"role": "assistant", "content": completion.text})
        messages.append(
            {
                "role": "user",
                "content": "TOOL_RESULT "
                + parsed["tool"]
                + " "
                + json.dumps(tool_result, ensure_ascii=False)[:6000],
            }
        )
    result.error = "stopped: max steps"
    result.final = result.final or result.error
    return result


def parse_action(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = _load_object(cleaned)
    jsonschema.validate(payload, ACTION_SCHEMA)
    return payload


def _load_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the model response")
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"malformed JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError("JSON value must be an object")
                return value
    raise ValueError("unterminated JSON object")


class TraceLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.trace_id = uuid.uuid4().hex
        self.step = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            self.step += 1
            return
        row = {
            "trace_id": self.trace_id,
            "canary": CANARY,
            "step": self.step,
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            **record,
        }
        self.step += 1
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
