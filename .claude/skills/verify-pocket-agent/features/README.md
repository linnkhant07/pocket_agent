# Pocket Agent feature map

Each file covers one user-facing feature: how a user reaches it, how to drive it with this skill's scripts, and what end state proves it.

| Feature | File | Entry points |
| --- | --- | --- |
| Fill an application (CLI) | [cli-fill.md](cli-fill.md) | `pocket-agent fill URL` |
| Fill an application (app) | [desk-fill.md](desk-fill.md) | App Apply section |
| Profile, resume, and saved answers | [answers-profile.md](answers-profile.md) | App Resume, Missing details, and Answers sections; `profile.yaml` `form_answers` |
| AI provider settings | [settings.md](settings.md) | App AI drafting select, `fill --model` |
| Offline demo | [demo.md](demo.md) | `pocket-agent demo` (documented in docs/DEMO.md) |

Not yet mapped: job search and tracker (`fetch`, `search`, `score`, `track`), the MCP server (`pocket-agent mcp`), `eval-forms`, and Workday account handling (`--workday-accounts`, which needs live Keychain credentials).
