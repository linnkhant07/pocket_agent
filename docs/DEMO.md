# First prototype demonstration

The supported boundary is filling and navigating to Review. Submission stays manual.
Live employers can still require MFA, email verification, CAPTCHA, or missing personal facts.

## Repeatable offline demonstration

Install dependencies once:

```bash
uv sync --extra dev --extra accounts
uv run playwright install chromium
```

Run the local fictional scenario (no employer website, credentials, model, or personal profile needed):

```bash
uv run pocket-agent demo
```

It fills contact information, referral source, phone type, uploads a fictional resume,
fills education and repeated work history, and stops at Review. Close the window to exit.
It does not demonstrate live Workday authentication.

Rehearse three fresh launches without a visible browser:

```bash
uv run pocket-agent demo --headless --runs 3 --out .pytest_cache/demo-results.json
```

Each run must report `passed: true`, `status: review`, no questions, no unresolved fields,
and zero submission clicks. A failing run returns exit code 2.

## Personal live demonstration

Keep personal profile and resume files excluded from Git. Configure the actual PDF in
`~/.pocket-agent/config.yaml`:

```yaml
resume_path: /absolute/path/to/your/resume.pdf
```

This setting is shared by the CLI and UI. `--resume PATH` overrides it for a CLI run.
A UI resume upload updates the shared setting.

Use an existing verified account for the live demo:

```bash
uv run --extra accounts pocket-agent fill 'WORKDAY_JOB_URL' --model none --workday-accounts --unattended
```

The browser remains open for inspection. Read `~/.pocket-agent/last-fill-report.json`:

- `review`: the final/review step is reached without known missing required fields or validation errors.
- `blocked`: a gate, missing required answer, validation error, or execution error remains.
- `stopped`: review was not established; do not present this as completion.

Exit codes: 0 for an unattended review, 2 for an incomplete unattended run without an execution error,
1 for execution errors or detected submission. Add `--close-on-finish` to exit without waiting for the
browser window to close; `--headless` also exits automatically.

Reports include the selected resume path, missing questions, and visible validation errors.
A local blocker screenshot is saved when possible. Reports and screenshots may contain personal
information and must not be committed or shared unredacted.

Rehearse the exact live job and inspect all values before claiming live compatibility. Keep the
local demo and a recording available as fallback; simulation success does not establish live success.

## Pause for help, then resume the real application

Use `--interactive` when you want automation to pause only when it needs you:

```bash
uv run --extra accounts pocket-agent fill 'WORKDAY_JOB_URL' --model none --workday-accounts --interactive
```

After a valid field answer, automation continues immediately. Rejected nonempty answers
are requested again (up to three attempts); rejected values are not saved as reusable answers.
The page is read again after each answer so re-rendered controls do not reuse stale references.

After an account gate or form/navigation error, correct the issue in the same browser and
press Enter to resume. The current page is reused without loading the job URL again, and
valid existing field values are preserved. Type `q` at the recovery prompt to stop automation.
In the UI, correct the page and press Continue after a blocker.

`--unattended` still means no terminal questions. It reports unresolved blockers and leaves
the browser available for inspection; use interactive mode for the pause-and-resume workflow.

## AI answers for unfamiliar questions

The answer generator uses local Ollama independently of the optional GGUF field matcher:

```bash
uv run --extra accounts pocket-agent fill 'JOB_URL' --model none --ai-answers --answer-model qwen3.5:4b --interactive
```

Add `--workday-accounts` only for supported Workday URLs. Ollama must be running with the named model installed.
In UI Settings, enable **Use local AI for unfamiliar questions and written answers** and enter the Ollama model name.

Rules and explicit saved answers run first. The fallback can draft required written responses using profile,
resume, and current page context, select directly supported facts, and answer Yes about an explicitly listed
skill. It abstains on unsupported claims and personal decisions such as salary, eligibility, consent, or availability.
A failed model request or invalid response falls back to the existing user question/resume workflow.
Optional unanswered fields retain the existing skip behavior.

Generated values are checked against browser validation. CLI reports include `ai_answers` with source references.
They are not saved into reusable answer memory. Evidence references and output checks reduce mistakes but do
not prove that every generated sentence is accurate; review the final form, particularly generated prose.
This feature changes question answering, not browser navigation or final submission policy.
