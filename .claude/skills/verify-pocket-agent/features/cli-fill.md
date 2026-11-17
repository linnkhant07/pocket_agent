# Fill an application (CLI)

`pocket-agent fill URL` opens Chromium, fills known facts from the profile, resume, and saved answers, uploads the resume, and advances through Next/Continue. It stops before Submit.

## Sub-features
- Unattended mode (`--unattended`): never prompts. It writes `$POCKET_AGENT_HOME/last-fill-report.json`.
- Interactive mode (`--interactive`): prompts in the terminal, and `/skip` leaves a field blank.
- Resume upload, including the `--resume PATH` override.
- Repeated work-history blocks.
- Recovery prompt after a gate or error. Press Enter to resume or type `q` to quit.

## How to get to it (user POV)
In a terminal: `uv run pocket-agent fill 'APPLICATION_URL' --model none --interactive`.

## Driving it with the scripts
```bash
RUN=$($S/launch.sh); source "$RUN/env"
printf 'form_answers:\n  phone_device_type: Mobile\n  how_did_you_hear: "Internet - LinkedIn"\n' >> "$POCKET_AGENT_HOME/profile.yaml"
$S/fill_cli.sh "$RUN" prototype-demo
```
Proof: `evidence/cli-prototype-demo/stdout.txt` lists `filled` for First Name, Last Name, Email, Phone, Phone Device Type, How Did You Hear, Resume, School, Degree, Field of Study, and each Job Title, Company, and Job Description. It shows `skipped Phone Extension`. The report has `unresolved: []`, `submits: 0`, and `ai.attempted: 0`, and `status` is `review`.

For interactive mode, run `pocket-agent fill ... --interactive --headless` in a tmux/PTY session, and answer or `/skip` at each `> (type /skip ...)` prompt.

## Gotchas
- Without the two `form_answers`, the fictional init profile cannot answer Phone Device Type or How Did You Hear. The run then ends with those labels in `unresolved` and exit 2. That is correct behavior, not a harness bug.
- As of 2026-11-15, the demo form ends with `status: "stopped"` and `current_step: "Step 4 of 4"` even when everything is filled. The engine only reports `review` when the progress text contains "review" (`forms/engine.py`), and the rendered form says "Step N of 4". `tests/test_demo_readiness.py` also fails in that working tree. Report this rather than accepting `stopped` as success.
- The non-headless, non-unattended CLI waits for Enter or for the browser window to close. Always pass `--headless` or `--close-on-finish` when driving it from an agent.
