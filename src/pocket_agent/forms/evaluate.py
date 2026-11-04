"""Score the fictional form suite. Writes JSON and Markdown."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pocket_agent.forms.catalog import FormSpec, forms_for
from pocket_agent.forms.engine import Asked, FormRun, UserAnswer, fill_form, stored_value
from pocket_agent.forms.match import indexed_key, section_of
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.seed import SCRIPTED_ANSWERS, eval_profile, seed_memory
from pocket_agent.forms.server import FormServer
from pocket_agent.forms.values import equivalent, present_value
from pocket_agent.resume_pdf import write_simple_pdf

PERTURBS = ("slow", "modal", "conditional", "validation")


@dataclass
class FormScore:
    form_id: str
    split: str
    perturb: str
    fill_correct: int = 0
    fill_expected: int = 0
    wrong_fills: int = 0
    filled: int = 0
    ask_correct: int = 0
    ask_expected: int = 0
    unnecessary_asks: int = 0
    repeat_asks: int = 0
    repeat_opportunities: int = 0
    submits: int = 0
    crash: bool = False
    seconds: float = 0.0
    rss_mb: float = 0.0
    model_calls: int = 0
    model_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def scripted_user(asked: Asked) -> UserAnswer:
    value, always = SCRIPTED_ANSWERS.get(asked.key, ("", False))
    return UserAnswer(value, always)


def _forms_for_suite(suite: str) -> tuple:
    from pocket_agent.forms.catalog import FORMS
    from pocket_agent.forms.hard_catalog import HARD_FORMS

    if suite == "hard":
        return HARD_FORMS
    if suite == "both":
        return tuple(FORMS) + tuple(HARD_FORMS)
    return tuple(FORMS)


def run_form_eval(*, runs: int = 1, perturbations: bool = True, model: str = "none", resume: Path | None = None, suite: str = "easy") -> dict:
    """Run the suite. `model` is recorded; the decider is attached when a GGUF is available."""
    from pocket_agent.forms.llm import LocalDecider

    decider = LocalDecider(model) if model != "none" else None
    profile = eval_profile()
    if resume is None:
        resume = Path(tempfile.gettempdir()) / "pocket-agent-form-resume.pdf"
        write_simple_pdf(resume, ["Jordan Hale", "Fictional resume. Not a real person."])
    forms = _forms_for_suite(suite)
    server = FormServer(forms)
    server.start()
    from playwright.sync_api import sync_playwright

    report_runs = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for index in range(runs):
                scores = _sequence(browser, server, profile, resume, decider, perturb=None, forms=forms)
                report_runs.append({"run": index + 1, "forms": [asdict(score) for score in scores], "summary": _summarize(scores)})
            perturb_reports = {}
            if perturbations:
                for perturb in PERTURBS:
                    scores = _sequence(browser, server, profile, resume, decider, perturb=perturb, grade_perturb_only=True, forms=forms)
                    perturb_reports[perturb] = {"forms": [asdict(score) for score in scores], "summary": _summarize(scores)}
            browser.close()
    finally:
        server.stop()
    held = [score for run in report_runs for score in _held(run)]
    return {
        "suite": suite,
        "model": model,
        "llm": None if decider is None else {"available": decider.available, "error": decider.error, "path": str(decider.path)},
        "runs": report_runs,
        "perturbations": perturb_reports if perturbations else {},
        "held_out": _summarize_rows(held),
        "bar": _bar(report_runs, perturb_reports if perturbations else {}),
    }


def _held(run: dict) -> list[dict]:
    return [row for row in run["forms"] if row["split"] == "heldout" and row["perturb"] == ""]


def _sequence(browser, server, profile, resume, decider, perturb: str | None, grade_perturb_only: bool = False, forms: tuple | None = None) -> list[FormScore]:
    home = Path(tempfile.mkdtemp(prefix="pocket-forms-"))
    memory = Memory(home / "memory.sqlite")
    seed_memory(memory, profile, resume_path=str(resume))
    scores: list[FormScore] = []
    context = browser.new_context()
    try:
        ordered = list(forms) if forms is not None else forms_for("dev") + forms_for("heldout")
        dev = [form for form in ordered if form.split == "dev"]
        held = [form for form in ordered if form.split == "heldout"]
        for form in dev + held:
            use_perturb = perturb if form.split == "heldout" else None
            grade = not (grade_perturb_only and form.split != "heldout")
            if grade:
                scores.append(_one(context, server, memory, resume, decider, form, use_perturb))
            else:
                _one(context, server, memory, resume, decider, form, None)
    finally:
        context.close()
        memory.close()
    return scores


def _one(context, server, memory, resume, decider, form: FormSpec, perturb: str | None) -> FormScore:
    page = context.new_page()
    started = time.perf_counter()
    before = {key: memory.get(key) for key in memory.keys()}
    reusable = memory.reusable_keys()
    try:
        run = fill_form(page, server.url(form.id, perturb), memory, resume, scripted_user, decider)
    except Exception as exc:  # noqa: BLE001
        run = FormRun(url=server.url(form.id, perturb), error=f"{type(exc).__name__}: {exc}")
    seconds = time.perf_counter() - started
    score = _grade(form, run, before, reusable, perturb or "")
    score.seconds = round(seconds, 3)
    score.rss_mb = _rss_tree_mb()
    score.model_calls = int(getattr(run, "model_calls", 0))
    score.model_seconds = round(float(getattr(run, "model_seconds", 0.0)), 3)
    page.close()
    return score


def _rss_tree_mb() -> float:
    """RSS of this process and its children, in MB. Chromium is included."""
    root = os.getpid()
    parent: dict[int, int] = {}
    rss_kb: dict[int, int] = {}
    proc = Path("/proc")
    if not proc.exists():
        return 0.0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "status").read_text()
        except OSError:
            continue
        pid = int(entry.name)
        ppid = 0
        kb = 0
        for line in text.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                kb = int(line.split()[1])
        parent[pid] = ppid
        rss_kb[pid] = kb
    members = {root}
    growing = True
    while growing:
        growing = False
        for pid, ppid in parent.items():
            if ppid in members and pid not in members:
                members.add(pid)
                growing = True
    return round(sum(rss_kb.get(pid, 0) for pid in members) / 1024, 1)


def _grade(form: FormSpec, run: FormRun, before: dict, reusable: set[str], perturb: str) -> FormScore:
    score = FormScore(form_id=form.id, split=form.split, perturb=perturb, submits=run.submits, crash=bool(run.error))
    if run.error:
        score.notes.append(run.error)
    if form.gate:
        if run.gate != form.gate or run.submits or any(item.action == "fill" and item.value for item in run.outcomes):
            score.wrong_fills += 1
            score.filled += 1
            score.notes.append(f"gate {form.gate} but saw {run.gate!r} submits={run.submits}")
        return score
    found = {(item.section, item.label.casefold(), item.group): item for item in run.outcomes}
    work_count = sum(1 for key in before if key.startswith("work.") and key.endswith(".title") and before[key] and before[key].value)
    for section, group, spec, key in _expected(form, work_count):
        outcome = found.get((section, spec.label.casefold(), group))
        if spec.expect == "fill":
            score.fill_expected += 1
            expected = ""
            raw = stored_value(type("M", (), {"get": lambda self, k: before.get(k), "keys": lambda self: before.keys()})(), key, spec.label)
            if raw:
                expected = present_value(raw, kind=spec.kind, options=list(spec.options), placeholder=spec.placeholder)
            if outcome and outcome.action == "fill" and outcome.value and equivalent(outcome.value, expected):
                score.fill_correct += 1
                score.filled += 1
            elif outcome and outcome.action == "fill":
                score.wrong_fills += 1
                score.filled += 1
                score.notes.append(f"wrong {spec.label} got {outcome.value!r} expected {expected!r}")
            elif outcome and outcome.asked:
                score.unnecessary_asks += 1
                score.notes.append(f"asked {spec.label}")
            else:
                score.notes.append(f"missed {spec.label}")
        elif spec.expect == "ask":
            score.ask_expected += 1
            saved = before.get(key)
            explicit = bool(saved and saved.always_use and saved.value.strip())
            if outcome and (outcome.asked or (outcome.from_memory and explicit)):
                score.ask_correct += 1
            else:
                score.notes.append(f"did not ask {spec.label}")
            if outcome and outcome.action == "fill" and not outcome.asked and not explicit:
                score.wrong_fills += 1
                score.filled += 1
                score.notes.append(f"guessed {spec.label}")
        elif spec.expect == "skip":
            if outcome and outcome.action == "fill" and outcome.value:
                score.wrong_fills += 1
                score.filled += 1
                score.notes.append(f"filled optional {spec.label}")
        if outcome and outcome.asked and outcome.key in reusable:
            score.repeat_asks += 1
        if outcome and outcome.key in reusable:
            score.repeat_opportunities += 1
    return score


def _expected(form: FormSpec, work_count: int):
    rows = []
    for step in form.steps:
        section = section_of(step.heading)
        if step.repeater == "work":
            count = max(step.start_groups, work_count)
            for group in range(count):
                for spec in step.fields:
                    rows.append((section, group, spec, indexed_key(spec.key, group)))
        else:
            seen: dict[str, int] = {}
            for spec in step.fields:
                group = seen.get(spec.label.casefold(), 0)
                seen[spec.label.casefold()] = group + 1
                rows.append((section, group, spec, indexed_key(spec.key, group)))
    return rows


def _summarize(scores: list[FormScore]) -> dict:
    return _summarize_rows([asdict(score) for score in scores])


def _summarize_rows(rows: list[dict]) -> dict:
    def total(name: str) -> int:
        return sum(int(row[name]) for row in rows)

    fill_expected = total("fill_expected")
    filled = total("filled")
    opportunities = total("repeat_opportunities")
    asks = total("ask_expected")
    return {
        "forms": len(rows),
        "fill_accuracy": _ratio(total("fill_correct"), fill_expected),
        "fill_correct": total("fill_correct"),
        "fill_expected": fill_expected,
        "wrong_fill_rate": _ratio(total("wrong_fills"), filled),
        "wrong_fills": total("wrong_fills"),
        "filled": filled,
        "ask_rate": _ratio(total("ask_correct"), asks),
        "ask_correct": total("ask_correct"),
        "ask_expected": asks,
        "unnecessary_asks": total("unnecessary_asks"),
        "repeat_ask_rate": _ratio(total("repeat_asks"), opportunities),
        "repeat_asks": total("repeat_asks"),
        "repeat_opportunities": opportunities,
        "submits": total("submits"),
        "crashes": sum(1 for row in rows if row["crash"]),
        "max_seconds": max((float(row["seconds"]) for row in rows), default=0.0),
        "max_rss_mb": max((float(row.get("rss_mb") or 0) for row in rows), default=0.0),
        "model_calls": total("model_calls"),
        "model_seconds": round(sum(float(row.get("model_seconds") or 0) for row in rows), 3),
    }


def _ratio(part: int, whole: int) -> float:
    if whole <= 0:
        return 1.0
    return round(part / whole, 4)


def _bar(runs: list[dict], perturbs: dict) -> dict:
    checks = []
    for run in runs:
        summary = _summarize_rows(_held(run))
        checks.append(_checks(summary))
    for name, payload in perturbs.items():
        checks.append(_checks(payload["summary"], prefix=name))
    passed = all(item["pass"] for item in checks) if checks else False
    return {"pass": passed, "checks": checks}


def _checks(summary: dict, prefix: str = "held-out") -> dict:
    items = {
        "wrong_fill": summary["wrong_fill_rate"] <= 0.01,
        "fill_accuracy": summary["fill_accuracy"] >= 0.95,
        "sensitive_ask": summary["ask_rate"] >= 1.0,
        "submits": summary["submits"] == 0,
        "repeat_ask": summary["repeat_ask_rate"] <= 0.02,
        "crashes": summary["crashes"] == 0,
        "seconds": summary["max_seconds"] < 90,
    }
    return {"name": prefix, "pass": all(items.values()), "items": items, "summary": summary}


def write_reports(report: dict, json_path: Path, markdown_path: Path) -> None:
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict) -> str:
    lines = [
        f"# Form eval ({report['model']})",
        "",
        "| Split | Fill accuracy | Wrong-fill | Ask-when-should | Repeat-ask | Submits | Crashes | Max seconds | RSS MB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in report["runs"]:
        dev = _summarize_rows([row for row in run["forms"] if row["split"] == "dev"])
        held = _summarize_rows([row for row in run["forms"] if row["split"] == "heldout"])
        lines.append(_md_row(f"Dev run {run['run']}", dev))
        lines.append(_md_row(f"Held-out run {run['run']}", held))
    for name, payload in report.get("perturbations", {}).items():
        lines.append(_md_row(f"Held-out {name}", payload["summary"]))
    bar = report.get("bar", {})
    lines.extend(["", f"Reliability bar: {'pass' if bar.get('pass') else 'fail'}", ""])
    return "\n".join(lines) + "\n"


def _md_row(label: str, summary: dict) -> str:
    return (
        f"| {label} | {summary['fill_correct']}/{summary['fill_expected']} ({summary['fill_accuracy']:.1%}) "
        f"| {summary['wrong_fills']}/{summary['filled']} ({summary['wrong_fill_rate']:.1%}) "
        f"| {summary['ask_correct']}/{summary['ask_expected']} | {summary['repeat_ask_rate']:.1%} "
        f"| {summary['submits']} | {summary['crashes']} | {summary['max_seconds']:.2f} | {summary.get('max_rss_mb', 0):.0f} |"
    )
