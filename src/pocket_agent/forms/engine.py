"""Fill one application page from memory and stop before submit."""

from __future__ import annotations

import contextvars
import re
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from pocket_agent.forms.guard import classify_button, progress_is_last
from pocket_agent.forms.match import indexed_key, match_label, phrase, section_of, suggestions
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import ask_key, is_sensitive_key, question_for
from pocket_agent.forms.reader import ExtractedField, PageSnapshot, extract_page
from pocket_agent.forms.values import closest_option, date_part, date_variants, equivalent, is_blank, present_value, resolve_option

# form.submit() does not fire a submit event, and requestSubmit can run from a listener the
# guard never sees. Count both, and block them unless this is an account or wizard step.
_GUARD_JS = r"""
(() => {
if (window.__pocketGuard) return;
window.__pocketGuard = true;
window.__pocketSubmits = window.__pocketSubmits || 0;
const pocketCount = (form) => {
  if (form && form.dataset && form.dataset.pocketAccount === 'true') return false;
  window.__pocketSubmits += 1;
  return !(form && form.dataset && form.dataset.pocketWizard === 'true');
};
document.addEventListener('submit', (event) => {
  if (pocketCount(event.target)) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);
const origSubmit = HTMLFormElement.prototype.submit;
HTMLFormElement.prototype.submit = function () {
  if (this.dataset && (this.dataset.pocketAccount === 'true' || this.dataset.pocketWizard === 'true')) {
    return origSubmit.apply(this, arguments);
  }
  window.__pocketSubmits += 1;
  return undefined;
};
const origRequest = HTMLFormElement.prototype.requestSubmit;
if (origRequest) {
  HTMLFormElement.prototype.requestSubmit = function () {
    if (this.dataset && (this.dataset.pocketAccount === 'true' || this.dataset.pocketWizard === 'true')) {
      return origRequest.apply(this, arguments);
    }
    window.__pocketSubmits += 1;
    return undefined;
  };
}
})();
"""

_INIT = _GUARD_JS

_RESUME_INIT = "() => {\n" + _GUARD_JS + "\n}"

_MAX_PAGES = 24
_write_key: contextvars.ContextVar[str | None] = contextvars.ContextVar("pocket_write_key", default=None)
_GENERIC_LABEL = re.compile(
    r"^(please explain|if yes,? please explain|other|comments|additional comments|explain|please describe)$",
    re.I,
)


@dataclass
class UserAnswer:
    value: str
    always_use: bool = False
    skip: bool = False
    forget: bool = False  # Sensitive answers are remembered unless the user opts out.


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
    because: str = ""  # for a status answer: the stated facts it follows from


@dataclass
class FormRun:
    url: str
    outcomes: list[FieldOutcome] = field(default_factory=list)
    submits: int = 0
    error: str | None = None
    gate: str = ""
    model_calls: int = 0
    model_seconds: float = 0.0
    current_step: str = ""
    status: str = "stopped"
    ai_answers: list[dict] = field(default_factory=list)
    ai_context: str | None = None
    unresolved: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    answerer: object = None  # status.StatusAnswerer for this fill


def fill_form(
    page,
    url: str,
    memory: Memory,
    resume_path: Path | None,
    ask: AskFn,
    llm=None,
    *,
    resume: bool = False,
    events: EventFn | None = None,
    navigate: bool | None = None,
    preserve_existing: bool = False,
    stop: Callable[[], bool] | None = None,
) -> FormRun:
    """resume=True fills the page already open (after the user signed in by hand) instead of loading url."""
    if navigate is not None:
        resume = not navigate
    run = FormRun(url=url)
    from pocket_agent.forms.status import StatusAnswerer

    run.answerer = StatusAnswerer(
        memory,
        reader=getattr(llm, "status_reader", None),
        store_path=Path(memory.path).parent / "readings.sqlite",
    )
    stalled = False
    try:
        page.set_default_timeout(8000)
        if not resume:
            page.add_init_script(_INIT)
            page.goto(url, wait_until="domcontentloaded")
        else:
            # The init script already counts submits on pages loaded since the first run; cover a page that predates it.
            page.evaluate(_RESUME_INIT)
        handled: set[tuple[str, str, int]] = set()
        asks: dict[tuple[str, str, int], int] = {}
        heading = ""
        for page_index in range(_MAX_PAGES):
            if stop and stop():
                run.error = "Fill cancelled."
                break
            snap = _wait_ready(page) if page_index == 0 else extract_page(page)
            if snap.gate:
                run.gate = snap.gate
                break
            _dismiss(page, snap)
            snap = extract_page(page)
            if snap.gate:
                run.gate = snap.gate
                break
            if _workday_resume_step(page):
                try:
                    _upload_workday_resume(page, memory, resume_path)
                    run.outcomes.append(FieldOutcome("Resume", "", 0, "resume", "fill", "uploaded", False, True))
                    if not _advance(page, extract_page(page)):
                        run.error = "Resume uploaded, but Workday did not advance from the upload step."
                        break
                    _wait_ready(page)
                except Exception as exc:
                    run.error = f"Workday resume upload stopped ({type(exc).__name__}); no next-page click was sent."
                    break
                continue
            if _grow_work(page, memory, snap):
                continue
            # Fields on one page share a budget scaled to the field count. The page budget is advances.
            cap = max(12, len(snap.fields) * 3 + 4)
            field_steps = 0
            while field_steps < cap:
                if stop and stop():
                    run.error = "Fill cancelled."
                    break
                snap = extract_page(page)
                field = _next_field(snap, handled)
                if field is None:
                    break
                field_steps += 1
                if field.heading and field.heading != heading:
                    heading = field.heading
                    _emit(events, {"type": "step", "heading": heading})
                try:
                    if preserve_existing and not _missing(field) and not _invalid(page, field.uid):
                        outcome = _outcome(field, None, "keep", field.value, asked=False, from_memory=False)
                    else:
                        outcome = _decide_and_write(page, field, memory, resume_path, ask, llm, run, snapshot=snap)
                except Exception as exc:  # noqa: BLE001 — one stuck widget should not end the whole page
                    outcome = _outcome(field, None, "error", type(exc).__name__, asked=False, from_memory=False)
                ident = (field.section, field.label.casefold(), field.group)
                # An invalid or empty answer is asked again. Three tries, then we stop looping.
                if _invalid(page, field.uid) and asks.get(ident, 0) < 3:
                    asks[ident] = asks.get(ident, 0) + 1
                    try:
                        _clear_field(page, field)
                    except Exception:
                        pass
                elif outcome.asked and not (outcome.value or "").strip() and asks.get(ident, 0) < 3:
                    asks[ident] = asks.get(ident, 0) + 1
                else:
                    handled.add(ident)
                run.outcomes.append(outcome)
                _emit(events, _activity_event(memory, outcome, field.heading))
                if field.kind == "file" and outcome.action == "error":
                    run.error = "Resume upload did not complete; stopped before advancing."
                    break
            if run.error:
                break
            if _next_field(extract_page(page), handled) is not None:
                run.error = "Stopped after reaching the action limit; application is incomplete."
                break
            advanced = _advance(page, extract_page(page))
            if not advanced.moved:
                from pocket_agent.forms.workday import account_scope
                snap = extract_page(page)
                if account_scope(page.url) and snap.progress and not progress_is_last(snap.progress) and not re.search(r"\breview\b", snap.progress, re.I):
                    missing = [item.label for item in snap.fields if item.required and _missing(item)]
                    run.error = "Workday did not advance from the current step" + (": " + ", ".join(missing) if missing else "; check validation or navigation errors")
                elif advanced.stalled:
                    stalled = True
                break
            # A page that did not change keeps handled answers. Only a real advance starts over.
            handled.clear()
            asks.clear()
            from pocket_agent.forms.workday import account_scope
            if account_scope(page.url):
                _wait_ready(page)
        else:
            run.error = "Stopped after reaching the action limit; application is incomplete."
        final_snapshot = extract_page(page)
        run.current_step = final_snapshot.progress
        run.unresolved = [item.label for item in final_snapshot.fields if item.required and _missing(item)]
        run.validation_errors = page.locator('input:invalid, select:invalid, textarea:invalid, [aria-invalid=true]').evaluate_all(
            "els => els.filter(el => el.getClientRects().length).map(el => el.validationMessage || el.getAttribute('aria-label') || 'Invalid field')"
        )
        run.submits = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
        if "/submitted" in page.url:
            run.submits = max(run.submits, 1)
        if run.submits:
            run.error = run.error or "A submit was attempted while filling. Stopped. Nothing was left submitted for you."
        review_visible = bool(re.search(r"\breview\b", final_snapshot.progress or "", re.I)) or _ready_for_review(final_snapshot)
        blocked = bool(run.gate or run.error or run.unresolved or run.validation_errors or stalled or run.submits)
        run.status = "blocked" if blocked else "review" if review_visible else "stopped"
    except Exception as exc:  # noqa: BLE001 — the eval harness records crashes per form
        run.error = f"{type(exc).__name__}: {exc}"
    counts = review_counts(run.outcomes)
    if run.gate:
        _emit(events, {"type": "gate", "gate": run.gate, **counts, "submits": run.submits})
    elif run.error:
        _emit(events, {"type": "error", "message": run.error})
    elif run.status == "review":
        _emit(events, {"type": "review", **counts, "submits": run.submits})
    if run.answerer is not None:
        run.answerer.close()
    return run


