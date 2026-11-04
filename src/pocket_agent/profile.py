"""profile.yaml load, validate, and merge. The shipped example is fictional."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from pocket_agent.paths import CANARY, examples_dir

REQUIRED_SECTIONS = ("name", "education", "skills", "preferences")


class ProfileError(ValueError):
    pass


def example_profile_path() -> Path:
    return examples_dir() / "profile.yaml"


def load_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ProfileError(f"No profile at {path}. Run `pocket-agent init`.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ProfileError("profile.yaml must be a mapping")
    return validate_profile(data)


def validate_profile(data: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_SECTIONS if not data.get(key)]
    if missing:
        raise ProfileError(f"profile.yaml is missing: {', '.join(missing)}")
    email = str(data.get("email") or "")
    if email and "@" not in email:
        raise ProfileError("profile email is not an email address")
    education = data.get("education") or []
    if not isinstance(education, list) or not education:
        raise ProfileError("education must be a non-empty list")
    for item in education:
        if not isinstance(item, dict) or not item.get("school") or not item.get("degree"):
            raise ProfileError("each education entry needs school and degree")
    citizenship = data.setdefault("citizenship", {})
    if not isinstance(citizenship, dict):
        raise ProfileError("citizenship must be a mapping")
    citizenship.setdefault("clearance", "none")
    citizenship.setdefault("requires_sponsorship", False)
    location = data.setdefault("location", {})
    if not isinstance(location, dict):
        raise ProfileError("location must be a mapping")
    location.setdefault("willing_to_relocate", False)
    location.setdefault("remote_ok", True)
    data.setdefault("canary", CANARY)
    return data


def dump_profile(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def public_view(profile: dict[str, Any], section: str | None = None) -> dict[str, Any]:
    if section:
        if section not in profile:
            return {"ok": False, "error": f"unknown profile section {section!r}"}
        return {"ok": True, "section": section, "value": profile[section]}
    hidden = {"canary"}
    return {"ok": True, "profile": {key: value for key, value in profile.items() if key not in hidden}}
