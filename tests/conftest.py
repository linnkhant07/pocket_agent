import os
import sys
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # A non-tty run is fully buffered. Line buffering keeps the final pass/fail line visible.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(line_buffering=True)
            except (OSError, ValueError):
                pass


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    sys.stdout.flush()
    sys.stderr.flush()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("POCKET_AGENT_HOME", str(root))
    monkeypatch.setenv("POCKET_AGENT_TODAY", "2026-11-04")
    return root


@pytest.fixture(autouse=True)
def _private_readings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test reads status questions into its own cache, never the user's ~/.pocket-agent."""
    from pocket_agent.forms import status

    monkeypatch.setattr(status, "readings_path", lambda: tmp_path / "readings.sqlite")


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_TODAY", os.environ.get("POCKET_AGENT_TODAY", "2026-11-04"))


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests that need a Keychain install their own fake; none may read or write the user's real one."""
    from pocket_agent.account import auth
    from pocket_agent.account.client import AccountError

    def unavailable():
        raise AccountError("Keychain is disabled in tests.", 503)

    monkeypatch.setattr(auth, "_keyring", unavailable)
