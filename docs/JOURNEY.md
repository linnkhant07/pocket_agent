# Pocket Agent, build journal

Times are America/Los_Angeles (PST in November). Nothing here is a guessed metric. If a number is in this file, it was measured on this machine or read from a response header, a model card, or `git log`.

The project is a fully local recruiting assistant. The longer bet is to distill a cloud agent into a small model that runs on a laptop, Apple Silicon first, and to publish the whole thing under MIT. The assistant finds public jobs, scores them against a local profile, tracks applications, and drafts answers. It never submits an application.

## 2026-11-03 16:20 PST — empty repo, written plan

The session started from an empty git repo whose only commit was `Initialize project` (16:20 PST). The source of truth was a short plan: four skills (find, screen, track, prefill), an OpenAI-compatible local backend, model tiers by RAM, a small Python harness, an MCP server, and an eval with programmatic checks. Week 1 was the working slice. Weeks 2–4 are synthetic profiles, teacher traces, LoRA, and an optional GRPO run. Training on real user data is out of scope.

The first decision was the layout. One installable Python package, `pocket-agent`, with the CLI of the same name. User files go in `~/.pocket-agent/`. The repository ships a fictional person, Jordan Hale at `jordan.hale@example.com`, phone `+1-555-0100`, Northwind University. A canary string, `POCKET-AGENT-CANARY-7f3a`, is written into profiles and traces so a real resume cannot be mixed into training data by accident.

## 2026-11-03 16:22 PST — which job feeds, and which ones actually answered

Public job APIs rot. Before writing a parser, the endpoints were requested for real.

- Greenhouse `https://boards-api.greenhouse.io/v1/boards/stripe/jobs` returned a JSON document with a `jobs` array (745 jobs in that pull).
- Lever `https://api.lever.co/v0/postings/netflix?mode=json` returned **404**. Spotify returned **200** at 00:22:10 GMT (`Content-Length: 820002`). Palantir also returned 200. Plaid, Rippling, Anduril, Shopify, Discord, and Figma returned 404. Company slugs are configuration, not something the client should guess.
- SimplifyJobs publishes `.github/scripts/listings.json` on the `dev` branch. A ranged GET of `SimplifyJobs/New-Grad-Positions` showed the object shape: `id`, `company_name`, `title`, `active`, `locations`, `url`, `sponsorship`, `degrees`, `date_posted`. A full download of that file was 13 MB and 19,044 rows, 3,115 of them `active`. `SimplifyJobs/Summer2027-Internships` has the same shape plus a `terms` array. The Summer 2026 repo is off-season. The live internship list is Summer 2027.

At 16:23 PST the feeds were narrowed: those two Simplify repos are the default, and `listings.json` is preferred over the README. Greenhouse and Lever stay optional, one company board at a time. That matches how people already use the Simplify lists, and it avoids pretending every ATS slug is stable.

An early read of `listings.json` through a web fetcher died at 5,000,000 bytes with `Unterminated string` (line 184800). The file is larger than that tool's cap. `curl` to disk got the full 13 MB. The client now downloads the file itself, with a six-hour cache, a one-second gap per host, and a normal User-Agent.

README parsing stayed as a fallback. The current Simplify README is an HTML table, not a pipe table, so both are supported. Sponsorship values seen in the new-grad file were mostly `Other` (18,967), plus `U.S. Citizenship is Required` (55), `Does Not Offer Sponsorship` (14), and `Offers Sponsorship` (8).

## Model tiers, checked rather than remembered

On 2026-11-04 the Ollama library pages for these tags returned HTTP 200: `qwen3:0.6b`, `qwen3:1.7b`, `qwen3:8b`, `qwen3:8b-q8_0`, `qwen3:14b`, `qwen3.5:4b`. The Hugging Face LICENSE files for `Qwen/Qwen3-0.6B`, `Qwen/Qwen3-1.7B`, `Qwen/Qwen3-14B`, and `Qwen/Qwen3.5-4B` start with the Apache License 2.0 text. LFM2.5 was named in the plan as an 8 GB alternative and was not pinned, because those Qwen tags were the ones whose license was actually opened.

`pocket-agent init` maps detected RAM at 12 GB and 24 GB:

| Detected RAM | Tier | Tag |
| --- | --- | --- |
| under 12 GB | 8 GB | `qwen3:1.7b` |
| 12 GB up to 24 GB | 16 GB | `qwen3.5:4b` |
| 24 GB and above | 32 GB | `qwen3:14b` |

This VM reported 15.6 GB, so init selected `qwen3.5:4b`. The CPU smoke model is `qwen3:0.6b` (about 523 MB on the Ollama page, 522,653,767 bytes once pulled). The 4B default was not pulled here. This box is a 4-core CPU with no GPU, which the Ollama installer also reported (`Unable to detect NVIDIA/AMD GPU`).

## 2026-11-03 16:41 PST — Week 1 is a package

Commit `Add Pocket Agent Week 1` landed at 16:41 PST. It has the CLI, profile schema, SQLite tracker with CSV export, the feed clients, nine tools, a newline-delimited MCP server, the agent loop, 50 hand-checked tasks, and GitHub Actions for Ubuntu and macOS. There is no `submit_application` tool. `db_upsert` only writes a local status. A unit test rejects a status of `submit`.