def _missing(field: ExtractedField) -> bool:
    """A required unchecked checkbox reads as 'No', which is still blank."""
    if field.kind in {"checkbox", "multicheck"}:
        return field.value.strip().casefold() not in {"yes"} and not (
            field.kind == "multicheck" and field.value.strip()
        )
    return is_blank(field.value)


def _ready_for_review(snap: PageSnapshot) -> bool:
    """A page whose only way forward is Submit, with nothing left blank, is ready for the user."""
    if any(item.required and _missing(item) for item in snap.fields):
        return False
    kinds = [
        classify_button(button.text, button_type=button.button_type, onclick=button.onclick, heading=button.heading)
        for button in snap.buttons
    ]
    return "submit" in kinds and "forward" not in kinds and not snap.progress


def review_counts(outcomes: list[FieldOutcome]) -> dict[str, int]:
    filled = sum(1 for item in outcomes if item.action == "fill" and item.value and not item.asked)
    asked = sum(1 for item in outcomes if item.asked)
    skipped = sum(1 for item in outcomes if item.action == "skip")
    return {"filled": filled, "asked": asked, "skipped": skipped}


def activity_source(memory: Memory, outcome: FieldOutcome) -> str:
    if outcome.because:
        return outcome.because
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


def _wait_ready(page, settle_ms: int = 12000) -> PageSnapshot:
    try:
        page.wait_for_function("() => !document.body.classList.contains('booting')", timeout=8000)
    except Exception:
        pass
    # Client-rendered boards (Workday) show an empty shell at domcontentloaded. Wait for a question, a gate,
    # or a button the loop can use (a step may legitimately start with only "Add another" / "Next").
    snap = extract_page(page)
    waited = 0
    while not _ready(snap) and waited < settle_ms:
        page.wait_for_timeout(250)
        waited += 250
        snap = extract_page(page)
    # Eightfold renders the form in chunks: the first fields appear a second or two before the rest.
    steady = 0
    while snap.fields and not snap.gate and steady < 3 and waited < settle_ms:
        page.wait_for_timeout(250)
        waited += 250
        count = len(snap.fields)
        snap = extract_page(page)
        steady = steady + 1 if len(snap.fields) == count else 0
    return snap


def _ready(snap: PageSnapshot) -> bool:
    if progress_is_last(snap.progress) or re.search(r"\breview\b", snap.progress, re.I):
        return True
    if snap.fields or snap.gate:
        return True
    return any(classify_button(button.text, button_type=button.button_type, onclick=button.onclick) in {"forward", "add"} for button in snap.buttons)


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
        button = _add_button(snap, have)
        if button is None:
            return grew
        before = have
        page.locator(f'[data-pocket-uid="{button.uid}"]').click()
        page.wait_for_timeout(50)
        snap = extract_page(page)
        if _work_groups(snap) <= before:
            return grew
        grew = True
    return grew


def _work_groups(snap: PageSnapshot) -> int:
    groups = {field.group for field in snap.fields if field.section == "work" and field.label}
    if not groups:
        return 0
    return max(groups) + 1


