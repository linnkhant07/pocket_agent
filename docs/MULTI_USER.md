# Pocket Agent accounts

Pocket Agent keeps each person's profile, PDF resume, answers, and fill history in a Supabase account. Everything else runs on your own computer. One app does it all.

## Start the app

Install the project with Keychain support on macOS, then start the app from the folder that holds your `.env`:

```sh
uv sync --extra accounts
uv run playwright install chromium
uv run --env-file .env pocket-agent ui
```

With an installed copy, `pocket-agent ui` works the same way. The `.env` file needs `SUPABASE_URL` and the public `SUPABASE_ANON_KEY`. After the first sign-in the app remembers the project in `~/.pocket-agent/config.yaml`, so later starts work from any folder.

A tab opens at `http://127.0.0.1:8000`. Then:

1. Click **Continue with Google** and pick your account. You do this once per computer.
2. Upload your PDF resume. The app fills your profile from it.
3. Fill in only the details the resume did not cover, and answer any optional questions. Answers save as you type.
4. Paste a job application link under **Apply** and click **Fill**.

A Chromium window opens on your computer and fills the form with your account data. When the form asks something your account cannot answer, the question appears in the app tab. Answer it there and the fill continues. The fill stops before Submit. Review the form in the Chromium window and submit it yourself.

The app listens only on `127.0.0.1`. Keep the default port, because Google returns to `http://127.0.0.1:8000/auth/callback` and Supabase accepts only callback URLs on its allowlist.

## One sign-in for the app and the terminal

Your session is one refresh token in the macOS Keychain. The app and the terminal share it.

- Reloading the tab keeps you signed in. On load the tab asks the app for a fresh access token, and the app makes one from the Keychain session.
- If you already ran `pocket-agent login`, the app opens signed in.
- After you sign in through the app, `pocket-agent fill URL --account` works in the terminal without another sign-in.
- **Sign out** in the app revokes the session and deletes the Keychain entry, which signs the terminal out too. `pocket-agent logout` does the same from the terminal.

The browser tab keeps only a short-lived access token in memory. The refresh token never reaches the page.

## Resume-first onboarding

After sign-in, the first step is **Upload**. Choosing a PDF uploads it right away and fills your profile. You do not need a profile beforehand. The app extracts details from the PDF and saves a partial draft through the same versioned profile RPC and private resume storage that profile edits use. The form then hides fields the resume already answered. **View extracted details** lists everything imported, and **Edit extracted details** shows those fields for correction.

Extraction reads the PDF with `pypdf` and structures the text with an Ollama model on your computer. No paid parser API or hosted AI key is involved. `POCKET_AGENT_MODEL` and `POCKET_AGENT_BASE_URL` choose the model and server. The defaults are `qwen3.5:4b` and `http://127.0.0.1:11434`. Pull the model once with `ollama pull qwen3.5:4b`. If Ollama is not running, the app still imports contact details and warns that extraction was partial.

Every extracted value must quote the resume and map to an allowlisted profile path. The importer fills name, contact details, location, education, jobs and their accomplishments, skills, and links. It fills US work authorization or sponsorship only from a direct statement such as "Authorized to work in the United States". It never infers them from nationality, school, employer, or location. Demographic answers stay voluntary, and the importer never fills them. Fills refuse to start until the profile is complete.

Uploading a newer resume changes only fields you have not edited. Each import records the values it wrote in `onboarding.extracted_values`, and the next import overwrites a field only when it is empty or still holds that value. The app deletes the replaced PDF from storage. If the profile save fails, it deletes the PDF it just uploaded.

The app accepts text-based PDFs up to 10 MiB. It reads the first 40,000 characters and keeps at most 300 facts.

## Filling an application

Each fill takes a fresh copy of your account. The app downloads your profile, answers, and resume into a private temporary folder with mode 0700. The filler works only from that folder. When the fill ends, the app saves the answers you typed, records the run in your history, and deletes the folder.

Questions you answer during a fill follow the same rules as the terminal.

- A non-sensitive answer is saved to your account and reused on later applications.
- A sensitive answer, such as a demographic question, shows an **Always use this answer** checkbox. It is off by default. With it off, the answer fills this form only and is not saved.
- **Skip** leaves the field empty.

Answers saved this way appear under **Answers saved from your fills**, where you can edit or clear them.

Some pages need you, such as a sign-in or a CAPTCHA. The fill then ends as blocked. Finish that step in the Chromium window and click **Continue on this page**. Only one fill runs at a time.

**AI drafting** chooses Off, GPT, or Claude for written answers and salary questions. The app remembers your choice in this browser. Choosing GPT or Claude shows fields for your own API key and a model ID from your provider account. The key is saved in the macOS Keychain and never shown again, and the model ID goes in `~/.pocket-agent/config.yaml`. The terminal's `fill --model gpt` or `--model claude` uses the same saved settings. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `POCKET_AGENT_AI_MODEL` in the environment still take precedence. Your resume and profile then go from your computer to that provider.

