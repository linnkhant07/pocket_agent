# Offline demo

`docs/DEMO.md` documents `pocket-agent demo [--headless --runs N --out PATH]`. It fills the fictional `prototype-demo` form with a seeded Jordan Hale profile and checks DOM values. A failing run exits 2.

## Sub-features
- A headed run that waits for the window to close.
- Headless rehearsal over N runs, with a JSON results file.

## How to get to it (user POV)
`uv run pocket-agent demo --headless --runs 3 --out .pytest_cache/demo-results.json`

## Driving it with the scripts
```bash
RUN=$($S/launch.sh); source "$RUN/env"
uv run pocket-agent demo --headless --runs 1 --out "$RUN/evidence/demo/demo-results.json"; echo "exit=$?"
```
Proof: `demo-results.json` has every run with `passed: true`, `status: "review"`, no questions, and `submits: 0`.

## Gotchas
- As of 2026-11-15, `demo` is **not registered** in `cli.py`, so the command fails with `invalid choice: 'demo'`. `src/pocket_agent/demo.py` exists but is untracked. Report this gap.
- Calling `run_demo()` directly also returns `passed: false` with `status: "stopped"`. That is the same review-detection gap described in [cli-fill.md](cli-fill.md).
