"""Filesystem layout. User data lives under ~/.pocket-agent, never in the repo."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

CANARY = "POCKET-AGENT-CANARY-7f3a"
HOME_DIRNAME = ".pocket-agent"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path | None:
    candidate = package_root().parents[1]
    if (candidate / "pyproject.toml").exists() and (candidate / "examples").exists():
        return candidate
    return None


def examples_dir() -> Path:
    root = repo_root()
    if root is not None:
        return root / "examples"
    packaged = package_root() / "resources" / "examples"
    if packaged.exists():
        return packaged
    return package_root() / "resources"


def resources_dir() -> Path:
    return package_root() / "resources"


def data_home() -> Path:
    override = os.environ.get("POCKET_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / HOME_DIRNAME


def today() -> date:
    raw = os.environ.get("POCKET_AGENT_TODAY")
    if raw:
        return date.fromisoformat(raw)
    return date.today()
