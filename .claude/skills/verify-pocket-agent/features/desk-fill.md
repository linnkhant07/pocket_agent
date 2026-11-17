# Fill an application (app)

The Apply section of `pocket-agent ui`. The user pastes a link and clicks Fill. A Chromium window fills the form from the signed-in account, questions appear in an ask card in the tab, and the fill ends with a `done` event. The filler's window stays open for a manual Submit.

## Sub-features
- Start a fill from `#jobUrl` and `#fill`. A second Fill while one runs gets an error event.
- Ask card (`#askCard`). A select, a date input, a textarea, or a text input, then `#askSend` or `#askSkip`. Sensitive questions also show `#askRemember` ("Always use this answer"), off by default.
- Live log (`#fillLog`) of `status` and `error` events.
- Result (`#fillResult[data-status]`), one of `review`, `blocked`, or `stopped`.
- Continue on this page (`#continueFill`) after a `blocked` result, which resumes on the open page.
- History (`#runList`) gets the run, and non-sensitive answers appear under **Answers saved from your fills**.

## How to get to it (user POV)
Run `uv run --env-file .env pocket-agent ui`, sign in with Google once, paste the application link under Apply, and click Fill.

## Driving it with the scripts
```bash
RUN=$($S/launch.sh --ui); source "$RUN/env"
uv run python $S/drive_ui.py prototype-demo              # two questions, filler ends on the Review step
uv run python $S/drive_ui.py prototype-demo              # again: no questions, the answers were saved
uv run python $S/drive_ui.py hd-eeo                      # sensitive questions, nothing saved without consent
uv run python $S/drive_ui.py hd-login --expect blocked   # sign-in gate
```
Proof: `evidence/ui-<id>/result.json` has the expected `status`, `submits: 0`, and `filler_step` (for example `Review`). Check `04-filler.png` and `03-end.png` as well.

## Gotchas
- The account is the fake from `tests/account_fakes.py`. Real Supabase needs a real Google sign-in, which a verification run cannot do.
- Fictional forms report `stopped` at their last step, because their progress line never says "Review". Use `filler_step` to see where the filler stopped.
- The app keeps one filler browser across fills, and each Fill opens a new tab in it.
