# AI provider settings

This feature chooses GPT, Claude, or Off for AI-drafted answers. In the app it is the **AI drafting** select (`#model`) in the Apply section, remembered in the browser's localStorage as `pocketModel`. Choosing GPT or Claude shows `#aiKey`, `#aiModel`, `#aiSave`, and `#aiForget`. The key goes to Keychain through `PUT /api/ai/{provider}` and is never returned. On the CLI it is `fill --model none|gpt|claude`, which reads the same saved key and model. Environment variables take precedence.

## Sub-features
- `#model` choice, sent as `model` in the websocket `start` message.

## How to get to it (user POV)
Open the app and pick a provider next to Fill, or pass `--model` on the CLI.

## Driving it with the scripts
Launch with `--ui` and pick `none` in `#model` (the default). Reload and confirm the choice stays.
Proof: with `none`, the fill log shows no `[AI]` summary line and no provider call is made. With `gpt` selected and no key saved, Fill is refused with a notice. Saving a fake key and model shows "OpenAI is ready.", `GET $UI/api/ai` reports `key_saved: true`, and the response never contains the key. In the skill's app the key lands in the fake Keychain, never the real one.

## Gotchas
- Verifying `gpt` or `claude` sends applicant facts to a paid external API. Don't do that in a verification run without the user's explicit approval and their own key.
- With `--model gpt` (the CLI default) and no key, `fill` fails with a configuration error. Always pass `--model none` unless you are testing that error.
