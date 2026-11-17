"""Exhaustive bounded check of Pocket Agent's no-submit promise on the generic advance path.

Runs the REAL `classify_button` and `progress_is_last` from src/pocket_agent/forms/guard.py on every input in
the bounded scope below, composes them exactly as `_advance` does for non-Workday pages
(src/pocket_agent/forms/engine.py, the classify_button call through the final `.click()`), and compares the result
against an independent spec of "this click could submit the application".

Usage:  uv run python .claude/skills/verify-submit-guard/check_submit_guard.py [--max-words N] [--json PATH]
Exit 0: no violation beyond known_findings.json. Exit 1: a NEW violation class. Exit 3: harness error.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import time

from pathlib import Path

from pocket_agent.forms.guard import classify_button, progress_is_last

BASELINE = Path(__file__).with_name("known_findings.json")

# ---- Bounded scope -------------------------------------------------------------------------------------------
# Button-label vocabulary: the forward words the guard accepts, every submit-ish verb an applicant-facing form uses,
# account/dismiss/add words the guard also routes, and connectives that appear in real labels.
WORDS = [
    "next", "continue", "save", "and", "to", "&", "-",
    "submit", "submission", "submitting", "apply", "application", "send", "confirm", "finish", "finalize", "complete",
    "review", "sign", "in", "create", "account", "close", "add", "another", "now", "step",
]
CASES = [str.lower, str.title, str.upper]
BUTTON_TYPES = ["", "button", "submit", "SUBMIT"]
ONCLICKS = ["", "goNext()", "form.submit()", "this.form.requestSubmit()"]
DIALOG = [False, True]
HEADINGS = ["", "Contact Information", "Work Experience", "Review", "Review and Submit", "Application Review",
            "Reviewing your application", "Summary", "Confirm your details", "Confirmation", "Voluntary Disclosures"]
PROGRESS = (
    [""] +
    [f"{prefix}{a}{sep}{b}" for prefix in ("Step ", "STEP ", "Current Step ", "Page ", "")
     for sep in (" of ", " OF ", "/", " / ") for a in range(0, 7) for b in range(0, 7)]
)

# ---- Independent spec: could clicking this control submit the application? ----------------------------------
_TOKEN = re.compile(r"[a-z0-9]+")
SUBMIT_TOKENS = {"submit", "submission", "submitting", "apply", "send", "confirm", "finish", "finalize"}
FINAL_HEADING_TOKENS = {"review", "reviewing", "summary", "confirm", "confirmation"}


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.casefold()))


def spec_last_step(progress: str) -> bool:
    """A step indicator 'X of Y' or 'X/Y' (any prefix such as Step or Page) with X == Y > 0 is the final step."""
    match = re.search(r"(\d+)\s*(?:of|/)\s*(\d+)", progress, re.I)
    return bool(match) and int(match.group(2)) > 0 and int(match.group(1)) == int(match.group(2))


def harm_reasons(text: str, button_type: str, onclick: str, heading: str, progress: str) -> list[str]:
    reasons = []
    if tokens(text) & SUBMIT_TOKENS:
        reasons.append("label asks to submit")
    if button_type.casefold() == "submit":
        reasons.append("type=submit")
    if "submit" in onclick.casefold():
        reasons.append("onclick submits")
    if tokens(heading) & FINAL_HEADING_TOKENS:
        reasons.append("final/review heading")
    if spec_last_step(progress):
        reasons.append("last step")
    return reasons


# ---- The code under check, composed exactly as engine._advance's generic path ---------------------------------
def advance_clicks(text: str, button_type: str, onclick: str, in_dialog: bool, heading: str, progress: str) -> bool:
    if progress_is_last(progress):                       # engine._advance: `if progress_is_last(snap.progress): return False`
        return False
    kind = classify_button(text, button_type=button_type, onclick=onclick, in_dialog=in_dialog, heading=heading)
    return kind == "forward" and not in_dialog           # `if kind == "forward" and not button.in_dialog: forwards.append`


def dismiss_clicks(text: str, button_type: str, onclick: str) -> bool:
    # engine._dismiss: clicks an in-dialog button when classify_button(..., in_dialog=True) == "dismiss" (no heading passed).
    return classify_button(text, button_type=button_type, onclick=onclick, in_dialog=True) == "dismiss"


def add_clicks(text: str, button_type: str, onclick: str) -> bool:
    # engine._add_button: clicks when classify_button(...) == "add" (no heading/dialog passed). The extra section check
    # there only narrows clicks, so treating it as always satisfied over-approximates and stays sound for this property.
    return classify_button(text, button_type=button_type, onclick=onclick) == "add"


def labels(max_words: int):
    seen = set()
    for n in range(1, max_words + 1):
        for combo in itertools.product(WORDS, repeat=n):
            for case in CASES:
                text = case(" ".join(combo))
                if text not in seen:
                    seen.add(text)
                    yield text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-words", type=int, default=3)
    parser.add_argument("--json", help="Write all distinct violation classes here")
    parser.add_argument("--baseline", default=str(BASELINE), help="Known findings; only classes not in it fail the run")
    parser.add_argument("--write-baseline", action="store_true", help="Record the current classes as known findings")
    args = parser.parse_args()
    started = time.time()
    checked = 0
    # Group violations by (reason set, guard result) and keep the shortest witness of each class.
    classes: dict[tuple, dict] = {}

    # Stage 1: label × type × onclick × dialog × heading, with no progress indicator.
    clickable = []
    for text in labels(args.max_words):
        for button_type, onclick, in_dialog, heading in itertools.product(BUTTON_TYPES, ONCLICKS, DIALOG, HEADINGS):
            checked += 1
            if not advance_clicks(text, button_type, onclick, in_dialog, heading, ""):
                continue
            reasons = harm_reasons(text, button_type, onclick, heading, "")
            if not reasons:
                if button_type == "" and onclick == "" and heading == "":
                    clickable.append(text)
                continue
            witness = {"label": text, "type": button_type, "onclick": onclick, "in_dialog": in_dialog,
                       "heading": heading, "progress": "", "reasons": reasons}
            # One class per single cause, so each gap is reported once with its shortest witness.
            for reason in reasons:
                if reason == "label asks to submit":
                    details = sorted(tokens(text) & SUBMIT_TOKENS)
                elif reason == "final/review heading":
                    details = [heading]
                else:
                    details = [button_type.casefold() or onclick]
                for detail in details:
                    key = (reason, detail)
                    best = classes.get(key)
                    if best is None or (len(best["reasons"]), len(best["label"])) > (len(reasons), len(text)):
                        classes[key] = witness

    # Stage 1b: the dismiss-dialog and add-work-block click paths, label x type x onclick.
    for text in labels(args.max_words):
        for button_type, onclick in itertools.product(BUTTON_TYPES, ONCLICKS):
            for path, clicks in (("dismiss path", dismiss_clicks), ("add path", add_clicks)):
                checked += 1
                if not clicks(text, button_type, onclick):
                    continue
                reasons = harm_reasons(text, button_type, onclick, "", "")
                for reason in reasons:
                    details = sorted(tokens(text) & SUBMIT_TOKENS) if reason == "label asks to submit" else [button_type.casefold() or onclick]
                    for detail in details:
                        key = (f"{path}: {reason}", detail)
                        witness = {"label": text, "type": button_type, "onclick": onclick, "in_dialog": path == "dismiss path",
                                   "heading": "", "progress": "", "reasons": [f"{path}: {r}" for r in reasons]}
                        best = classes.get(key)
                        if best is None or (len(best["reasons"]), len(best["label"])) > (len(witness["reasons"]), len(text)):
                            classes[key] = witness

    # Stage 2: every progress string × a clean forward label that stage 1 proved clickable.
    probe = sorted(clickable, key=len)[:1] or ["Next"]
    for progress in PROGRESS:
        for text in probe:
            checked += 1
            if advance_clicks(text, "", "", False, "", progress) and spec_last_step(progress):
                key = ("last step", re.sub(r"\d+", "N", progress).casefold())
                classes.setdefault(key, {"label": text, "type": "", "onclick": "", "in_dialog": False, "heading": "",
                                         "progress": progress, "reasons": ["last step"]})

    elapsed = time.time() - started
    print(f"checked {checked:,} inputs (labels up to {args.max_words} words over {len(WORDS)} words x 3 casings) in {elapsed:.1f}s")
    print(f"clean clickable labels found: {len(set(clickable)):,} (e.g. {sorted(set(clickable), key=len)[:4]})")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(list(classes.values()), handle, indent=2)
    current = {f"{key[0]} | {key[1]}": witness for key, witness in classes.items()}
    if args.write_baseline:
        Path(args.baseline).write_text(json.dumps(sorted(current), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(current)} known findings to {args.baseline}")
        return 0
    known = set(json.loads(Path(args.baseline).read_text(encoding="utf-8"))) if Path(args.baseline).exists() else set()
    new = {key: w for key, w in current.items() if key not in known}
    fixed = sorted(known - set(current))

    def show(witness: dict) -> str:
        where = ", ".join(f"{k}={witness[k]!r}" for k in ("type", "onclick", "heading", "progress") if witness[k])
        return f"clicks {witness['label']!r}" + (f" [{where}]" if where else "") + f"  <- {'; '.join(witness['reasons'])}"

    for key in sorted(current):
        print(f"  {'NEW  ' if key in new else 'known'} {show(current[key])}")
    for key in fixed:
        print(f"  fixed {key}  (no longer violated: remove it from {Path(args.baseline).name})")
    if new:
        print(f"FAIL: {len(new)} new violation class(es) beyond {len(current) - len(new)} known finding(s).")
        return 1
    print(f"PASS: no new violations ({len(current)} known finding(s), {len(fixed)} fixed).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"harness error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(3)