The terminal path still exists. `pocket-agent fill URL --account --model none` runs the same account fill and asks missing questions in the terminal. Plain `pocket-agent fill URL` uses your local `~/.pocket-agent` files and never touches the account.

## Sign-in

Follow the [Google setup guide](GOOGLE_SIGN_IN.md) once. It creates a Google OAuth web client, enables the Supabase Google provider, and allows the app and terminal callback URLs.

Google is the only sign-in method. Password and email-link sign-in were removed for a security reason. With "Confirm email" off, someone could register a password account under another person's email, and Supabase's automatic identity linking would later attach that person's Google sign-in to it. Keep **Confirm email** on in Supabase so no unverified email account can exist.

During the redirect the tab keeps the PKCE verifier and a random state in its own `sessionStorage` and deletes them on return. It removes the code from the address bar before the app exchanges it. Each sign-in uses a fresh verifier and state, so a returned code can only finish the sign-in that started it. `pocket-agent login` runs the same flow with its own loopback listener on port 8766.

**Delete my data** removes your profile, every stored resume PDF, saved answers, and fill history. Only the project owner can remove the Supabase Auth identity itself, from the dashboard.

## Local server safety

Any website you visit can send requests to `127.0.0.1`, and this server can act with your session. It defends against that in three ways.

- It rejects any request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>`. That stops DNS rebinding, where a hostile site points its own name at your computer.
- It rejects any websocket whose `Origin` is not the app's own origin. Browsers let any site open a websocket to any address, so this check is what keeps other sites from starting fills.
- `/api/session` and every request that changes data require a same-origin `Origin` header or `Sec-Fetch-Site: same-origin`.

Every response carries `Cache-Control: no-store` and a Content Security Policy that allows only the app's own scripts.

## Set up a Supabase project

1. Create a dedicated Supabase project, enable the Google provider with Confirm email on, and apply [the migration](../supabase/migrations/202611160001_accounts.sql). See [database setup and checks](../supabase/README.md).
2. Put `SUPABASE_URL` and the public `SUPABASE_ANON_KEY` in `.env`. Never use a service-role key.
3. Follow the [Google setup guide](GOOGLE_SIGN_IN.md).

There is no hosted deployment. Each person runs the app on their own computer against the shared Supabase project.

## Data ownership and module map

- `account/client.py` talks to Supabase Auth, PostgREST, and private Storage. It caps PDF downloads at 10 MiB and never passes Supabase error text to the browser.
- `account/auth.py` and `account/oauth.py` handle Google sign-in, token refresh, identity checks, the shared Keychain session, and sign-out.
- `account/runner.py` validates the account data, applies the sensitive-answer consent rules, and owns the fill lifecycle. Its `account_fill` context manager builds the temporary folder, and on exit it saves reusable answers, records history, and deletes the folder. The terminal and the app both use it.
- `account/resume_import.py` extracts quoted facts with pypdf and Ollama and merges them into the draft profile.
- `account/questions.py` lists the questionnaire keys and labels. Sensitivity comes from the existing form policy.
- `ui/app.py` serves the app and its API on `127.0.0.1` and applies the local server checks above.
- `ui/desk.py` runs fills. It is a small state machine (idle, filling, asking) that owns the Chromium window and sends `status`, `ask`, `error`, and `done` events to the tab over a websocket.
- `supabase/` holds the migration and SQL tests that use real database roles. RLS limits every account table to its owner, and the resume bucket is private.

The account is the source of truth for the profile and answers. Profile saves send the revision they started from, and the database rejects a save if someone else saved first. Each answer save replaces that answer's row, so the later of two edits wins. A fill rebuilds its working memory from the account every time, so stale facts and consent never carry over between fills.

Run history stores the URL, the stopping status (`review`, `blocked`, or `stopped`), and the labels of unanswered questions. The app shows one row per application, with its latest result and how many times you filled it. It never claims the application was submitted, and it does not upload field values, screenshots, or errors. If the network fails at the end of a fill, the app prints a warning in the terminal that started it, and nothing retries later.

## What has been verified

`uv run pytest tests/test_ui.py tests/test_ui_e2e.py tests/test_account.py tests/test_google_auth.py tests/test_resume_import.py` runs the Python tests. `node tests/test_portal_google.mjs` runs the browser-side tests. `tests/test_ui_e2e.py` starts the real app, restores a signed-in session, fills the fictional demo form in a real Chromium, answers its questions in the tab, and checks the recorded history and saved answers. These tests use an in-memory Supabase and Keychain, so they cannot prove RLS, Storage limits, or live Google sign-in. Before relying on a project, run `supabase/tests/accounts_rls.sql` against a disposable Supabase database, complete a real Google sign-in, and run one real fill.

Two older problems remain outside this feature. Nine tests in `tests/test_fill_recovery.py` fail against the current reader and engine. The submit-guard checker in `.claude/skills/verify-submit-guard` reports 29 known gaps involving unusual button labels, final-page headings, and step formats. Fix those before treating the filler as production-ready.
