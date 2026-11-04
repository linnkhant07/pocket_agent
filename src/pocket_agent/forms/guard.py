"""Decide which buttons may be clicked. Submit is never one of them."""

from __future__ import annotations

import re

_SUBMIT = re.compile(
    r"\b(submit|apply now|apply|send application|confirm application|finish and submit|agree and submit|finish|review and submit)\b",
    re.I,
)
_ACCOUNT = re.compile(r"\b(create account|create an account|sign in|log in|sign up|signup)\b", re.I)
_FORWARD = re.compile(r"\b(next|continue|save and continue)\b", re.I)
_DISMISS = re.compile(r"\b(no thanks|close|dismiss|not now|maybe later|cancel)\b", re.I)
_ADD = re.compile(r"\badd another\b|\badd (?:work|experience|employment|education|job|another)\b", re.I)
_STEP = re.compile(r"step\s+(\d+)\s+of\s+(\d+)", re.I)


def progress_is_last(text: str) -> bool:
    match = _STEP.search(text or "")
    if not match:
        return False
    return int(match.group(1)) == int(match.group(2)) and int(match.group(2)) > 0


def looks_like_submit(text: str, *, button_type: str = "", onclick: str = "") -> bool:
    if (button_type or "").casefold() == "submit":
        return True
    if _SUBMIT.search(text or ""):
        return True
    script = (onclick or "").casefold()
    if "submit" in script or "requestsubmit" in script:
        return True
    return False


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
    if _FORWARD.search(text or "") and re.search(r"\breview\b", heading or "", re.I):
        return "submit"
    if _DISMISS.search(text or ""):
        return "dismiss"
    if _ADD.search(text or ""):
        return "add"
    if in_dialog:
        return "other"
    if _FORWARD.search(text or ""):
        return "forward"
    return "other"
