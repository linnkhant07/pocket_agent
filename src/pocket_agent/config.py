"""Runtime config in ~/.pocket-agent/config.yaml, overridable by the environment."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

from pocket_agent.paths import data_home


def config_path(home: Path | None = None) -> Path:
    return (home or data_home()) / "config.yaml"


def load_config(home: Path | None = None) -> dict[str, Any]:
    path = config_path(home)
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    if os.environ.get("POCKET_AGENT_BACKEND"):
        data["backend"] = os.environ["POCKET_AGENT_BACKEND"]
    if os.environ.get("POCKET_AGENT_MODEL"):
        data["model"] = os.environ["POCKET_AGENT_MODEL"]
    if os.environ.get("POCKET_AGENT_BASE_URL"):
        data["base_url"] = os.environ["POCKET_AGENT_BASE_URL"]
    data.setdefault("backend", "ollama")
    return data


_CONFIG_LOCK = threading.Lock()


def save_config(data: dict[str, Any], home: Path | None = None) -> Path:
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, sort_keys=False)
    with _CONFIG_LOCK:
        path.write_text(text, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path