def _add_button(snap: PageSnapshot, have: int = 0):
    for button in snap.buttons:
        kind = classify_button(button.text, button_type=button.button_type, onclick=button.onclick)
        section = section_of(button.heading)
        # An unlabeled "Add another" only counts as work history once the page already shows a work block.
        if kind == "add" and (section == "work" or (section == "" and have > 0)):
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


def _name_confirmation(page, field: ExtractedField, memory: Memory, snapshot: PageSnapshot | None) -> str | None:
    """Confirm only an entered name matching the profile, not a general attestation."""
    label = field.label.casefold()
    if not re.match(r"^have you (?:added|entered|provided) your full legal name\b", label):
        return None
    if ask_key(field.label, field.section):
        return None
    expected = memory.get("full_name")
    if not expected or not expected.value.strip() or snapshot is None:
        return None
    normalize = lambda value: " ".join(value.split()).casefold()
    names: dict[str, list[str]] = {}
    for candidate in snapshot.fields:
        if candidate.uid == field.uid or candidate.kind != "text":
            continue
        matched = match_label(candidate.label, section=candidate.section)
        if matched and matched.key in {"full_name", "first_name", "last_name"}:
            names.setdefault(matched.key, []).append(_read_back(page, candidate))
    target = normalize(expected.value)
    if len(names.get("full_name", [])) == 1 and normalize(names["full_name"][0]) == target:
        return "Yes"
    if len(names.get("first_name", [])) == len(names.get("last_name", [])) == 1:
        if normalize(names["first_name"][0] + " " + names["last_name"][0]) == target:
            return "Yes"
    return None


def _option_labels(field: ExtractedField) -> list[str]:
    options = getattr(field, "options", None) or []
    return [option["label"] for option in options if isinstance(option, dict) and option.get("label")]


def _may_reuse(entry, key: str | None, field: ExtractedField, memory: Memory) -> bool:
    """Profile and resume facts fill directly. A typed answer is reused with consent, or for the same wording."""
    if entry is None or not str(entry.value).strip():
        return False
    if entry.always_use or entry.source != "answer":
        return True
    if entry.sensitive or is_sensitive_key(key) or _generic_label(field.label):
        return False
    # The same canonical key is the same fact. Generic prompts are excluded above, so they stay per page.
    return True


def _decide_and_write(page, field: ExtractedField, memory: Memory, resume_path, ask: AskFn, llm, run: FormRun, *, snapshot: PageSnapshot | None = None) -> FieldOutcome:
    labels = _option_labels(field)
    forced = ask_key(field.label, field.section, labels)
    if field.kind == "file" and forced == "cover_letter":
        # The only file we have is the resume. It does not go in a cover letter slot.
        return _outcome(field, forced, "skip", "", asked=False, from_memory=False)
    if field.kind == "file" and resume_path is not None and not resume_names_user(Path(resume_path), memory):
        # Boards parse the upload and overwrite name, email, and phone with what it says. Never send someone else's resume.
        return _outcome(field, "resume", "skip", "", asked=False, from_memory=False)
    status = _status_outcome(page, field, memory, resume_path, ask, run, snapshot)
    if status is not None:
        return status
    if forced:
        entry = memory.get(forced)
        # Demographic surveys are offered to the user even when voluntary.
        # Other optional preferences still require an explicit reusable answer.
        demographic = forced in {"gender", "hispanic", "race", "veteran_status", "disability_status", "pronouns"}
        if not field.required and not demographic and not (entry and entry.always_use and entry.value.strip()):
            return _outcome(field, forced, "skip", field.value, asked=False, from_memory=False)
        if not (entry and entry.always_use and entry.value.strip()) and forced in {"essay", "cover_letter", "salary_expectation"}:
            generated = _generate_and_write(page, field, resume_path, llm, run)
            if generated:
                return generated
        return _ask_or_saved(page, field, memory, resume_path, ask, forced)
    confirmed = _name_confirmation(page, field, memory, snapshot)
    if confirmed:
        written = _write(page, field, confirmed, resume_path)
        if written:
            return _outcome(field, "full_name_confirmation", "fill", written, asked=False, from_memory=True)
    # A generic "Explain…" prompt is keyed by the full label plus the host and path.
    # Its learned wording must not carry an answer onto another site.
    learned = {} if _generic_label(field.label) else memory.learned_labels()
    match = match_label(field.label, section=field.section, learned=learned, options=labels)
    base = match.key if match and match.score >= 0.93 and not is_sensitive_key(match.key) else None
    key = indexed_key(base, field.group) if base else None
    entry = memory.get(key) if key else None
    # "Have you worked here?" is No unless the question names an employer we have on file.
    # That answer is computed from work history, so it does not need a stored entry.
    if key == "previously_employed_here":
        value = stored_value(memory, key, field.label)
    else:
        value = stored_value(memory, key, field.label) if key and _may_reuse(entry, key, field, memory) else ""
    if _generic_label(field.label) and not (entry and entry.always_use):
        value = ""
    ruled = bool(key and value.strip() and match and match.reason in {"exact", "tokens", "subset", "learned"})
    if ruled:
        written = _write_for(page, field, value, resume_path, key)
        if written and not (page is not None and _invalid(page, getattr(field, "uid", ""))):
            return _outcome(field, key, "fill", written, asked=False, from_memory=True)
        if written:
            try:
                _clear_field(page, field)
            except Exception:
                pass
        if field.required:
            return _ask_or_saved(page, field, memory, resume_path, ask, key or _label_key(field, page.url))
    mapped = _map_with_model(page, field, memory, resume_path, llm, run)
    if mapped is not None:
        return mapped
    if field.required and page is not None and _invalid(page, getattr(field, "uid", "")):
        return _ask_or_saved(page, field, memory, resume_path, ask, key or _label_key(field, page.url))
    if field.required and not _missing(field):
        return _outcome(field, key, "keep", field.value, asked=False, from_memory=False)
    if field.required:
        generated = _generate_and_write(page, field, resume_path, llm, run)
        if generated:
            return generated
        return _ask_or_saved(page, field, memory, resume_path, ask, key or _label_key(field, page.url))
    # Unknown optional fields may already contain resume-parsed or user-entered
    # values. Leaving them alone is safer than erasing them.
    return _outcome(field, key, "skip", field.value, asked=False, from_memory=False)


