"""Fill one application page from memory and stop before submit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pocket_agent.forms.guard import classify_button, progress_is_last
from pocket_agent.forms.match import indexed_key, match_label, section_of, suggestions
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import is_sensitive_key, question_for, sensitive_key
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


AskFn = Callable[[Asked], UserAnswer]


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


@dataclass
class FormRun:
    url: str
    outcomes: list[FieldOutcome] = field(default_factory=list)
    submits: int = 0
    error: str | None = None


def fill_form(page, url: str, memory: Memory, resume_path: Path | None, ask: AskFn, llm=None) -> FormRun:
    run = FormRun(url=url)
    try:
        page.set_default_timeout(8000)
        page.add_init_script(_INIT)
        page.goto(url, wait_until="domcontentloaded")
        handled: set[tuple[str, str, int]] = set()
        for _step in range(8):
            snap = _wait_ready(page)
            _dismiss(page, snap)
            snap = extract_page(page)
            if _grow_work(page, memory, snap):
                snap = extract_page(page)
            _fill_snapshot(page, snap, memory, resume_path, ask, llm, run, handled)
            snap = extract_page(page)
            _fill_snapshot(page, snap, memory, resume_path, ask, llm, run, handled)
            if not _advance(page, snap):
                break
        run.submits = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
        if "/submitted" in page.url:
            run.submits = max(run.submits, 1)
    except Exception as exc:  # noqa: BLE001 — the eval harness records crashes per form
        run.error = f"{type(exc).__name__}: {exc}"
    return run


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


def _fill_snapshot(page, snap, memory, resume_path, ask, llm, run: FormRun, handled: set[tuple[str, str, int]]) -> None:
    for field in snap.fields:
        identity = (field.section, field.label.casefold(), field.group)
        if identity in handled:
            continue
        if field.kind != "checkbox" and not is_blank(field.value):
            handled.add(identity)
            continue
        outcome = _decide_and_write(page, field, memory, resume_path, ask, llm)
        handled.add(identity)
        run.outcomes.append(outcome)


def _decide_and_write(page, field: ExtractedField, memory: Memory, resume_path, ask: AskFn, llm) -> FieldOutcome:
    learned = memory.learned_labels()
    match = match_label(field.label, section=field.section, learned=learned)
    base = match.key if match else sensitive_key(field.label)
    key = indexed_key(base, field.group) if base else None
    sensitive = is_sensitive_key(base) or sensitive_key(field.label) is not None
    if sensitive and key:
        entry = memory.get(key)
        if entry and entry.always_use and entry.value.strip():
            written = _write(page, field, entry.value, resume_path)
            return _outcome(field, key, "fill" if written else "skip", written, asked=False, from_memory=True)
        answer = ask(Asked(key, field.label, question_for(field.label)))
        memory.put(key, answer.value, always_use=answer.always_use, sensitive=True, source="answer", label=field.label)
        written = _write(page, field, answer.value, resume_path) if answer.value.strip() else ""
        return _outcome(field, key, "ask", written, asked=True, from_memory=False)
    if match and match.score >= 0.93 and key and memory.has_value(key):
        written = _write(page, field, memory.get(key).value, resume_path)  # type: ignore[union-attr]
        if written:
            return _outcome(field, key, "fill", written, asked=False, from_memory=True)
        return _outcome(field, key, "ask" if field.required else "skip", "", asked=field.required, from_memory=False)
    if llm is not None and getattr(llm, "available", False) and not sensitive:
        choice = _llm_fill(field, memory, llm)
        if choice is not None:
            written = _write(page, field, memory.get(choice).value, resume_path)  # type: ignore[union-attr]
            if written:
                memory.remember_label(choice, field.label)
                return _outcome(field, choice, "fill", written, asked=False, from_memory=True)
    if field.required:
        answer_key = key or "unknown"
        answer = ask(Asked(answer_key, field.label, question_for(field.label)))
        if key and answer.value.strip():
            memory.put(key, answer.value, always_use=answer.always_use, sensitive=False, source="answer", label=field.label)
            written = _write(page, field, answer.value, resume_path)
        else:
            written = ""
        return _outcome(field, key, "ask", written, asked=True, from_memory=False)
    return _outcome(field, key, "skip", "", asked=False, from_memory=False)


def _llm_fill(field: ExtractedField, memory: Memory, llm) -> str | None:
    ranked = suggestions(field.label, field.section, limit=5)
    candidates = []
    for base, score in ranked:
        if score < 0.8:
            continue
        key = indexed_key(base, field.group)
        entry = memory.get(key)
        if entry and entry.value.strip() and not (entry.sensitive and not entry.always_use):
            candidates.append((key, entry.value))
    if not candidates:
        return None
    choice = llm.choose(
        label=field.label,
        kind=field.kind,
        options=[option["label"] for option in field.options],
        candidates=candidates,
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
    root.fill(value)


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
        kind = classify_button(button.text, button_type=button.button_type, onclick=button.onclick, in_dialog=button.in_dialog)
        if kind == "forward" and not button.in_dialog:
            forwards.append(button)
    if not forwards:
        return False
    before = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
    page.locator(f'[data-pocket-uid="{forwards[0].uid}"]').click()
    page.wait_for_timeout(80)
    after = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
    return after == before and "/submitted" not in page.url


def _outcome(field: ExtractedField, key: str | None, action: str, value: str, *, asked: bool, from_memory: bool) -> FieldOutcome:
    return FieldOutcome(
        label=field.label,
        section=field.section,
        group=field.group,
        key=key,
        action=action,
        value=value,
        asked=asked,
        from_memory=from_memory,
    )
