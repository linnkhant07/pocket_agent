---
name: verify-submit-guard
description: Exhaustive bounded check of Pocket Agent's "nothing is submitted for you" promise. It runs the real button guard (forms/guard.py) through every non-Workday click path in forms/engine.py over 23.5M inputs in about 10 s. Use it on any change to guard.py, to _advance, _dismiss, or _add_button in engine.py, or to how buttons are read in reader.py, and before claiming a change is submit-safe.
---

# Verify the submit guard

**Property.** The filler's generic click paths never click a control that could submit the application. Those paths are Next/Continue in `_advance`, dialog dismissal in `_dismiss`, and "add another work block" in `_add_button`.

**Harm if broken.** An applicant's half-checked application goes to an employer without their review. The README, `guard.py`'s docstring, and the CLI all promise this never happens.

**Tool and why.** No verifier runs exactly on this code. The guard is Python `re` with `\b`. CrossHair silently missed a known counterexample ("Next submission"), and regex-to-automaton libraries (interegular) reject `\b`. So, per the pstack fallback, this check runs the **real code** on **every input in a bounded scope** and checks each result against an independent spec. It is exhaustive within the bound, not a sample.

## Run

```bash
uv run python .claude/skills/verify-submit-guard/check_submit_guard.py
```

It takes about 10 s with no dependencies beyond the repo's own. Exit codes:

- **0**: nothing beyond `known_findings.json`.
- **1**: a NEW violation class. Its shortest witness is printed.
- **3**: harness error.

A line marked `fixed` means a known finding no longer occurs. Remove it from `known_findings.json` (or rerun with `--write-baseline` once you have confirmed that every remaining class is intended).

## What is checked (scope and bounds)

The checker composes the real `classify_button` and `progress_is_last` exactly as each covered click path does:

| Path | Code | When it clicks |
| --- | --- | --- |
| Advance | `engine._advance`, generic branch from the `classify_button(` call to the `.click()` on `forwards[0]` | `not progress_is_last(progress)` and kind == `forward` and not in a dialog |
| Dismiss | `engine._dismiss` | kind == `dismiss`, with `in_dialog=True` |
| Add | `engine._add_button` | kind == `add`. Its extra section check is treated as always true, which over-approximates the clicks and keeps the check sound. |

Inputs, all combined exhaustively:

- **Labels**: every sequence of 1–3 words over 27 words (next, continue, save, submit, submission, submitting, apply, application, send, confirm, finish, finalize, complete, review, sign, in, create, account, close, add, another, and connectives), in lower, Title, and UPPER case.
- **type**: `""`, `button`, `submit`, `SUBMIT`.
- **onclick**: none, `goNext()`, `form.submit()`, `this.form.requestSubmit()`.
- **in_dialog**: true and false.
- **heading**: 11 headings, including Review, Review and Submit, Summary, Confirmation, and plain steps.
- **progress text**: 980 indicators. The prefixes are Step, STEP, Current Step, Page, or none. The separators are "of", "OF", "/", " / ". The numbers run 0–6.

The independent spec says a click **could submit** when any of these holds:

- The label contains a token in {submit, submission, submitting, apply, send, confirm, finish, finalize}.
- `type=submit`.
- `onclick` mentions submit.
- The heading contains a token in {review, reviewing, summary, confirm, confirmation}.
- The progress reads X of Y or X/Y with X == Y > 0.

**Not covered:**

- The Workday branch of `_advance` (`workday_next`). It deliberately clicks `type=submit` Next buttons by `data-automation-id`.
- `workday.py` account clicks.
- Field widgets (`root.click()`, `press("Enter")` in text inputs).
- How `reader.py` extracts `text`, `button_type`, `onclick`, and `heading` from the DOM. The check trusts those values.
- Labels longer than 3 words or with words outside the vocabulary, and non-ASCII look-alikes.
- The runtime backstop (`window.__pocketSubmits` and `/submitted`). That detects a submission after the fact and is not prevention.

## Known findings (current code breaks the property)

`known_findings.json` records 29 classes from three root causes. Product code has not been changed. These are for the user to decide on.

1. **Submit-like verbs the guard doesn't know.** `_SUBMIT` matches whole words submit, apply, "send application", "confirm application", finish, and similar. It misses submission, submitting, send, confirm, and finalize on their own. So `_advance` clicks "Next submission" or "Next confirm", `_dismiss` clicks a dialog's "Confirm close", and `_add_button` clicks "Send add another".
2. **Final-step headings other than "Review".** The heading rule is `\breview\b`, so a Next/Continue under "Reviewing your application", "Summary", "Confirm your details", or "Confirmation" is clicked.
3. **Last-step indicators other than "Step X of Y".** `progress_is_last` only parses `step N of M`. "Page 1 of 1", "Step 2/2", "2 / 2", and a bare "1 of 1" are not treated as last, so a Next there is clicked.

Read a witness such as `clicks 'next' [progress='Page 1 of 1']` as follows: on a page whose progress text says "Page 1 of 1", `_advance` would click a button labelled "next".

## Real bug or modeling error?

When the check prints NEW, follow these steps:

1. Recreate the witness in the real code. For example, `uv run python -c "from pocket_agent.forms.guard import classify_button; print(classify_button('Next submission'))"`, or the `progress_is_last(...)` equivalent.
2. If the guard returns what the checker says, and the matching engine path would click it, the bug is real. Report it with the witness. Don't weaken the spec or add it to the baseline to make the run pass.
3. If the engine path no longer clicks the way the table above says, the checker is stale. Update `advance_clicks`, `dismiss_clicks`, or `add_clicks` to mirror the new code, cite the new lines, and rerun.
4. If a change is meant to allow a label (for example, a reviewed decision that "Send code" is safe), change the spec token sets in the checker deliberately, and say so in the change description.

## Proof that it can fail

At the sync commit, each of these scratch mutations to `guard.py` produced NEW violations. Each was reverted, and the check then passed again with an empty baseline:

- `looks_like_submit` without its type=submit check → `clicks 'next' [type='submit']`.
- Without its onclick check → `next`, `close`, and `add another` with `onclick='form.submit()'` are clicked.
- `classify_button` without its review-heading rule → `next` under the Review, Review and Submit, and Application Review headings is clicked.
- `progress_is_last` true only for "Step 1 of 1" → `next` at "Step 2 of 2" is clicked.

## Coverage record

- `src/pocket_agent/forms/guard.py`: `looks_like_submit`, `classify_button`, `progress_is_last`, and the `_SUBMIT`, `_ACCOUNT`, `_FORWARD`, `_DISMISS`, `_ADD`, and `_STEP` patterns.
- `src/pocket_agent/forms/engine.py`: `_advance` (the generic branch after the Workday `continue`), `_dismiss`, and `_add_button`.
- **Sync commit: `bbe663d`.** The checker was re-run after the last-page, final-heading, and exact-dismiss fixes. `known_findings.json` is empty: the 29 previous classes are refused, and the run reports no new violation. Both files matched this commit when that pass was confirmed.

Before trusting a pass, check for drift:

```bash
git diff bbe663d -- src/pocket_agent/forms/guard.py src/pocket_agent/forms/engine.py
```

If the diff touches covered code, reread it against the path table above and update the checker if needed. Move the sync commit forward only after confirming that the checker describes the code at the new commit.