def _generate_and_write(page, field, resume_path, llm, run):
    if llm is None or not hasattr(llm, "generate") or field.kind == "file":
        return None
    started = time.perf_counter()
    generator = None
    before = 0
    try:
        generator = getattr(llm, "salary", None) if ask_key(field.label, field.section) == "salary_expectation" else llm
        if generator is None:
            return None
        if run.ai_context is None:
            # Job context is an allowlist of experience, projects, and skills. Contact details,
            # demographic answers, and rendered select values stay on the page.
            text = page.locator("body").evaluate(r"""body => {
                const clone = body.cloneNode(true);
                clone.querySelectorAll('script, style, input, textarea, select, option, [role=combobox], [role=listbox], [class*=single-value], [class*=multi-value]').forEach(el => el.remove());
                const drop = /equal employment|self-identif|demographic|voluntary|eeo|contact information|my information|^contact$/i;
                clone.querySelectorAll('h1,h2,h3,h4').forEach(el => {
                  if (!drop.test(el.innerText || '')) return;
                  let sib = el.nextElementSibling;
                  el.remove();
                  while (sib && !/^H[1-4]$/.test(sib.tagName)) {
                    const next = sib.nextElementSibling;
                    sib.remove();
                    sib = next;
                  }
                });
                return (clone.textContent || '').replace(/\s+/g, ' ').trim();
            }""")
            text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", " ", text)
            text = re.sub(r"\+?\d[\d\s().-]{7,}\d", " ", text)
            run.ai_context = page.url + "\n" + re.sub(r"\s+", " ", text).strip()[:7800]
        before = _attempted(generator)
        answer = generator.generate(label=field.label, kind=field.kind,
            options=[option["label"] for option in field.options if option.get("label")],
            job_context=run.ai_context)
        if answer:
            written = _write(page, field, answer.value, resume_path)
            if written:
                run.ai_answers.append({"label": field.label, "value": written, "evidence": answer.evidence})
                return _outcome(field, None, "fill", written, asked=False, from_memory=False, via_model=True)
    except Exception:
        pass
    finally:
        if generator is not None and _attempted(generator) > before:
            run.model_calls += 1
        run.model_seconds += time.perf_counter() - started
    return None


def _attempted(generator) -> int:
    backend = getattr(generator, "backend", None)
    if backend is None:
        return 0
    return int(getattr(backend, "attempted", 0) or 0) + int(getattr(backend, "status_attempted", 0) or 0)


def _generic_label(label: str) -> bool:
    flat = " ".join(re.sub(r"[^a-z0-9]+", " ", (label or "").casefold()).split())
    if _GENERIC_LABEL.match(flat):
        return True
    return bool(re.search(r"\bif yes\b", flat)) and bool(re.search(r"\b(explain|describe|comment)\b", flat))


def _label_key(field: ExtractedField, url: str = "") -> str:
    """Hash the full label. Generic prompts also include the page, so they do not cross employers."""
    full = " ".join(re.sub(r"[^a-z0-9]+", " ", field.label.casefold()).split())
    if _generic_label(field.label):
        parsed = urlsplit(url or "")
        full = f"{parsed.netloc}{parsed.path}\n{full}"
    return "q." + hashlib.sha256(full.encode()).hexdigest()[:20]


def _status_outcome(page, field, memory, resume_path, ask, run: FormRun, snapshot) -> FieldOutcome | None:
    """Status questions are answered from the user's stated facts, whatever the wording. None: not one."""
    from pocket_agent.forms import status

    if run.answerer is None:
        return None
    verdict = run.answerer.answer(field, page=snapshot.fields if snapshot is not None else ())
    if isinstance(verdict, status.NotStatus):
        return None
    if isinstance(verdict, status.Derived):
        written = _write(page, field, verdict.value, resume_path)
        if written:
            return _outcome(field, verdict.key, "fill", written, asked=False, from_memory=True, because=verdict.because)
        if not field.required:
            return _outcome(field, verdict.key, "skip", field.value, asked=False, from_memory=False)
        return _ask_or_saved(page, field, memory, resume_path, ask, _label_key(field))
    if not field.required:
        return _outcome(field, verdict.key, "skip", field.value, asked=False, from_memory=False)
    return _ask_or_saved(page, field, memory, resume_path, ask, _label_key(field), fact=verdict)


def _ask_or_saved(page, field, memory, resume_path, ask, key: str, *, fact=None) -> FieldOutcome:
    entry = memory.get(key)
    if entry and entry.source.startswith("profile_exact:"):
        normalize = lambda text: " ".join(text.replace('*', '').split()).strip().casefold()
        fingerprint = hashlib.sha256(normalize(field.label).encode()).hexdigest()
        if entry.source != "profile_exact:" + fingerprint:
            entry = None
    canonical = ask_key(field.label, field.section, _option_labels(field))
    # A status question is personal even when its storage key is a claim id, not a policy key.
    sensitive = is_sensitive_key(key) or canonical == key or fact is not None
    if entry and entry.always_use and entry.value.strip() and not (_generic_label(field.label) and entry.source == "answer"):
        written = _write_for(page, field, entry.value, resume_path, key)
        if written and not (page is not None and _invalid(page, getattr(field, "uid", ""))):
            return _outcome(field, key, "fill", written, asked=False, from_memory=True)
        if written:
            try:
                _clear_field(page, field)
            except Exception:
                pass
    question = question_for(field.label)
    if field.kind == "dropdown":
        offered = _peek_dropdown(page, field)
        if 0 < len(offered) <= 15:
            question += " Options: " + " / ".join(offered)
    answer = ask(Asked(
        key, field.label, question, kind=field.kind,
        options=tuple(option["label"] for option in getattr(field, "options", ()) if option.get("label")),
        sensitive=sensitive, section=getattr(field, "heading", "") or field.section,
    ))
    if answer.skip:
        return _outcome(field, key, "skip", "", asked=False, from_memory=False)
    existing = memory.get(key)
    stored = _canonical_answer(key, answer.value, existing)
    # Sensitive answers are remembered for the same fact unless the user opts out.
    # Other answers keep the flag the caller set. Opting out does not publish the fact.
    remember = (not answer.forget) if sensitive else answer.always_use
    # A form whose options do not express the stored fact must not replace it, or drop its consent.
    if not (existing is not None and existing.value == stored and (existing.always_use or existing.source != "answer")):
        memory.put(
            key,
            stored,
            always_use=remember,
            sensitive=sensitive,
            source="answer",
            label=field.label,
        )
    if fact is not None and fact.key and remember:
        # A status answer is also a fact about the user, so it settles every other wording of that same claim.
        stated = fact.fact(answer.value)
        if stated:
            memory.put(fact.key, stated, always_use=True, sensitive=True, source="answer")
    written = _write_for(page, field, stored, resume_path, key) if stored.strip() else ""
    if not written and field.value and not is_blank(field.value):
        _clear_field(page, field)
    return _outcome(field, key, "ask", written, asked=True, from_memory=False)


