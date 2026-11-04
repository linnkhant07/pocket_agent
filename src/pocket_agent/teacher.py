"""Collect teacher traces with the same harness, then keep the ones that pass.

The teacher is any OpenAI-compatible server. Set:

- POCKET_AGENT_TEACHER_BASE_URL
- POCKET_AGENT_TEACHER_MODEL
- POCKET_AGENT_TEACHER_API_KEY  (optional; default "ollama")

Ollama's OpenAI shim returned empty content for Qwen3 on this machine, so a
base URL on port 11434 uses the native /api/chat client. Any other URL is
POST {base}/chat/completions. This module does not pick a paid host.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pocket_agent.eval.checker import check_task
from pocket_agent.eval.tasks import tasks_for_suite
from pocket_agent.harness.backend import backend_for
from pocket_agent.harness.loop import run_agent
from pocket_agent.paths import CANARY

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
# Separators keep this off bare ids. +1-555-0100 matches; a 7-digit board id does not.
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s.\-()]{0,2}\d){7,}(?!\w)")


def teacher_settings_from_env() -> dict[str, str]:
    base = os.environ.get("POCKET_AGENT_TEACHER_BASE_URL", "").strip()
    model = os.environ.get("POCKET_AGENT_TEACHER_MODEL", "").strip()
    if not base or not model:
        raise RuntimeError(
            "Set POCKET_AGENT_TEACHER_BASE_URL and POCKET_AGENT_TEACHER_MODEL. "
            "POCKET_AGENT_TEACHER_API_KEY is optional."
        )
    return {
        "base_url": base,
        "model": model,
        "api_key": os.environ.get("POCKET_AGENT_TEACHER_API_KEY", "ollama"),
    }


def backend_name_for(base_url: str) -> str:
    if ":11434" in base_url or "ollama" in base_url.lower():
        return "ollama"
    return "openai"


def scrub_text(text: str, names: list[str] | None = None) -> str:
    """Replace emails, phone numbers, and known profile names. Keep the canary."""
    cleaned = _EMAIL.sub("[redacted-email]", text)
    cleaned = _PHONE.sub("[redacted-phone]", cleaned)
    for name in names or []:
        cleaned = _replace_name(cleaned, name)
    if CANARY not in text and CANARY not in cleaned:
        pass
    return cleaned


def _replace_name(text: str, name: str) -> str:
    parts = [part for part in name.split() if len(part) > 2]
    if len(name) > 2:
        text = re.sub(re.escape(name), "[redacted-name]", text, flags=re.IGNORECASE)
    for part in parts:
        text = re.sub(rf"\b{re.escape(part)}\b", "[redacted-name]", text, flags=re.IGNORECASE)
    return text


def scrub_trace_file(path: Path, names: list[str]) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(_scrub_value(json.loads(line), names))
    return rows


def _scrub_value(value: Any, names: list[str]) -> Any:
    """Scrub string leaves only. Scrubbing the encoded JSON broke on long floats."""
    if isinstance(value, str):
        return scrub_text(value, names)
    if isinstance(value, list):
        return [_scrub_value(item, names) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_value(item, names) for key, item in value.items()}
    return value


def collect_traces(
    *,
    suite: str,
    out: Path,
    limit: int | None = None,
    base_url: str,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    tasks = tasks_for_suite(suite)
    if limit is not None:
        tasks = tasks[:limit]
    name = backend_name_for(base_url)
    client = backend_for(name, base_url=base_url, model=model, api_key=api_key)
    accepted: list[dict[str, Any]] = []
    dropped = 0
    for task in tasks:
        from pocket_agent.eval.runner import _prepare_home, _feed_fixtures
        from pocket_agent.eval.tasks import EVAL_TODAY
        import tempfile

        from pocket_agent.tools import ToolContext

        with tempfile.TemporaryDirectory(prefix="pocket-teach-") as temporary:
            home = Path(temporary)
            _prepare_home(home, task)
            ctx = ToolContext(home=home, today=EVAL_TODAY, network=False, fixtures=_feed_fixtures())
            trace_path = home / "trace.jsonl"
            result = run_agent(task["prompt"], client, ctx, model=model, trace_path=trace_path)
            errors = check_task(task, result)
            if errors or not trace_path.exists():
                dropped += 1
                continue
            names = _names_for(task)
            rows = scrub_trace_file(trace_path, names)
            accepted.append(
                {
                    "task_id": task["id"],
                    "skill": task["skill"],
                    "canary": CANARY,
                    "model": model,
                    "trace": rows,
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in accepted:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "suite": suite,
        "model": model,
        "base_url": base_url,
        "backend": name,
        "seen": len(tasks),
        "accepted": len(accepted),
        "dropped": dropped,
        "out": str(out),
        "canary": CANARY,
    }
    summary_path = out.with_suffix(out.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _names_for(task: dict[str, Any]) -> list[str]:
    profile = task.get("profile") or {}
    names = []
    if profile.get("name"):
        names.append(str(profile["name"]))
    return names
