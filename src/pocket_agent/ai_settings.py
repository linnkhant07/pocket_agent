"""AI provider keys live in macOS Keychain and model IDs in config.yaml, shared by the app and the terminal.

Environment variables still win, so existing setups keep working.
"""
from __future__ import annotations

import os

import yaml

from pocket_agent.config import config_path

PROVIDERS = {"gpt": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
_SERVICE = "pocket-agent:ai"


def _ring():
    from pocket_agent.account.auth import _keyring
    return _keyring()


def _stored_key(provider: str) -> str:
    from pocket_agent.account.client import AccountError
    try:
        return _ring().get_password(_SERVICE, provider) or ""
    except AccountError:
        return ""  # No Keychain (not macOS, or keyring missing); environment variables still work.


def api_key(provider: str) -> str:
    return os.environ.get(PROVIDERS[provider], "") or _stored_key(provider)


def model(provider: str) -> str:
    return os.environ.get("POCKET_AGENT_AI_MODEL", "") or str(_models().get(provider) or "")


def status() -> dict:
    return {provider: {"key_saved": bool(_stored_key(provider)), "key_from_env": bool(os.environ.get(variable)),
                       "model": model(provider), "model_from_env": bool(os.environ.get("POCKET_AGENT_AI_MODEL"))}
            for provider, variable in PROVIDERS.items()}


def save(provider: str, *, key: str | None, model_id: str) -> None:
    if key:
        _ring().set_password(_SERVICE, provider, key)
    data = _config()
    data.setdefault("ai", {}).setdefault("models", {})[provider] = model_id
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def forget_key(provider: str) -> None:
    ring = _ring()
    if ring.get_password(_SERVICE, provider):
        ring.delete_password(_SERVICE, provider)


def _config() -> dict:
    # Read the file itself; load_config() mixes in environment overrides that must not be written back.
    path = config_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return data if isinstance(data, dict) else {}


def _models() -> dict:
    models = (_config().get("ai") or {}).get("models")
    return models if isinstance(models, dict) else {}