def resume_names_user(path: Path, memory: Memory) -> bool:
    """True when the resume file mentions the profile's name (or there is no name to check against)."""
    entry = memory.get("full_name")
    name = entry.value.strip().casefold() if entry and entry.value else ""
    if not name:
        return True
    try:
        if path.suffix.casefold() == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((pdf_page.extract_text() or "") for pdf_page in PdfReader(str(path)).pages)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — an unreadable file is not proof it is the wrong person
        return True
    flat = " ".join(text.split()).casefold()
    return name in flat or all(part in flat for part in name.split())


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


def _canonical_answer(key: str, value: str, existing) -> str:
    """Store a fact as its canonical value. Never replace one with another form's option wording."""
    from pocket_agent.forms import facts

    if not facts.is_fact(key):
        return value
    canon = facts.canonicalize(key, value)
    if canon:
        return canon
    if existing is not None and facts.canonicalize(key, existing.value):
        return existing.value
    return value


def _map_with_model(page, field, memory, resume_path, llm, run: FormRun):
    """Last resort before asking: map the question without sending the user's answers."""
    if llm is None or not hasattr(llm, "map_facts") or field.kind == "file":
        return None
    from pocket_agent.forms import facts

    catalog = facts.public_catalog(memory)
    if not catalog:
        return None
    labels = _option_labels(field)
    before = _attempted(llm)
    try:
        raw = llm.map_facts(label=field.label, options=labels, catalog=catalog)
    except Exception:
        return None
    finally:
        if _attempted(llm) > before:
            run.model_calls += 1
    applied = facts.apply_mapping(raw, options=labels, catalog=catalog, memory=memory)
    if applied is None:
        return None
    key, option = applied
    written = _write_for(page, field, option, resume_path, key)
    if not written:
        return None
    return _outcome(field, key, "fill", written, asked=False, from_memory=True, via_model=True)


def _retarget(page, field: ExtractedField) -> ExtractedField:
    """The page may have gained controls while we waited for an answer. Write to the field with this label."""
    snap = extract_page(page)
    matches = [
        candidate for candidate in snap.fields
        if candidate.label.casefold() == field.label.casefold()
        and candidate.kind == field.kind
        and candidate.group == field.group
    ]
    return matches[0] if len(matches) == 1 else field


def _write_for(page, field: ExtractedField, raw: str, resume_path: Path | None, key: str | None) -> str:
    token = _write_key.set(key)
    try:
        return _write(page, field, raw, resume_path)
    finally:
        _write_key.reset(token)


def _write(page, field: ExtractedField, raw: str, resume_path: Path | None) -> str:
    field = _retarget(page, field)
    options = [option["label"] for option in field.options]
    from pocket_agent.forms import facts

    key = _write_key.get()
    if facts.is_fact(key):
        rendered = facts.render(
            key, raw, kind=field.kind, options=options, question=field.label, input_type=field.input_type,
        )
        if rendered == "":
            return ""
        if rendered is not None:
            raw = rendered
    if field.kind == "multicheck":
        return _apply_multicheck(page, field, raw, key)
    if field.kind == "workday_prompt":
        root = page.locator(f'[data-pocket-uid="{field.uid}"]')
        if equivalent(_read_back(page, field), raw):
            return _read_back(page, field)
        root.fill("")
        root.press_sequentially(raw, delay=25)
        root.press("Enter")
        try:
            page.wait_for_function("""({uid, value}) => {
                const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
                if (!el || el.value) return false;
                let parent=el.parentElement;
                for(let i=0; parent&&i<4; i++,parent=parent.parentElement){
                    const selected=parent.querySelector('[role=listbox][aria-label*="selected" i]');
                    if(selected && selected.innerText.toLowerCase().includes(value.toLowerCase()))return true;
                }
                return false;
            }""", arg={"uid": field.uid, "value": raw}, timeout=8000)
        except Exception:
            return ""
        return _read_back(page, field)
    if field.kind == "workday_select":
        root = page.locator(f'[data-pocket-uid="{field.uid}"]')
        if equivalent(root.inner_text(), raw):
            return root.inner_text().strip()
        root.click()
        choices = page.get_by_role("option")
        try:
            choices.first.wait_for(state="visible", timeout=8000)
        except Exception:
            return ""
        labels = choices.all_inner_texts()
        choice = resolve_option(raw, labels)
        if not choice:
            root.press("Escape")
            return ""
        choices.nth(labels.index(choice)).click()
        return root.inner_text().strip() if equivalent(root.inner_text(), choice) else ""
    if field.kind == "file":
        if resume_path is None:
            return ""
        page.locator(f'[data-pocket-uid="{field.uid}"]').set_input_files(str(resume_path))
        _settle_upload(page, field.uid)
        from pocket_agent.forms.workday import account_scope
        if account_scope(page.url) and re.search(r"autofill with resume", page.locator("body").inner_text(), re.I):
            _wait_workday_upload(page, Path(resume_path).name)
        return "uploaded"
    part = date_part(field.label, raw, named=field.kind in {"dropdown", "select", "combobox"})
    if part:
        raw = part
    if field.kind == "dropdown":
        return _pick_dropdown(page, field, raw)
    target = present_value(raw, kind=field.kind, options=options, placeholder=field.placeholder, input_type=field.input_type)
    if field.kind == "date":
        variants = [target] + [item for item in date_variants(raw) if item != target]
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", raw.strip()):
            # A full day was given ("today's date"); do not shorten it to month/year first.
            variants = [raw.strip()] + [item for item in variants if item != raw.strip()]
    else:
        variants = [target]
    national = field.kind == "text" and bool(re.match(r"^\+\d{1,3}[\s-]+\(?\d", target or ""))
    if national:
        # Boards with a separate country-code picker (Eightfold) reject "+1-626-..." and want the national number.
        variants.append(re.sub(r"^\+\d{1,3}[\s-]+", "", target))
    if not target and field.kind in {"select", "combobox", "radio"}:
        return ""
    if field.input_type == "number":
        target = target.replace(",", "").strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?", target):
            # A number box refuses text; leave it for the user instead of crashing the run.
            return ""
        variants = [target]
    written = ""
    for variant in variants:
        if not variant:
            continue
        _apply(page, field, variant)
        if national:
            # Those boards validate on blur. blur() rather than a key press, so nothing can submit.
            page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate("el => el.blur()")
            page.wait_for_timeout(300)
        page.wait_for_timeout(30)
        if not _invalid(page, field.uid):
            written = _read_back(page, field)
            if written and set(written) <= set("•*"):
                written = variant
            break
    return written


