"""Decide which buttons may be clicked. Submit is never one of them."""

from __future__ import annotations

import re

# Whole words only. "send", "confirm", and "finalize" on their own can submit.
_SUBMIT = re.compile(
    r"\b(submit|submitting|submission|apply now|apply|send application|send|confirm application|"
    r"confirm|confirmation|finish and submit|agree and submit|finish|finalize|review and submit)\b",
    re.I,
)
_ACCOUNT = re.compile(r"\b(create account|create an account|sign in|log in|sign up|signup)\b", re.I)
_FORWARD = re.compile(r"\b(next|continue|save and continue)\b", re.I)
# Exact short dismiss labels. "Cancel application" is not one of them.
_DISMISS_EXACT = {"close", "x", "no thanks", "not now", "maybe later", "dismiss"}
_DISMISS_BLOCK = re.compile(r"\b(application|account|submit|delete)\b", re.I)
_ADD = re.compile(r"\badd another\b|\badd (?:work|experience|employment|education|job|another)\b", re.I)
# "Page 2 of 2", "2/2", "2 / 2", "Step 2 of 2", and a bare "1 of 1".
_STEP = re.compile(
    r"(?:(?:current\s+)?(?:step|page)\s+)?(\d+)\s*(?:of|/)\s*(\d+)",
    re.I,
)
_FINAL_HEADING = re.compile(
    r"\b(review(?:ing)?|summary|confirm(?:ation)?|submission|send|finalize)\b",
    re.I,
)


def progress_is_last(text: str) -> bool:
    match = _STEP.search(text or "")
    if not match:
        return False
    current, total = int(match.group(1)), int(match.group(2))
    return total > 0 and current == total


def looks_like_submit(text: str, *, button_type: str = "", onclick: str = "") -> bool:
    if (button_type or "").casefold() == "submit":
        return True
    if _SUBMIT.search(text or ""):
        return True
    script = (onclick or "").casefold()
    if "submit" in script or "requestsubmit" in script:
        return True
    return False


def _is_dismiss(text: str) -> bool:
    label = " ".join((text or "").casefold().split())
    if not label or label not in _DISMISS_EXACT:
        return False
    if _DISMISS_BLOCK.search(label):
        return False
    return True


def classify_button(
    text: str,
    *,
    button_type: str = "",
    onclick: str = "",
    in_dialog: bool = False,
    heading: str = "",
) -> str:
    """Return submit, account, forward, dismiss, add, or other."""
    if looks_like_submit(text, button_type=button_type, onclick=onclick):
        return "submit"
    if _ACCOUNT.search(text or ""):
        return "account"
    if _FORWARD.search(text or "") and _FINAL_HEADING.search(heading or ""):
        return "submit"
    if _is_dismiss(text):
        return "dismiss"
    if _ADD.search(text or ""):
        return "add"
    if in_dialog:
        return "other"
    if _FORWARD.search(text or ""):
        return "forward"
    return "other"
