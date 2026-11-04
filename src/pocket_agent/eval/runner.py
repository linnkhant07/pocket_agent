"""Run the hand-checked suite against a mock script or a live backend."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from pocket_agent.eval.checker import check_task, json_validity, tool_accuracy
from pocket_agent.eval.tasks import EVAL_TODAY, tasks_for_suite
from pocket_agent.harness.backend import backend_for
from pocket_agent.harness.loop import run_agent
from pocket_agent.paths import CANARY, examples_dir, repo_root, resources_dir
from pocket_agent.profile import deep_merge, dump_profile, load_profile
from pocket_agent.snapshot import job_from_public
from pocket_agent.tools import ToolContext
from pocket_agent.tracker import Tracker


def run_eval(
    *,
    suite: str = "week1",
    backend: str = "mock",
    model: str = "mock",
    base_url: str | None = None,
    limit: int | None = None,
    fail_under: float | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    tasks = tasks_for_suite(suite)
    if limit is not None:
        tasks = tasks[:limit]
    ollama_peak = _model_server_rss_mb()
    rows = []
    for task in tasks:
        ollama_peak = max(ollama_peak, _model_server_rss_mb())
        rows.append(_run_one(task, backend=backend, model=model, base_url=base_url))
        ollama_peak = max(ollama_peak, _model_server_rss_mb())
    report = _summarize(rows, suite=suite, backend=backend, model=model, label=label)
    report["peak_rss_mb"] = _peak_rss_mb()
    report["ollama_rss_mb"] = round(ollama_peak, 1) if backend == "ollama" and ollama_peak else None
    report["canary"] = CANARY
    report["today"] = EVAL_TODAY.isoformat()
    if fail_under is not None and report["task_success"] < fail_under:
        report["failed_threshold"] = True
    return report


def _run_one(task: dict[str, Any], *, backend: str, model: str, base_url: str | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pocket-eval-") as temporary:
        home = Path(temporary)
        _prepare_home(home, task)
        ctx = ToolContext(home=home, today=EVAL_TODAY, network=False, fixtures=_feed_fixtures())
        client = backend_for(backend, base_url=base_url, model=model, script=task["script"] if backend == "mock" else None)
        trace_path = home / "traces" / f"{task['id']}.jsonl"
        result = run_agent(task["prompt"], client, ctx, model=model, trace_path=trace_path)
        errors = check_task(task, result)
        return {
            "id": task["id"],
            "skill": task["skill"],
            "success": not errors,
            "errors": errors,
            "tool_accuracy": tool_accuracy(task, result),
            "json_validity": json_validity(result),
            "restraint": bool(task["expect"].get("restraint")),
            "latency_s": result.latency_s,
            "prompt_tokens": result.prompt_tokens if result.usage_seen else None,
            "completion_tokens": result.completion_tokens if result.usage_seen else None,
            "usage_seen": result.usage_seen,
            "final_excerpt": (result.final or "")[:240],
            "tools": [call["name"] for call in result.tools],
            "ask_user": result.ask_user is not None,
        }


def _prepare_home(home: Path, task: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    if task.get("profile"):
        profile = task["profile"]
    else:
        profile = load_profile(examples_dir() / "profile.yaml")
        if task.get("profile_overlay"):
            profile = deep_merge(profile, task["profile_overlay"])
    dump_profile(home / "profile.yaml", profile)
    setup = task.get("setup")
    if setup == "empty":
        Tracker(home / "tracker.db").close()
        return
    tracker = Tracker(home / "tracker.db")
    if setup == "snapshot":
        from pocket_agent.snapshot import load_snapshot

        load_snapshot(tracker, snapshot_path())
    else:
        jobs = [job_from_public(item) for item in _load_json("fixture_jobs.json")]
        tracker.upsert_jobs(jobs, include_inactive=True)
    tracker.close()


def snapshot_path() -> Path:
    root = repo_root()
    if root is None:
        raise FileNotFoundError("the frozen snapshot is shipped in the repo checkout")
    path = root / "data" / "snapshots" / "public_postings.jsonl"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _feed_fixtures() -> dict[str, Any]:
    return {
        "new-grad": _load_json("feed_newgrad.json"),
        "intern": _load_json("feed_intern.json"),
        "greenhouse:fabrikam": _load_json("feed_greenhouse.json"),
        "lever:tailspin": _load_json("feed_lever.json"),
    }


def _load_json(name: str) -> Any:
    return json.loads((resources_dir() / name).read_text(encoding="utf-8"))


def _summarize(rows: list[dict[str, Any]], *, suite: str, backend: str, model: str, label: str | None) -> dict[str, Any]:
    n = len(rows)
    successes = sum(1 for row in rows if row["success"])
    restraint_rows = [row for row in rows if row["restraint"]]
    restraint_hits = sum(1 for row in restraint_rows if row["success"])
    latencies = [row["latency_s"] for row in rows]
    completion = sum(row["completion_tokens"] or 0 for row in rows)
    latency_sum = sum(latencies)
    usage = any(row["usage_seen"] for row in rows) and backend != "mock"
    resolved_label = label or ("CPU smoke test" if "0.6" in model else backend)
    return {
        "label": resolved_label,
        "suite": suite,
        "backend": backend,
        "model": model,
        "n": n,
        "task_success_count": successes,
        "task_success": successes / n if n else 0.0,
        "tool_call_accuracy": (sum(1 for row in rows if row["tool_accuracy"]) / n) if n else 0.0,
        "json_validity": (sum(row["json_validity"] for row in rows) / n) if n else 0.0,
        "restraint_score": (restraint_hits / len(restraint_rows)) if restraint_rows else None,
        "restraint_n": len(restraint_rows),
        "latency_p50_s": _percentile(latencies, 0.50),
        "latency_p95_s": _percentile(latencies, 0.95),
        "tokens_per_second": (completion / latency_sum) if usage and latency_sum else None,
        "completion_tokens": completion if usage else None,
        "tasks": rows,
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _peak_rss_mb() -> float | None:
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return round(int(line.split()[1]) / 1024, 1)
    try:
        import resource
        import sys

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return round(rss / (1024 * 1024), 1)
        return round(rss / 1024, 1)
    except Exception:
        return None


def _model_server_rss_mb() -> float:
    """RSS of the Ollama parent plus llama-server, which is where the weights sit."""
    return max(_named_rss_mb("ollama") or 0.0, _named_rss_mb("llama-server") or 0.0)


def _named_rss_mb(name: str) -> float | None:
    proc = Path("/proc")
    if not proc.exists():
        return None
    total = 0.0
    found = False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "comm").read_text(encoding="utf-8").strip()
            if name not in command:
                continue
            found = True
            for line in (entry / "status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) / 1024
        except OSError:
            continue
    return total if found else None


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"label: {report['label']}",
        f"suite: {report['suite']}  backend: {report['backend']}  model: {report['model']}",
        f"task success: {report['task_success_count']}/{report['n']} ({report['task_success']:.3f})",
        f"tool-call accuracy: {report['tool_call_accuracy']:.3f}",
        f"json validity: {report['json_validity']:.3f}",
        f"restraint: {report['restraint_score']}",
        f"latency p50/p95 s: {report['latency_p50_s']:.4f} / {report['latency_p95_s']:.4f}"
        if report["latency_p50_s"] is not None
        else "latency: n/a",
        f"peak RSS MB: {report.get('peak_rss_mb')}",
        f"model server RSS MB: {report.get('ollama_rss_mb')}",
        f"tokens/s: {report.get('tokens_per_second')}",
    ]
    failed = [row for row in report["tasks"] if not row["success"]]
    if failed:
        lines.append("failed:")
        for row in failed:
            lines.append(f"  {row['id']}: {'; '.join(row['errors'])}")
    return "\n".join(lines)


def load_sources_example() -> dict[str, Any]:
    path = examples_dir() / "sources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def eval_env_today() -> None:
    os.environ["POCKET_AGENT_TODAY"] = EVAL_TODAY.isoformat()
