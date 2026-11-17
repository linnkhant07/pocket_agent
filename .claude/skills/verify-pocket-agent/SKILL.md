---
name: verify-pocket-agent
description: Drive Pocket Agent (job-application filler) the way a user does, through the `pocket-agent fill` CLI and the local app (`pocket-agent ui`), against the repo's fictional form suite, in an isolated POCKET_AGENT_HOME. Use it to prove a change to the filler, the app, account fills, inline questions, or saved answers works in the real app, not only in pytest.
---

# Verify Pocket Agent

Pocket Agent fills job applications in Playwright Chromium and stops before Submit. Users reach it two ways:

- **CLI**: `pocket-agent fill URL ...`, which reads local files in `POCKET_AGENT_HOME`.
- **App**: `pocket-agent ui`, a FastAPI app on 127.0.0.1. It holds Google sign-in, resume upload, profile, answers, history, and an Apply section whose Fill button starts an account fill through the websocket at `/ws`.

Every verification here runs against the **fictional** form suite (`forms/catalog.py`, `forms/hard_catalog.py`, and the demo form in `demo.py`), which a local server provides. Never point a verification run at a real employer URL, and never let it click Submit. Live Workday or other employer sites need the user's own credentials and are out of scope.

All paths below are relative to the repo root. `S=.claude/skills/verify-pocket-agent/scripts`.

## Launch

```bash
S=.claude/skills/verify-pocket-agent/scripts
RUN=$($S/launch.sh --ui)     # omit --ui when you only need the CLI; HEADED=1 shows the filler window
source "$RUN/env"            # exports RUN, POCKET_AGENT_HOME, FORMS, and with --ui also UI and FILLER_CDP
```

`launch.sh` does four things:

- Creates `$RUN = .pytest_cache/verify-pocket-agent/run-<ts>-<pid>/`.
- Runs `pocket-agent init --no-pull --yes` into a fresh `$RUN/home`. That copies the fictional Jordan Hale `profile.yaml` and `resume.pdf` from `examples/`.
- Starts `forms_server.py` on a free port. It prints `READY <url>` in `$RUN/logs/forms.log`.
- With `--ui`, starts `scripts/serve_app.py`. That runs the real app (`ui.app.create_app`) on a free port with the in-memory Supabase account and Keychain from `tests/account_fakes.py`, the same fakes pytest installs. The account starts signed in, with the fictional Jordan Hale profile and resume. With `HEADED=1` the filler's Chromium window is visible.

The app path cannot run against real Supabase without a real Google sign-in, so it always uses that fake account. Account fills require an HTTPS URL, so the filler's browser serves the fictional forms at `https://forms.pocket.test/form/<id>`, proxied to the local forms server. The filler also listens for CDP on `$FILLER_CDP`, which `drive_ui.py` uses to read the filled page.

It returns only when the forms server serves and `GET $UI/` answers. It exits non-zero and names the log otherwise.

Each launch is fully isolated: its own ports, home, and pid files. Two runs can go side by side. The user's real `~/.pocket-agent` is never touched as long as `POCKET_AGENT_HOME` is exported, so **always `source "$RUN/env"` before any `uv run pocket-agent ...`**.

Prerequisites (one time): `uv sync --extra dev` and `uv run playwright install chromium`. No API key, Ollama, or network is needed, because every run uses `--model none`.

## Doctor

```bash
$S/doctor.sh "$RUN"
```

The check is read-only. It confirms these things:

- The isolated home has its profile and resume, and is not `~/.pocket-agent`.
- The forms and UI pids we started are alive.
- The forms server serves `/form/prototype-demo`.
- The app serves its page, restores the fake signed-in session, and rejects a foreign Host header.
- Playwright Chromium launches.

Run it first whenever anything looks off. `doctor: unhealthy` means: clean up and relaunch, don't drive.

## Drive

The CLI form URLs are `$FORMS/form/<id>`. In the app, use `https://forms.pocket.test/form/<id>`. Useful ids:

| id | What it exercises |
| --- | --- |
| `prototype-demo` | Contact, education, repeated work history, resume upload, then a final step. No questions once saved answers exist. |
| `gh-amber` | A simple Greenhouse-style form with no questions. |
| `hd-questions` | Free-text, select, and choice questions the user must answer. |
| `hd-eeo` | Sensitive questions. The ask card shows "Always use this answer", off by default. |
| `hd-login` | A login gate. The fill ends `blocked` and the app shows Continue on this page. |
| `wd-*`, `gh-*`, `lv-*`, `hd-*`, `hh-*` | The full suites. See `forms/catalog.py` and `forms/hard_catalog.py`. |