_UPLOAD_BUSY = r"""
(uid) => {
  const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
  if (!el) return false;
  const block = el.closest("li, [class*='question'], [class*='field'], [role=group]") || el.parentElement;
  return [...block.querySelectorAll("*")].some((node) => {
    if (!node.offsetParent || node.children.length) return false;
    return /analy[sz]ing|parsing|uploading|processing/i.test(node.textContent || "");
  });
}
"""


def _settle_upload(page, uid: str) -> None:
    """Wait while the board reads the resume. Its autofill lands then, before our values, not on top of them."""
    page.wait_for_timeout(300)
    for _ in range(80):
        if not page.evaluate(_UPLOAD_BUSY, uid):
            break
        page.wait_for_timeout(250)
    else:
        raise TimeoutError("Resume upload is still processing")
    page.wait_for_timeout(300)


def _wait_workday_upload(page, filename: str) -> None:
    """Do not click Next while Workday is still receiving or parsing the PDF."""
    page.wait_for_function("""filename => {
        const shown = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
        const text = document.body.innerText;
        const busy = [...document.querySelectorAll('[aria-busy=true], [role=progressbar]')].some(shown)
          || /uploading|processing (?:your )?(?:resume|file)|parsing (?:your )?resume/i.test(text);
        const error = [...document.querySelectorAll('[role=alert], [data-automation-id*=error i]')]
          .some(el => shown(el) && /failed|unable|invalid|unsupported|too large|error/i.test(el.innerText));
        const next = document.querySelector('[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"]');
        const ready = !next || (!next.disabled && next.getAttribute('aria-disabled') !== 'true');
        const uploaded = /successfully uploaded|uploaded successfully/i.test(text);
        return !busy && !error && ready && uploaded && text.includes(filename);
    }""", arg=filename, timeout=30000)


def _workday_resume_step(page) -> bool:
    from pocket_agent.forms.workday import account_scope
    return bool(account_scope(page.url)) and bool(page.evaluate("""() =>
        [...document.querySelectorAll('h1,h2,h3,[data-automation-id=progressBarActiveStep]')]
        .some(el => el.getClientRects().length && /autofill with resume/i.test(el.innerText))
    """))


def _upload_workday_resume(page, memory: Memory, resume_path: Path | None) -> None:
    if resume_path is None or not Path(resume_path).is_file():
        raise FileNotFoundError("No resume PDF configured")
    if not resume_names_user(Path(resume_path), memory):
        raise ValueError("Resume name does not match the profile")
    filename = Path(resume_path).name
    # A previous run may already have uploaded the PDF. Do not upload it twice.
    text = page.locator('body').inner_text()
    if filename in text and re.search(r"successfully uploaded|uploaded successfully", text, re.I):
        _wait_workday_upload(page, filename)
        return
    target = page.locator('input[type=file][data-automation-id="file-upload-input-ref"]')
    # Older layouts have an unmarked hidden input beside the drop zone.
    if not target.count() and page.locator('input[type=file]').count() == 1:
        target = page.locator('input[type=file]')
    target.wait_for(state="attached", timeout=45000)
    target.set_input_files(str(resume_path))
    _wait_workday_upload(page, filename)


_NEARBY_OPTIONS = r"""
(uid) => {
  document.querySelectorAll("[data-pocket-pick]").forEach((node) => node.removeAttribute("data-pocket-pick"));
  const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
  if (!el || !el.parentElement) return [];
  const seen = [];
  for (const node of el.parentElement.querySelectorAll("[role=option], [class*='result'] > *, [class*='suggestion'], [class*='option']")) {
    if (node.contains(el) || !node.offsetParent || node.querySelector("[role=option]")) continue;
    const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
    if (!text || /no .*found|loading|no results/i.test(text)) continue;
    node.setAttribute("data-pocket-pick", String(seen.length));
    seen.push(text);
  }
  return seen;
}
"""


def _pick_nearby(page, field: ExtractedField, value: str) -> bool:
    """Type like a person so the board searches, then click the suggestion that matches the answer."""
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    for query in dict.fromkeys([value, value.split(",")[0].strip()]):
        if not query:
            continue
        root.click()
        root.fill("")
        root.press_sequentially(query, delay=25)
        for _ in range(12):
            page.wait_for_timeout(250)
            labels = page.evaluate(_NEARBY_OPTIONS, field.uid) or []
            choice = resolve_option(value, labels) or closest_option(value, labels)
            if choice:
                page.locator(f'[data-pocket-pick="{labels.index(choice)}"]').click()
                page.wait_for_timeout(100)
                return True
    root.fill(value)
    return False


_BOX = r"""
(uid) => {
  document.querySelectorAll("[data-pocket-box]").forEach((el) => el.removeAttribute("data-pocket-box"));
  const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
  if (!el) return;
  const box = el.closest(".select-shell") || el.closest("[class*='select__container']") || el.parentElement;
  box.setAttribute("data-pocket-box", uid);
}
"""

_BOX_OPTIONS = r"""
(uid) => {
  // Some selects render options into a separate listbox, named by aria-controls, once opened.
  const input = document.querySelector(`[data-pocket-uid="${uid}"]`);
  const list = input && document.getElementById(input.getAttribute("aria-controls") || "");
  if (list && !list.hasAttribute("data-pocket-box")) {
    document.querySelectorAll(`[data-pocket-box="${uid}"]`).forEach((el) => el.removeAttribute("data-pocket-box"));
    list.setAttribute("data-pocket-box", uid);
  }
  const box = document.querySelector(`[data-pocket-box="${uid}"]`);
  if (!box) return null;
  const labels = [...box.querySelectorAll("[role=option]")].map((el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim());
  const text = (box.innerText || "").toLowerCase();
  const loading = /loading/.test(text);
  return { labels, loading, done: !loading && (labels.length > 0 || /no options|no results|not found/.test(text)) };
}
"""