The mock suite, `pocket-agent eval --suite week1 --backend mock`, scored 50/50. Tool-call accuracy 1.0, JSON validity 1.0, restraint 1.0 on 18 tasks, latency 0.0 s because no model runs, peak RSS of that process 31.9 MB. `pytest` at that point was 16 passed, then 17 after an Ollama client test.

`pocket-agent synth --count 300 --seed 428` wrote `data/synthetic/profiles.jsonl` (300 lines, 336,549 bytes). Every row is `@example.com`, carries the canary, and leaves salary empty.

## Ollama, the empty `content` field, and a 0.6B model that copied the prompt

Installing Ollama needed `zstd` (`apt-get install zstd`). The install script then warned that systemd is not running, so the server was started by hand with `ollama serve`. `ollama pull qwen3:0.6b` finished, 522 MB of weights.

A call to `POST /v1/chat/completions` with `"think": false` came back in about 3.6 seconds with `content` equal to `""`, `completion_tokens` 120, and the actual text in `reasoning`. The same request on native `POST /api/chat` with `"think": false` returned `{"action":"final","answer":"hello"}` in `content`, `eval_count` 10, `eval_duration` 139528000 nanoseconds. Chat-template flags on the OpenAI shim (`chat_template_kwargs.enable_thinking`, `/no_think`) still left `content` empty. The CLI therefore talks to Ollama through `/api/chat`. llama.cpp and MLX stay on `/v1/chat/completions`.

Live `pocket-agent fetch` the same evening:

- Greenhouse `stripe`, limit 2: fetched 2, inserted 1, 1 duplicate.
- Lever `spotify`, limit 2: inserted 2.
- Simplify new-grad, limit 3, newest active rows: AMAX (Junior System Engineer, Fremont), Apple (Silicon Photonics Process Integration Engineer, Cupertino), Keysight (IC Test Engineer, Santa Rosa).

A frozen snapshot, `data/snapshots/public_postings.jsonl`, keeps the 1,000 newest active rows from each Simplify list. After dedupe that is **1,857** jobs (938 new-grad, 919 intern). Posted dates in the file run from **2026-10-05** through **2026-11-03**. The first version of `--limit` kept whatever came first in the file. It was changed, before this snapshot was written, to sort by `posted_at` descending. In this slice, `citizenship_required` and `clearance_required` are both 0, and every row's sponsorship is `other`. Degree lists are populated: 88 rows are PhD only. Eligibility traps for this snapshot have to use degree and graduation timing, not clearance flags that are not in the file.

The first CPU smoke, four tasks, still had the model echoing format placeholders (`<text>`, `<question>`). Score: **0/4** task success, tool-call accuracy 0.50, JSON validity 1.0, restraint 0.0, latency p50/p95 **0.383 s / 0.717 s**, eval-process peak RSS 36.4 MB. Sampling RSS by the process name `ollama` showed 41.7 MB. That number is the parent. The weights were in a child named `llama-server` at about 1.07 GB RSS (`1095472` kB in `/proc`). The eval reporter was changed to record that child. The placeholder text was removed from the prompt and replaced with a concrete sentence.

The full 50-task run after that change, label `CPU smoke test`, model `qwen3:0.6b`:

| | |
| --- | --- |
| Task success | **2/50 (0.04)** |
| Tool-call accuracy | 0.40 |
| JSON validity | 1.0 |
| Restraint | 2/18 (0.111) |
| Latency p50 / p95 | 0.449 s / 0.622 s |
| Eval process peak RSS | 39.2 MB |
| llama-server RSS | 1112.5 MB |
| Completion tokens | 1080 |
| Tokens/s | 45.1 |

The two passes were ASK_USER turns: salary expectations, and notice period. JSON parsed every time. The usual wrong answer was the example sentence that had just been added to the prompt: "Contoso Robotics is an eligible new-grad match." The model said that and called no tool. Putting a fluent example in the prompt taught a 0.6B model to copy it. That is the failure the next harness change is aimed at. The 2/50 table above is the before number.

## 2026-11-03 16:46 PST — commits, identity, and a push that did not happen

Five commits at 16:46 PST record the native Ollama client, newest-first limits, llama-server RSS, the snapshot, and the baseline in the README. `git config` on this clone is `Linn Khant Thuya <68958137+linnkhant07@users.noreply.github.com>`. The two earlier commits were rewritten to the same author and committer before any push, so a future GitHub history is not attributed to the agent account. `git log` shows that name on every commit.

`git push -u github main`, remote `https://github.com/linnkhant07/pocket_agent.git`, failed both times with:

```text
fatal: could not read Username for 'https://github.com': No such device or address
```

No GitHub token was created to get around that. `origin` was not force-pushed. It still points at the previous history, so it has diverged from this rewritten `main`.

## 2026-11-03 16:47 PST — a bundle, because the push was blocked

`git bundle create` of `main` at `cb2c0ff` produced a 325 KB file. `git bundle verify` said the bundle is okay and contains a complete history. Paths:

- `/opt/cursor/artifacts/pocket_agent.bundle`
- `/cursor/stores/self/artifacts/pocket_agent.bundle`

Those are the same file. `/opt/cursor/artifacts` is a symlink to the agent store artifacts directory. The frozen snapshot did not push the bundle over 25 MB. It is 325 KB.

Later entries will record the harness change aimed at the copied example sentence, the 0.6B rerun, a 1.7B run if this VM can hold it, the held-out task set, and the teacher-trace script.