**CLI fill** (real `pocket-agent fill`, unattended and headless, so it exits by itself):

```bash
$S/fill_cli.sh "$RUN" prototype-demo          # extra args are passed through to `fill`
```

**App fill** (a headless Playwright that drives the Apply section like a user):

```bash
uv run python $S/drive_ui.py prototype-demo                   # asks two questions, ends on the Review step
uv run python $S/drive_ui.py hd-eeo                           # sensitive questions
uv run python $S/drive_ui.py hd-login --expect blocked        # sign-in gate
```

`drive_ui.py` follows these steps:

1. Waits for the signed-in workspace, types the URL into `#jobUrl`, and clicks `#fill`.
2. Answers each `#askCard`. It picks an option in a select, a date for a date input, or types text, then clicks `#askSend`.
3. Waits for `#fillResult`, whose `data-status` is the `done` event's status.
4. Attaches to the filler over CDP and reads its active step heading and `window.__pocketSubmits`.

It exits 0 only when the status matches `--expect` (default `stopped`) and the filler shows 0 submits. The fictional forms end on a "Step N of N" progress line, which the engine reports as `stopped`, not `review`. `filler_step` in `result.json` shows where the filler stopped, such as `Review` for `prototype-demo`.

Stable handles in the app (`ui/static/index.html`):

- Sign-in: `#googleLogin`, `#logout`, `#identity`, `#workspace`.
- Profile and answers: `#resumeFile`, `#profileForm`, `#questionList`.
- Apply: `#jobUrl`, `#model`, `#fill`, `#continueFill`, `#askCard`, `#askQuestion`, `#askValue`, `#askRemember`, `#askSend`, `#askSkip`, `#fillLog`, `#fillResult[data-status]`.
- History: `#runList`.

To seed a saved answer for the CLI, add it under `form_answers:` in `$POCKET_AGENT_HOME/profile.yaml` (see the README). In the app, answer it in the Answers section or during a fill. App answers live only in the fake account and disappear when the app stops.

## Evidence

Everything goes under `$RUN/evidence/` and survives cleanup:

- `cli-<id>/`: `stdout.txt` (the filled/skipped list and the final status line), `last-fill-report.json` (`status`, `current_step`, `unresolved`, `validation_errors`, `submits`, `ai`), `exit_code`, and `memory-keys.txt` (keys in `forms.sqlite` after the run, which is the saved-answer side effect).
- `ui-<id>/`: `01-before-fill.png`, `02-question-N.png`, `03-end.png` (full page), `04-filler.png` (the filler's page), and `result.json` (status, filler step, submits, questions answered, the fill log, history, and saved answers).

Proof standards:

- Drive the real path: `pocket-agent fill` or the app's buttons. Don't call `fill_form()` or `run_demo()` from a script and call that a user-path proof.
- Capture both the action and the resulting state. For a fill, that is the report or `result.json` plus a screenshot or the filled list. Don't capture only "exit 0".
- Check side effects: `submits == 0` (nothing submitted), `forms.sqlite` keys after a CLI fill, and the app's history (`#runList`) and **Answers saved from your fills** after an app fill.
- `--model none` makes no AI calls. The report's `ai.attempted` must be `0`. Verify that number rather than trusting the flag.
- CLI exit codes are 0 for Review, 2 for incomplete unattended runs (blocked, stopped, or unresolved), and 1 for an execution error or a detected submission. A `2` is a real result to report, not a harness failure.

## Cleanup

```bash
$S/cleanup.sh "$RUN"                # stops only the pids in $RUN/*.pid; keeps evidence/ and logs/
$S/cleanup.sh "$RUN" --purge-home   # also deletes the isolated home (profile, sqlite)
```

Never `pkill python` or kill by name. The user may have their own `pocket-agent ui` on 8000. Run cleanup after every failed attempt too. Run dirs live under `.pytest_cache/` (gitignored), so `pytest --cache-clear` deletes old evidence. Copy anything you need to keep first.

## Feature map

See `features/README.md`. A proof of a feature that lists several entry points (CLI and app) is incomplete if it drives only one of them.