def _dropdown_queries(raw: str) -> list[str]:
    """Empty first (short lists show everything), then the answer, then shorter searches for long names.

    Search boxes match the typed text literally, so "University of California, Los Angeles" finds nothing
    when the site spells it "University of California - Los Angeles". The shorter searches drop punctuation.
    """
    plain = re.sub(r"\s*\([^)]*\)", "", raw).strip()
    words = re.sub(r"[^\w']+", " ", plain).split()
    queries = [""]
    for query in (raw, plain, " ".join(words)):
        if query and query not in queries:
            queries.append(query)
    for count in range(len(words) - 1, 1, -1):
        prefix = " ".join(words[:count])
        if prefix not in queries:
            queries.append(prefix)
    return queries[:6]


def _dropdown_labels(page, uid: str, before: dict | None = None) -> list[str]:
    """Options for the text just typed.

    An async list keeps showing the last search ("No options", or the old matches) until its debounce
    fires, so a reading only counts once the list has visibly changed or had time to, and it must read
    the same twice in a row.
    """
    old = before["labels"] if before else None
    moved = before is None
    last = None
    for step in range(20):
        page.wait_for_timeout(150)
        seen = page.evaluate(_BOX_OPTIONS, uid)
        if not seen:
            continue
        if seen["loading"] or seen["labels"] != old:
            moved = True
        if not moved and step < 6:
            continue
        if not seen["done"]:
            last = None
            continue
        if seen["labels"] == last:
            return list(last)
        last = seen["labels"]
    return list(last or [])


def _peek_dropdown(page, field: ExtractedField) -> list[str]:
    """Open the menu with no search, note what it offers, and close it again."""
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    page.evaluate(_BOX, field.uid)
    root.click()
    root.fill("")
    labels = _dropdown_labels(page, field.uid)
    root.press("Escape")
    return labels


def _pick_dropdown(page, field: ExtractedField, raw: str) -> str:
    """Open a search-select, look at what it offers, and click an option only when one clearly matches."""
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    page.evaluate(_BOX, field.uid)
    options = page.locator(f'[data-pocket-box="{field.uid}"] [role=option]')
    for query in _dropdown_queries(raw):
        # Clicking a react-select input while its menu is open closes it, and a closed menu does not search.
        if root.get_attribute("aria-expanded") != "true":
            root.click()
        before = page.evaluate(_BOX_OPTIONS, field.uid)
        root.fill(query)
        labels = _dropdown_labels(page, field.uid, before)
        choice = resolve_option(raw, labels) or closest_option(raw, labels)
        if choice:
            options.nth(labels.index(choice)).click()
            page.wait_for_timeout(50)
            if root.count() == 0:
                # Picking it removed the field (Greenhouse drops race and resets Hispanic/Latino on "Decline").
                return choice
            return _read_back(page, field) or choice
    root.fill("")
    root.press("Escape")
    return ""


def _apply_multicheck(page, field: ExtractedField, raw: str, key: str | None) -> str:
    from pocket_agent.forms import facts

    wanted = set(facts.race_values(raw) if key == "race" else [part.strip() for part in raw.split(",") if part.strip()])
    chosen: list[str] = []
    for option in field.options:
        meaning = facts.option_meaning(key or "", option["label"], field.label) if key else None
        label = option["label"]
        take = (meaning in wanted) or (label in wanted) or (facts.canonicalize(key, label) in wanted if key else False)
        if meaning and meaning.startswith("asian:") and "asian" in wanted and not any(item.startswith("asian:") for item in wanted):
            take = False
        if option.get("uid"):
            page.locator(f'[data-pocket-uid="{option["uid"]}"]').set_checked(bool(take))
        if take:
            chosen.append(label)
    return ", ".join(chosen)


def _apply(page, field: ExtractedField, value: str) -> None:
    if value.casefold() in {"immediately", "immediate", "asap", "now"} and (field.kind == "date" or field.input_type == "date"):
        from pocket_agent.paths import today
        value = today().isoformat()
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
        _pick_nearby(page, field, value)
        return
    picker = field.kind == "date" and root.get_attribute("aria-haspopup") == "dialog"
    if picker and root.evaluate("el => el.readOnly"):
        root.click()  # Eightfold's date picker only accepts typing once it is open.
    root.fill(value)
    if picker:
        root.press("Tab")  # Commit and close the calendar. Never Enter: that can submit the form.


def _clear_field(page, field: ExtractedField) -> None:
    if field.kind in {"file", "radio", "combobox", "typeahead", "dropdown"}:
        return
    locator = page.locator(f'[data-pocket-uid="{field.uid}"]')
    if field.kind == "checkbox":
        locator.set_checked(False)
        return
    locator.fill("")


