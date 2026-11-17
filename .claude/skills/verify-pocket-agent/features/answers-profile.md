# Profile, resume, and saved answers

The app's Resume, Missing details, and Answers sections. They upload a PDF resume that fills the account profile, edit what the resume missed, and autosave questionnaire answers. Answers learned during fills appear under **Answers saved from your fills**. The CLI user of plain `pocket-agent fill` edits `~/.pocket-agent/profile.yaml` instead, with reusable answers under `form_answers`.

## Sub-features
- Upload a resume: `#resumeFile`, which calls `POST /api/account/resume`. Extraction needs a local Ollama model. Without one, only contact details import, with a warning.
- Save missing details: `#profileForm` submit, which calls `PUT /api/account/profile`.
- Answers: each input in `#questionList` autosaves with `PUT /api/account/answers/{key}`, and clearing it calls `DELETE`.

## How to get to it (user POV)
Open the app, sign in, and scroll to Resume, Missing details, or Answers.

## Driving it with the scripts
Launch with `--ui`. The fake account starts with a complete profile, so Missing details is mostly hidden. Type into an input in `#questionList`, blur it, and reload the page.
Proof: after a reload the value is still there (it lives in the fake account until the app stops). After an app fill that asked a non-sensitive question, that question appears under **Answers saved from your fills**.

## Gotchas
- Resume upload goes through the real extractor. Without Ollama it reports a partial import, which is expected in a verification run.
- The fake account forgets everything when the app process stops.
