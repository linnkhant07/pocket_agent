"""Programmatic checks for agent traces. No model calls."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pocket_agent.harness.loop import AgentResult


def check_task(task: dict[str, Any], result: AgentResult) -> list[str]:
    expect = task.get("expect") or {}
    errors: list[str] = []
    answer = result.final or ""
    answer_l = answer.lower()
    called = [call["name"] for call in result.tools]
    called_with_ask = list(called)
    if result.ask_user:
        called_with_ask.append("ask_user")

    if tools_any := expect.get("tools_any"):
        if not (set(tools_any) & set(called_with_ask)):
            errors.append(f"expected one of {tools_any}, called {called_with_ask}")
    for name in expect.get("tools_all") or []:
        if name not in called_with_ask:
            errors.append(f"missing tool {name}")
    for name in expect.get("forbid_tools") or []:
        if name in called:
            errors.append(f"forbidden tool {name} was called")

    for needle in expect.get("answer_contains_all") or []:
        if needle.lower() not in answer_l:
            errors.append(f"answer missing {needle!r}")
    any_of = expect.get("answer_contains_any") or []
    if any_of and not any(needle.lower() in answer_l for needle in any_of):
        errors.append(f"answer missing all of {any_of}")
    for needle in expect.get("answer_excludes") or []:
        if needle.lower() in answer_l:
            errors.append(f"answer should not mention {needle!r}")

    if "ask_user" in expect:
        got = result.ask_user is not None
        if got != bool(expect["ask_user"]):
            errors.append(f"ask_user={got}, expected {expect['ask_user']}")
    if missing := expect.get("missing_any"):
        fields = (result.ask_user or {}).get("missing_fields") or []
        if not (set(missing) & set(fields)):
            errors.append(f"missing_fields {fields} did not include any of {missing}")

    jobs = list(_iter_jobs(result))
    companies = [str(job.get("company") or "") for job in jobs]
    if "min_results" in expect and len(jobs) < expect["min_results"]:
        errors.append(f"results {len(jobs)} < {expect['min_results']}")
    if "max_results" in expect and len(jobs) > expect["max_results"]:
        errors.append(f"results {len(jobs)} > {expect['max_results']}")
    for company in expect.get("result_companies_any") or []:
        if not any(company.lower() in item.lower() for item in companies):
            errors.append(f"results missing company {company}")
    for company in expect.get("result_companies_excludes") or []:
        if any(company.lower() in item.lower() for item in companies):
            errors.append(f"results included company {company}")

    fetches = [call["result"] for call in result.tools if call["name"] == "fetch_feed"]
    inserted = [int(item.get("inserted") or 0) for item in fetches]
    if "fetch_inserted" in expect and inserted != expect["fetch_inserted"]:
        errors.append(f"fetch inserted {inserted}, expected {expect['fetch_inserted']}")
    if "min_inserted" in expect and (not inserted or inserted[-1] < expect["min_inserted"]):
        errors.append(f"inserted {inserted} below {expect['min_inserted']}")

    scores = [call for call in result.tools if call["name"] == "score_fit" and call["result"].get("ok")]
    chosen = _pick_score(scores, expect.get("score_job"))
    if "score_eligible" in expect:
        if chosen is None:
            errors.append("no score_fit result")
        elif bool(chosen["eligible"]) != bool(expect["score_eligible"]):
            errors.append(f"eligible={chosen['eligible']} expected {expect['score_eligible']}")
    if chosen is not None and "score_min" in expect and chosen["score"] < expect["score_min"]:
        errors.append(f"score {chosen['score']} < {expect['score_min']}")
    if chosen is not None and "score_max" in expect and chosen["score"] > expect["score_max"]:
        errors.append(f"score {chosen['score']} > {expect['score_max']}")
    if needle := expect.get("blocker_contains"):
        blob = " ".join(chosen["blockers"]) if chosen else ""
        if needle.lower() not in blob.lower():
            errors.append(f"blockers {blob!r} missing {needle!r}")
    if order := expect.get("score_order"):
        by_id = {call["arguments"].get("job_id"): call["result"]["score"] for call in scores}
        values = [by_id.get(job_id) for job_id in order]
        if any(value is None for value in values) or any(values[i] <= values[i + 1] for i in range(len(values) - 1)):
            errors.append(f"score order {values} for {order}")

    upserts = [call["result"] for call in result.tools if call["name"] == "db_upsert"]
    if "application_status" in expect:
        status = None
        if upserts and upserts[-1].get("application"):
            status = upserts[-1]["application"].get("status")
        if status != expect["application_status"]:
            errors.append(f"status {status} != {expect['application_status']}")
    if "tool_ok" in expect:
        for name, ok in expect["tool_ok"].items():
            calls = [call for call in result.tools if call["name"] == name]
            if not calls:
                errors.append(f"tool_ok missing call {name}")
            elif bool(calls[-1]["result"].get("ok")) != bool(ok):
                errors.append(f"{name} ok={calls[-1]['result'].get('ok')} expected {ok}")

    queries = [call["result"] for call in result.tools if call["name"] == "db_query"]
    if "stats_applications" in expect:
        found = None
        for query in reversed(queries):
            if "applications" in query and isinstance(query.get("applications"), int):
                found = query["applications"]
                break
        if found != expect["stats_applications"]:
            errors.append(f"stats applications {found} != {expect['stats_applications']}")
    if sources := expect.get("result_sources_only"):
        allowed = set(sources)
        bad = [job.get("source") for job in jobs if job.get("source") not in allowed]
        if bad:
            errors.append(f"result sources {bad} not in {sources}")
    if title_needles := expect.get("result_title_excludes"):
        for job in jobs:
            title = str(job.get("title") or "").lower()
            for needle in title_needles:
                if needle.lower() in title:
                    errors.append(f"result title {job.get('title')!r} contains {needle!r}")
    if needles := expect.get("export_contains"):
        path = None
        for query in reversed(queries):
            if query.get("export_path"):
                path = Path(query["export_path"])
                break
        body = path.read_text(encoding="utf-8") if path and path.exists() else ""
        for needle in needles:
            if needle not in body:
                errors.append(f"csv missing {needle!r}")

    if expect.get("tool_arg_equals"):
        for name, expected_args in expect["tool_arg_equals"].items():
            calls = [call for call in result.tools if call["name"] == name]
            if not calls:
                errors.append(f"no {name} call to check args")
                continue
            actual = calls[0]["arguments"]
            for key, value in expected_args.items():
                if actual.get(key) != value:
                    errors.append(f"{name} arg {key}={actual.get(key)!r} expected {value!r}")

    if expect.get("must_not_claim_submitted") and _claims_submitted(answer):
        errors.append("answer claims an application was submitted")
    if expect.get("must_not_say_should_apply") and re.search(r"\byou should apply\b", answer, re.IGNORECASE):
        errors.append("answer tells the user to apply")
    return errors


def tool_accuracy(task: dict[str, Any], result: AgentResult) -> bool:
    return not any(
        message.startswith("expected one of")
        or message.startswith("missing tool")
        or message.startswith("forbidden tool")
        or message.startswith("no ")
        for message in check_task(task, result)
        if message.startswith("expected one of") or message.startswith("missing tool") or message.startswith("forbidden tool")
    )


def json_validity(result: AgentResult) -> float:
    if result.assistant_turns <= 0:
        return 0.0
    return result.valid_turns / result.assistant_turns


def _iter_jobs(result: AgentResult) -> Any:
    for call in result.tools:
        payload = call["result"]
        if not isinstance(payload, dict):
            continue
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            for job in jobs:
                yield job
        job = payload.get("job")
        if isinstance(job, dict):
            yield job


def _pick_score(scores: list[dict[str, Any]], job_id: str | None) -> dict[str, Any] | None:
    if job_id:
        for call in scores:
            if call["arguments"].get("job_id") == job_id:
                return call["result"]
        return None
    if not scores:
        return None
    return scores[-1]["result"]


def _claims_submitted(answer: str) -> bool:
    pattern = re.compile(
        r"\b(i submitted|i have submitted|i['’]ve submitted|i applied|i have applied|application was submitted|submitted your application)\b",
        re.IGNORECASE,
    )
    if not pattern.search(answer):
        return False
    for sentence in re.split(r"[.\n]", answer):
        if pattern.search(sentence) and not re.search(r"\b(not|never|won't|cannot|can't|didn't|did not)\b", sentence, re.IGNORECASE):
            return True
    return False
