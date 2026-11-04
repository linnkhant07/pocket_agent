import os
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("POCKET_AGENT_HOME", str(root))
    monkeypatch.setenv("POCKET_AGENT_TODAY", "2026-11-04")
    return root


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_TODAY", os.environ.get("POCKET_AGENT_TODAY", "2026-11-04"))
