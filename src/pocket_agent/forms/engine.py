"""Fill one application page from memory and stop before submit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pocket_agent.forms.guard import classify_button, progress_is_last
from pocket_agent.forms.match import indexed_key, match_label, section_of, suggestions
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import ask_key, is_sensitive_key, question_for
from pocket_agent.forms.reader import ExtractedField, PageSnapshot, extract_page
from pocket_agent.forms.values import date_variants, is_blank, present_value

_INIT = """
window.__pocketSubmits = 0;
document.addEventListener("submit", () => { window.__pocketSubmits += 1; }, true);
"""


@dataclass
class UserAnswer:
    value: str
    always_use: bool = False


@dataclass
class Asked:
    key: str
    label: str
    question: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    sensitive: bool = False
    section: str = ""


AskFn = Callable[[Asked], UserAnswer]
EventFn = Callable[[dict], None]


@dataclass
class FieldOutcome:
    label: str
    section: str
    group: int
    key: str | None
    action: str
    value: str
    asked: bool
    from_memory: bool
    via_model: bool = False


@dataclass
class FormRun:
    url: str
    outcomes: list[FieldOutcome] = field(default_factory=list)
    submits: int = 0
    error: str | None = None
    gate: str = ""
    model_calls: int = 0
    model_seconds: float = 0.0


def fill_form(
    page,
    url: str,
    memory: Memory,
    resume_path: Path | None,
    ask: AskFn,
    llm=None,
    *,
    events: EventFn | None = None,
    navigate: bool = True,
) -> FormRun:
    run = FormRun(url=url)
    try:
        page.set_default_timeout(8000)
        if navigate:
            page.add_init_script(_INIT)
            page.goto(url, wait_until="domcontentloaded")
        handled: set[tuple[str, str, int]] = set()
        heading = ""
        for step_index in range(48):
            snap = _wait_ready(page) if step_index == 0 else extract_page(page)
            if snap.gate:
                run.gate = snap.gate
                break
            _dismiss(page, snap)
            snap = extract_page(page)
            if snap.gate:
                run.gate = snap.gate
                break
            if _grow_work(page, memory, snap):
                continue
            field = _next_field(snap, handled)
            if field is not None:
                if field.heading and field.heading != heading:
                    heading = field.heading
                    _emit(events, {"type": "step", "heading": heading})
                outcome = _decide_and_write(page, field, memory, resume_path, ask, llm, run)
                handled.add((field.section, field.label.casefold(), field.group))
                run.outcomes.append(outcome)
                _emit(events, _activity_event(memory, outcome, field.heading))
                continue
            if not _advance(page, snap):
                break
        run.submits = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
        if "/submitted" in page.url:
            run.submits = max(run.submits, 1)
    except Exception as exc:  # noqa: BLE001 — the eval harness records crashes per form
        run.error = f"{type(exc).__name__}: {exc}"
    counts = review_counts(run.outcomes)
    if run.gate:
        _emit(events, {"type": "gate", "gate": run.gate, **counts, "submits": run.submits})
    elif run.error:
        _emit(events, {"type": "error", "message": run.error})
    else:
        _emit(events, {"type": "review", **counts, "submits": run.submits})
    return run


def review_counts(outcomes: list[FieldOutcome]) -> dict[str, int]:
    filled = sum(1 for item in outcomes if item.action == "fill" and item.value and not item.asked)
    asked = sum(1 for item in outcomes if item.asked)
    skipped = sum(1 for item in outcomes if item.action == "skip")
    return {"filled": filled, "asked": asked, "skipped": skipped}


def activity_source(memory: Memory, outcome: FieldOutcome) -> str:
    if outcome.via_model:
        return "model"
    if outcome.asked:
        return "answer"
    entry = memory.get(outcome.key) if outcome.key else None
    if entry is not None and entry.source == "resume":
        return "resume"
    return "memory"


def _emit(events: EventFn | None, payload: dict) -> None:
    if events is not None:
        events(payload)


def _activity_event(memory: Memory, outcome: FieldOutcome, heading: str) -> dict:
    return {
        "type": "activity",
        "label": outcome.label,
        "action": outcome.action,
        "value": outcome.value,
        "source": activity_source(memory, outcome),
        "heading": heading,
        "asked": outcome.asked,
    }


def _wait_ready(page) -> PageSnapshot:
    try:
        page.wait_for_function("() => !document.body.classList.contains('booting')", timeout=8000)
    except Exception:
        pass
    return extract_page(page)


def _dismiss(page, snap: PageSnapshot) -> None:
    for _ in range(3):
        targets = [
            button for button in snap.buttons
            if button.in_dialog and classify_button(button.text, button_type=button.button_type, onclick=button.onclick, in_dialog=True) == "dismiss"
        ]
        if not targets:
            return
        page.locator(f'[data-pocket-uid="{targets[0].uid}"]').click()
        page.wait_for_timeout(100)
        snap = extract_page(page)


def _grow_work(page, memory: Memory, snap: PageSnapshot) -> bool:
    needed = len(memory.indexed_values("work"))
    if needed <= 0:
        return False
    grew = False
    for _ in range(6):
        snap = extract_page(page)
        have = _work_groups(snap)
        if have >= needed:
            return grew
        button = _add_button(snap)
        if button is None:
            return grew
        before = have
        page.locator(f'[data-pocket-uid="{button.uid}"]').click()
        page.wait_for_timeout(50)
        grew = True
        snap = extract_page(page)
        if _work_groups(snap) <= before:
            return grew
    return grew


def _work_groups(snap: PageSnapshot) -> int:
    groups = {field.group for field in snap.fields if field.section == "work" and field.label}
    if not groups:
        return 0
    return max(groups) + 1


def _add_button(snap: PageSnapshot):
    for button in snap.buttons:
        kind = classify_button(button.text, button_type=button.button_type, onclick=button.onclick)
        if kind == "add" and section_of(button.heading) in {"", "work"}:
            return button
    return None


def _next_field(snap: PageSnapshot, handled: set[tuple[str, str, int]]) -> ExtractedField | None:
    pending = []
    for field in snap.fields:
        if (field.section, field.label.casefold(), field.group) in handled:
            continue
        pending.append(field)
    files = [field for field in pending if field.kind == "file"]
    return files[0] if files else (pending[0] if pending else None)


def _decide_and_write(page, field: ExtractedField, memory: Memory, resume_path, ask: AskFn, llm, run: FormRun) -> FieldOutcome:
    forced = ask_key(field.label, field.section)
    if forced:
        return _ask_or_saved(page, field, memory, resume_path, ask, forced)
    if field.kind == "checkbox" and not field.required and ask_key(field.label, field.section):
        return _outcome(field, None, "skip", "", asked=False, from_memory=False)
    learned = memory.learned_labels()
    match = match_label(field.label, section=field.section, learned=learned)
    base = match.key if match and match.score >= 0.93 and not is_sensitive_key(match.key) else None
    key = indexed_key(base, field.group) if base else None
    value = stored_value(memory, key, field.label) if key else ""
    ruled = bool(key and value.strip() and match and match.reason in {"exact", "tokens", "subset", "learned"})
    agreed = False
    if ruled and llm is not None and getattr(llm, "available", False) and match is not None and match.reason != "exact":
        import time
        started = time.perf_counter()
        agreed = _llm_fill(field, memory, llm) == key
        run.model_calls += 1
        run.model_seconds += time.perf_counter() - started
    if ruled:
        written = _write(page, field, value, resume_path)
        if written:
            return _outcome(field, key, "fill", written, asked=False, from_memory=True, via_model=agreed)
        if field.required:
            return _ask_or_saved(page, field, memory, resume_path, ask, key or "unknown")
    if field.required:
        return _ask_or_saved(page, field, memory, resume_path, ask, key or "unknown")
    if field.value and not is_blank(field.value):
        _clear_field(page, field)
    return _outcome(field, key, "skip", "", asked=False, from_memory=False)


def _ask_or_saved(page, field, memory, resume_path, ask, key: str) -> FieldOutcome:
    entry = memory.get(key)
    sensitive = is_sensitive_key(key) or ask_key(field.label, field.section) == key
    if entry and entry.always_use and entry.value.strip():
        written = _write(page, field, entry.value, resume_path)
        if written:
            return _outcome(field, key, "fill", written, asked=False, from_memory=True)
    answer = ask(Asked(
        key,
        field.label,
        question_for(field.label),
        kind=field.kind,
        options=tuple(option["label"] for option in field.options if option.get("label")),
        sensitive=sensitive,
        section=field.heading or field.section,
    ))
    memory.put(
        key,
        answer.value,
        always_use=answer.always_use,
        sensitive=sensitive,
        source="answer",
        label=field.label,
    )
    written = _write(page, field, answer.value, resume_path) if answer.value.strip() else ""
    if not written and field.value and not is_blank(field.value):
        _clear_field(page, field)
    return _outcome(field, key, "ask", written, asked=True, from_memory=False)


def stored_value(memory: Memory, key: str | None, label: str) -> str:
    if not key:
        return ""
    if key == "previously_employed_here":
        low = label.casefold()
        for mem_key in memory.keys():
            if not mem_key.endswith(".org"):
                continue
            entry = memory.get(mem_key)
            if entry and entry.value and entry.value.casefold() in low:
                return "Yes"
        return "No"
    entry = memory.get(key)
    return entry.value if entry and entry.value.strip() else ""


def _llm_fill(field: ExtractedField, memory: Memory, llm) -> str | None:
    ranked = suggestions(field.label, field.section, limit=8)
    candidates = []
    seen: set[str] = set()
    for base, score in ranked:
        if score < 0.2 or base in seen or is_sensitive_key(base):
            continue
        seen.add(base)
        key = indexed_key(base, field.group)
        value = stored_value(memory, key, field.label)
        if value.strip():
            candidates.append((key, value))
    if not candidates:
        return None
    choice = llm.choose(
        label=field.label,
        kind=field.kind,
        options=[option["label"] for option in field.options],
        candidates=candidates[:6],
    )
    if choice is None or choice.action != "fill":
        return None
    if choice.key not in {key for key, _value in candidates}:
        return None
    return choice.key


def _write(page, field: ExtractedField, raw: str, resume_path: Path | None) -> str:
    options = [option["label"] for option in field.options]
    if field.kind == "file":
        if resume_path is None:
            return ""
        page.locator(f'[data-pocket-uid="{field.uid}"]').set_input_files(str(resume_path))
        return "uploaded"
    target = present_value(raw, kind=field.kind, options=options, placeholder=field.placeholder, input_type=field.input_type)
    if field.kind == "date":
        variants = [target] + [item for item in date_variants(raw) if item != target]
    else:
        variants = [target]
    if not target and field.kind in {"select", "combobox", "radio"}:
        return ""
    written = ""
    for variant in variants:
        if not variant:
            continue
        _apply(page, field, variant)
        page.wait_for_timeout(30)
        if not _invalid(page, field.uid):
            written = _read_back(page, field)
            if written and set(written) <= set("•*"):
                written = variant
            break
    return written


def _apply(page, field: ExtractedField, value: str) -> None:
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    if field.kind == "checkbox":
        root.set_checked(value.casefold() in {"yes", "true"})
        return
    if field.kind == "radio":
        match = next((option for option in field.options if option["label"].casefold() == value.casefold()), None)
        if match and match["uid"]:
            page.locator(f'[data-pocket-uid="{match["uid"]}"]').check()
        return
    if field.kind == "select":
        root.select_option(label=value)
        return
    if field.kind == "combobox":
        root.click()
        match = next((option for option in field.options if option["label"].casefold() == value.casefold()), None)
        if match and match["uid"]:
            page.locator(f'[data-pocket-uid="{match["uid"]}"]').click()
        return
    if field.kind == "typeahead":
        root.click()
        root.fill(value[:12])
        page.wait_for_timeout(30)
        if field.list_id:
            page.evaluate(
                """(id) => {
                  const list = document.getElementById(id);
                  const input = document.querySelector('[aria-controls="' + id + '"]');
                  if (!list || !input) return;
                  const query = (input.value || "").toLowerCase();
                  list.hidden = false;
                  list.querySelectorAll("[role=option]").forEach((item) => {
                    item.hidden = query.length >= 2 && !(item.textContent || "").toLowerCase().includes(query);
                  });
                }""",
                field.list_id,
            )
            choice = page.locator(f"#{field.list_id} [role=option]").filter(has_text=value)
            if choice.count():
                choice.first.click()
                return
        root.fill(value)
        return
    root.fill(value)


def _clear_field(page, field: ExtractedField) -> None:
    if field.kind in {"file", "radio", "combobox", "typeahead"}:
        return
    locator = page.locator(f'[data-pocket-uid="{field.uid}"]')
    if field.kind == "checkbox":
        locator.set_checked(False)
        return
    locator.fill("")


def _read_back(page, field: ExtractedField) -> str:
    if field.kind == "radio":
        for option in field.options:
            if option["uid"] and page.locator(f'[data-pocket-uid="{option["uid"]}"]').is_checked():
                return option["label"]
        return ""
    if field.kind == "checkbox":
        return "Yes" if page.locator(f'[data-pocket-uid="{field.uid}"]').is_checked() else "No"
    if field.kind == "combobox":
        raw = page.locator(f'[data-pocket-uid="{field.uid}"]').inner_text().strip()
        return "" if raw.casefold() in {"select one", "select", "choose", "choose one"} else raw
    if field.kind == "select":
        return page.locator(f'[data-pocket-uid="{field.uid}"] option:checked').inner_text().strip()
    if field.kind == "file":
        count = page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate("el => el.files ? el.files.length : 0")
        return "uploaded" if count else ""
    return page.locator(f'[data-pocket-uid="{field.uid}"]').input_value()


def _invalid(page, uid: str) -> bool:
    return bool(page.evaluate(
        """(uid) => {
          const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
          if (!el) return false;
          if (el.getAttribute("aria-invalid") === "true") return true;
          const box = el.parentElement;
          return !!(box && box.querySelector("[role=alert]"));
        }""",
        uid,
    ))


def _advance(page, snap: PageSnapshot) -> bool:
    if progress_is_last(snap.progress):
        return False
    forwards = []
    for button in snap.buttons:
        kind = classify_button(
            button.text,
            button_type=button.button_type,
            onclick=button.onclick,
            in_dialog=button.in_dialog,
            heading=button.heading,
        )
        if kind == "forward" and not button.in_dialog:
            forwards.append(button)
    if not forwards:
        return False
    before = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
    page.locator(f'[data-pocket-uid="{forwards[0].uid}"]').click()
    page.wait_for_timeout(80)
    after = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
    return after == before and "/submitted" not in page.url


def _outcome(field: ExtractedField, key: str | None, action: str, value: str, *, asked: bool, from_memory: bool, via_model: bool = False) -> FieldOutcome:
    return FieldOutcome(
        label=field.label,
        section=field.section,
        group=field.group,
        key=key,
        action=action,
        value=value,
        asked=asked,
        from_memory=from_memory,
        via_model=via_model,
    )
