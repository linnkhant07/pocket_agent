"""Collect teacher traces on the teach set from Groq's free OpenAI-compatible API.

Reads GROQ_API_KEY from the environment and never prints it. Throttles to the
free-tier RPM/TPM, retries 429 with backoff, and stops at a daily cap.
Resumable: accepted traces and per-attempt rows are appended to JSONL, and a
task already accepted (any model) or already attempted by this model is skipped.

    uv run python scripts/collect_groq_traces.py --model openai/gpt-oss-120b --minutes 40
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

import httpx

from pocket_agent.eval.checker import check_task
from pocket_agent.harness.backend import Completion
from pocket_agent.harness.loop import run_agent
from pocket_agent.paths import CANARY
from pocket_agent.teacher import scrub_trace_file
from pocket_agent.teacher_tasks import prepare_teach_home, teach_tasks

BASE_URL = "https://api.groq.com/openai/v1"


class DailyCap(Exception):
    pass


class GroqBackend:
    def __init__(self, model: str, rpm: int = 28, tpm: int = 7500) -> None:
        self.model = model
        self.key = os.environ["GROQ_API_KEY"]
        self.min_gap = 60.0 / rpm
        self.tpm = tpm
        self.last = 0.0
        self.window: deque[tuple[float, int]] = deque()
        self.requests = 0
        self.prompt_tokens = 0
        self.cached_tokens = 0
        self.completion_tokens = 0
        self.client = httpx.Client(timeout=120)

    def _wait(self) -> None:
        while True:
            now = time.time()
            while self.window and now - self.window[0][0] > 60:
                self.window.popleft()
            used = sum(tokens for _, tokens in self.window)
            gap = self.min_gap - (now - self.last)
            if gap > 0:
                time.sleep(gap)
                continue
            if used > self.tpm - 2500 and self.window:
                time.sleep(max(0.5, 60 - (now - self.window[0][0]) + 0.1))
                continue
            return

    def complete(self, messages, *, model, temperature=0.0, max_tokens=400) -> Completion:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_completion_tokens": 1500,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
        }
        delay = 5.0
        for attempt in range(8):
            self._wait()
            self.last = time.time()
            started = time.perf_counter()
            try:
                response = self.client.post(
                    BASE_URL + "/chat/completions", json=body, headers={"Authorization": f"Bearer {self.key}"}
                )
            except httpx.HTTPError as exc:
                print(f"network error {type(exc).__name__}, retrying", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if response.status_code == 429:
                text = response.text
                if re.search(r"per day|\(RPD\)|\(TPD\)", text):
                    raise DailyCap(text[:300])
                wait = float(response.headers.get("retry-after") or delay)
                print(f"429, sleeping {wait:.1f}s", flush=True)
                time.sleep(min(wait, 120))
                delay = min(delay * 2, 60)
                continue
            if response.status_code >= 500:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if response.status_code == 400 and "tool_use_failed" in response.text:
                # gpt-oss sometimes emits a built-in tool call. Hand the harness an
                # empty turn so its normal one-retry correction runs.
                self.requests += 1
                return Completion(text="", latency_s=time.perf_counter() - started)
            if response.status_code == 400 and ("json_validate_failed" in response.text or "output_parse_failed" in response.text):
                self.requests += 1
                return Completion(text="", latency_s=time.perf_counter() - started)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            self.requests += 1
            usage = payload.get("usage") or {}
            prompt = int(usage.get("prompt_tokens") or 0)
            cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            self.prompt_tokens += prompt
            self.cached_tokens += cached
            self.completion_tokens += completion
            self.window.append((time.time(), prompt - cached + completion))
            message = (payload.get("choices") or [{}])[0].get("message") or {}
            return Completion(
                text=str(message.get("content") or ""),
                latency_s=time.perf_counter() - started,
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
        raise RuntimeError("gave up after repeated retries")


def _done(path: Path, key) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if key(row):
                out.add(row["task_id"])
    return out


def _interleave(tasks: list[dict]) -> list[dict]:
    """Round-robin by task family so a short run still covers every skill."""
    groups: dict[str, list[dict]] = {}
    for task in tasks:
        groups.setdefault(task["id"].rsplit("-", 1)[0], []).append(task)
    out: list[dict] = []
    while any(groups.values()):
        for family in list(groups):
            if groups[family]:
                out.append(groups[family].pop(0))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, default=Path("data/teacher/teach_traces.jsonl"))
    parser.add_argument("--attempts", type=Path, default=Path("data/teacher/teach_attempts.jsonl"))
    parser.add_argument("--minutes", type=float, default=40)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    accepted_ids = _done(args.out, lambda row: True)
    attempted_ids = _done(args.attempts, lambda row: row["model"] == args.model)
    todo = [t for t in teach_tasks() if t["id"] not in accepted_ids and t["id"] not in attempted_ids]
    todo = _interleave(todo)
    if args.limit:
        todo = todo[: args.limit]
    backend = GroqBackend(args.model)
    started = time.time()
    attempted = accepted = 0
    stop = "done"
    for task in todo:
        if time.time() - started > args.minutes * 60:
            stop = "time box"
            break
        with tempfile.TemporaryDirectory(prefix="pocket-groq-") as temporary:
            home = Path(temporary)
            ctx = prepare_teach_home(home, task)
            trace_path = home / "trace.jsonl"
            try:
                result = run_agent(task["prompt"], backend, ctx, model=args.model, trace_path=trace_path)
            except DailyCap as exc:
                stop = f"daily cap: {exc}"
                break
            except RuntimeError as exc:
                print(f"{task['id']} error {exc}", flush=True)
                stop = f"error: {exc}"
                break
            errors = check_task(task, result)
            ok = not errors and trace_path.exists()
            attempted += 1
            with args.attempts.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"task_id": task["id"], "model": args.model, "accepted": ok, "errors": errors,
                                         "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens}) + "\n")
            if ok:
                accepted += 1
                rows = scrub_trace_file(trace_path, [task["profile"]["name"]])
                with args.out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"task_id": task["id"], "skill": task["skill"], "canary": CANARY,
                                             "model": args.model, "trace": rows}, ensure_ascii=False) + "\n")
            print(f"{task['id']} {'OK' if ok else 'FAIL ' + '; '.join(errors)[:160]}", flush=True)
    summary = {
        "model": args.model, "attempted": attempted, "accepted": accepted,
        "acceptance": round(accepted / attempted, 3) if attempted else None,
        "requests": backend.requests, "prompt_tokens": backend.prompt_tokens, "cached_tokens": backend.cached_tokens,
        "completion_tokens": backend.completion_tokens, "wall_s": round(time.time() - started, 1), "stop": stop[:400],
    }
    print("SUMMARY " + json.dumps(summary), flush=True)
    with args.out.with_suffix(".runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