def _read_back(page, field: ExtractedField) -> str:
    if field.kind == "workday_select":
        return page.locator(f'[data-pocket-uid="{field.uid}"]').inner_text().strip()
    if field.kind == "workday_prompt":
        return page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate("""el => {
            let parent=el.parentElement;
            for(let i=0; parent&&i<4; i++,parent=parent.parentElement){
                const selected=parent.querySelector('[role=listbox][aria-label*="selected" i]');
                if(selected)return selected.innerText.trim();
            }
            return '';
        }""")
    if field.kind == "radio":
        for option in field.options:
            if option["uid"] and page.locator(f'[data-pocket-uid="{option["uid"]}"]').is_checked():
                return option["label"]
        return ""
    if field.kind == "checkbox":
        return "Yes" if page.locator(f'[data-pocket-uid="{field.uid}"]').is_checked() else "No"
    if field.kind == "dropdown":
        return page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate(
            """(el) => {
              const shell = el.closest(".select-shell") || el.closest("[class*='select__container']") || el.parentElement;
              const value = shell && shell.querySelector("[class*='single-value']");
              return value ? (value.innerText || value.textContent || "").trim() : (el.value || "").trim();
            }"""
        )
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
          if (el.willValidate && typeof el.checkValidity === "function" && !el.checkValidity()) return true;
          const box = el.parentElement;
          return !!(box && box.querySelector("[role=alert]"));
        }""",
        uid,
    ))


class _AdvanceResult:
    def __init__(self, moved: bool, stalled: bool = False) -> None:
        self.moved = moved
        self.stalled = stalled

    def __bool__(self) -> bool:
        return self.moved


def _page_signature(page) -> str:
    return page.evaluate(
        """() => {
          const bar = document.querySelector('[data-automation-id=progressBarActiveStep], #progress');
          const progress = bar ? (bar.innerText || '') : '';
          const fields = [...document.querySelectorAll('input, textarea, select')].filter(el => el.getClientRects().length)
            .map(el => [el.name, el.id, el.getAttribute('aria-label'), el.type].join('|')).join('\\n');
          return location.href + '\\n' + progress + '\\n' + fields;
        }"""
    )


def _submits(page) -> int:
    return int(page.evaluate("() => window.__pocketSubmits || 0") or 0)


def _advance(page, snap: PageSnapshot) -> _AdvanceResult:
    if progress_is_last(snap.progress):
        return _AdvanceResult(False)
    forwards = []
    for button in snap.buttons:
        # Workday's wizard navigation is type=submit on intermediate pages too.
        target = page.locator(f'[data-pocket-uid="{button.uid}"]')
        from pocket_agent.forms.workday import account_scope
        workday_next = (
            account_scope(page.url)
            and (_workday_resume_step(page) or bool(snap.progress))
            and not re.search(r"\breview\b", snap.progress, re.I)
            and not re.search(r"\breview\b", button.heading, re.I)
            and button.text.casefold() in {"next", "continue", "save and continue"}
            and target.get_attribute("data-automation-id") in {"bottom-navigation-next-button", "pageFooterNextButton"}
        )
        if workday_next:
            upload_step = _workday_resume_step(page)
            attached = not upload_step or any(field.kind == "file" and field.value == "uploaded" for field in snap.fields) or bool(
                re.search(r"\S+\.pdf\b", page.locator("body").inner_text(), re.I)
            )
            if any(field.required and _missing(field) for field in snap.fields):
                return _AdvanceResult(False)
            if page.locator('input:invalid, select:invalid, textarea:invalid, [aria-invalid=true]').count():
                return _AdvanceResult(False)
            if attached and target.is_enabled():
                if target.get_attribute("aria-hidden") == "true":
                    overlay = page.locator('[data-automation-id="click_filter"]').and_(
                        page.get_by_role("button", name=button.text, exact=True)
                    )
                    if overlay.count() != 1 or not overlay.is_visible():
                        return _AdvanceResult(False)
                    target = overlay
                target.evaluate("el => {const form = el.closest('form'); if (form) form.dataset.pocketWizard='true';}")
                counted = _submits(page)
                try:
                    target.click(timeout=8000)
                    if upload_step:
                        page.wait_for_function("""() => [...document.querySelectorAll('h1,h2,h3')]
                            .some(el => el.getClientRects().length && /^my information$/i.test(el.innerText.trim()))""", timeout=30000)
                    else:
                        page.wait_for_function(r"""before => {
                            const step = document.querySelector('[data-automation-id=progressBarActiveStep], #progress');
                            const normalize = text => text.replace(/\s+/g,' ').trim();
                            const current = step && normalize(step.innerText);
                            if (!current || current === normalize(before)) return false;
                            const title = current.replace(/^current step \d+ of \d+\s*/i,'').toLowerCase();
                            return [...document.querySelectorAll('h1,h2,h3')].some(el => {
                                const heading=normalize(el.innerText).toLowerCase();
                                return el.getClientRects().length && heading && (heading===title || title.startsWith(heading+' '));
                            });
                        }""", arg=snap.progress, timeout=30000)
                    later = _submits(page)
                    landed = page.url
                    heading = page.locator("h1, h2").first.inner_text() if page.locator("h1, h2").count() else ""
                    submitted = "/submitted" in landed or bool(re.search(r"\b(review|confirm)\b", heading, re.I))
                    if later > counted and not submitted:
                        # The verified Next control moved the wizard. That navigation submit is not an application submit.
                        page.evaluate("(n) => { window.__pocketSubmits = Math.max(0, (window.__pocketSubmits || 0) - n); }", later - counted)
                    return _AdvanceResult(moved=not submitted and later >= counted)
                except Exception:
                    return _AdvanceResult(False)
                finally:
                    page.locator('form[data-pocket-wizard]').evaluate_all("forms=>forms.forEach(form=>delete form.dataset.pocketWizard)")
            continue
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
        return _AdvanceResult(False)
    before = _submits(page)
    before_url = page.url
    before_sig = _page_signature(page)
    blocked = {"n": 0}

    def _block_post(route) -> None:
        try:
            if route.request.method.upper() == "GET":
                try:
                    route.fallback()
                except Exception:
                    route.continue_()
                return
        except Exception:
            return
        blocked["n"] += 1
        try:
            route.abort()
        except Exception:
            pass

    page.route("**/*", _block_post)
    try:
        page.locator(f'[data-pocket-uid="{forwards[0].uid}"]').click(timeout=8000)
        deadline = time.monotonic() + 2.5
        moved = False
        while time.monotonic() < deadline:
            if page.url != before_url or _page_signature(page) != before_sig:
                moved = True
                break
            page.wait_for_timeout(100)
    except Exception:
        try:
            moved = page.url != before_url or _page_signature(page) != before_sig
        except Exception:
            moved = False
    finally:
        try:
            page.unroute("**/*", _block_post)
        except Exception:
            pass
    if blocked["n"]:
        page.evaluate("(n) => { window.__pocketSubmits = (window.__pocketSubmits || 0) + n; }", blocked["n"])
    after = _submits(page)
    if after > before or blocked["n"] or "/submitted" in page.url:
        return _AdvanceResult(False)
    if moved:
        return _AdvanceResult(True)
    # The click landed and the page did not change. Do not click it again.
    return _AdvanceResult(False, stalled=True)


def _outcome(field: ExtractedField, key: str | None, action: str, value: str, *, asked: bool, from_memory: bool, via_model: bool = False, because: str = "") -> FieldOutcome:
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
        because=because,
    )
