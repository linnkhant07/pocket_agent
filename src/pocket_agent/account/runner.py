"""Prepare a private, disposable account snapshot for the existing local filler.

No local single-user profile, memory, resume, or config can leak into this run.
Network failure fails closed before a fill. After the fill, answers the user typed during it
are saved to the account when the filler may reuse them; sensitive answers only with explicit
always-use consent, exactly as local reuse works.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

from pocket_agent.account.client import AccountClient, AccountError
from pocket_agent.account.profile import missing_profile_fields, validate_account_profile
from pocket_agent.account.questions import questions

QUESTIONNAIRE_KEYS = frozenset(question["key"] for question in questions())
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import is_sensitive_key
from pocket_agent.profile import dump_profile


def snapshot_profile(snapshot: dict) -> tuple[dict, str]:
    row = snapshot.get("profile")
    if not row or not isinstance(row.get("data"), dict):
        raise AccountError("Upload your resume and finish your profile in Pocket Agent before filling.", 422)
    profile = deepcopy(row["data"])
    # Only the separate canonical answers table can supply questionnaire answers.
    profile.pop("form_answers", None)
    resume_object = profile.pop("resume_object", "")
    if not resume_object:
        raise AccountError("Upload a resume in Pocket Agent before filling.", 422)
    profile = validate_account_profile(profile, complete=False)
    if missing_profile_fields(profile):
        raise AccountError("Finish the missing details below your uploaded resume before starting a fill.", 422)
    profile = validate_account_profile(profile)
    answers = {}
    for answer in snapshot.get("answers", []):
        key, value = answer.get("key"), answer.get("value")
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            continue
        consent = answer.get("always_use") is True
        if is_sensitive_key(key) and not consent:
            continue
        # A fill answer's label scopes it to that wording. A questionnaire answer is for the question type,
        # whatever a form calls it, so it carries no label.
        label = "" if key in QUESTIONNAIRE_KEYS else answer.get("label") or ""
        answers[key] = {"value": value, "always_use": consent, "label": label}
    profile["form_answers"] = answers
    return profile, resume_object


def client_answers(snapshot: dict) -> dict[str, tuple[str, bool]]:
    return {a["key"]: (a.get("value"), a.get("always_use") is True)
            for a in snapshot.get("answers", []) if isinstance(a, dict) and isinstance(a.get("key"), str)}


def learned_answers(memory_path: Path) -> list[tuple[str, str, bool, str]]:
    """Answers typed during this fill that the filler itself would reuse next time."""
    if not memory_path.exists():
        return []
    memory = Memory(memory_path)
    try:
        reusable = memory.reusable_keys()
        labels = {}
        for label, key in memory.learned_labels().items():
            labels.setdefault(key, label)
        return [(e.key, e.value, e.always_use, labels.get(e.key, "")[:2000]) for e in memory.all_entries()
                if e.source == "answer" and e.key in reusable and e.value.strip()
                and 1 <= len(e.key) <= 200 and len(e.value) <= 20000]
    finally:
        memory.close()


def sync_learned_answers(client: AccountClient, memory_path: Path, known: dict[str, tuple[str, bool]]) -> tuple[int, str]:
    saved = 0
    try:
        for key, value, always_use, label in learned_answers(memory_path):
            if known.get(key) == (value, always_use):
                continue
            client.save_answer(key, value, always_use, label)
            saved += 1
    except AccountError as exc:
        print("Some answers from this fill could not be saved to your account. You may be asked again next time.")
        return saved, str(exc)
    if saved:
        print(f"Saved {saved} new answer(s) to your account for future applications.")
    return saved, ""


def sweep_old_account_folders(max_age_seconds: int = 3600) -> None:
    """Remove account snapshots left behind when a fill was killed. The live folder is newer than this."""
    import shutil
    import tempfile
    import time

    root = Path(tempfile.gettempdir())
    now = time.time()
    for path in root.glob("pocket-account-*"):
        try:
            if path.is_dir() and now - path.stat().st_mtime > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def require_https(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AccountError("Account fills require an HTTPS job application URL.", 422)


@dataclass
class AccountFill:
    """One account fill's private folder. The filler sets status and unresolved before the context exits."""
    home: Path
    status: str = "stopped"
    unresolved: list[str] = field(default_factory=list)
    sync_error: str = ""

    @property
    def resume(self) -> Path:
        return self.home / "resume.pdf"


@contextmanager
def account_fill(client: AccountClient, url: str) -> Iterator[AccountFill]:
    """Snapshot the account into a 0700 temp home; on exit sync answers, record the run, delete the folder."""
    require_https(url)
    sweep_old_account_folders()
    snapshot = client.snapshot()
    profile, resume_object = snapshot_profile(snapshot)
    content = client.download_resume(resume_object)
    import signal

    with TemporaryDirectory(prefix="pocket-account-") as directory:
        home = Path(directory)
        os.chmod(home, 0o700)
        profile_path = home / "profile.yaml"
        dump_profile(profile_path, profile)
        resume_path = home / "resume.pdf"
        resume_path.write_bytes(content)
        for path in (profile_path, resume_path):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        run = AccountFill(home)
        previous = None
        try:
            previous = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
        except (ValueError, OSError):
            previous = None

        def _restore() -> None:
            if previous is not None:
                try:
                    signal.signal(signal.SIGTERM, previous)
                except (ValueError, OSError):
                    pass

        try:
            yield run
        finally:
            try:
                _saved, error = sync_learned_answers(client, home / "forms.sqlite", client_answers(snapshot))
                if error:
                    run.sync_error = error
                try:
                    client.record_run(str(uuid4()), url, run.status, run.unresolved)
                except AccountError as exc:
                    # Fill results cannot be reconstructed after a crash; do not claim sync.
                    print("Fill finished locally, but history could not be saved. The account history may be incomplete.")
                    run.sync_error = run.sync_error or str(exc)
            finally:
                _restore()


def run_account_fill(args, fill: Callable[..., int], *, client: AccountClient | None = None) -> int:
    from pocket_agent.account.auth import signed_in_client

    require_https(args.url)
    if args.headless:
        raise AccountError("Account fills use a visible browser. Remove --headless.", 422)
    if args.resume:
        raise AccountError("Account fills use your account resume. Remove --resume.", 422)
    client = client or signed_in_client()
    try:
        with account_fill(client, args.url) as run:
            return fill(args, run.home, run)
    finally:
        client.close()
