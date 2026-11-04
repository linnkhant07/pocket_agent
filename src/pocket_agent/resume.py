"""Resume text extraction. PDF via pypdf, plain text directly."""

from __future__ import annotations

import re
from pathlib import Path

from pocket_agent.paths import examples_dir


def parse_resume(path: Path | None = None, *, home: Path | None = None) -> dict:
    target = _resolve(path, home)
    if target is None:
        return {"ok": False, "error": "no resume file found"}
    suffix = target.suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(target)
    elif suffix in {".txt", ".md"}:
        text = target.read_text(encoding="utf-8")
    else:
        return {"ok": False, "error": f"unsupported resume type {suffix}"}
    return {
        "ok": True,
        "path": str(target),
        "text": text.strip(),
        "emails": re.findall(r"[\w.+-]+@[\w.-]+", text),
        "lines": [line.strip() for line in text.splitlines() if line.strip()][:40],
    }


def _resolve(path: Path | None, home: Path | None) -> Path | None:
    if path is not None:
        return path if path.exists() else None
    if home is not None:
        for name in ("resume.pdf", "resume.txt"):
            candidate = home / name
            if candidate.exists():
                return candidate
    for name in ("resume.txt", "resume.pdf"):
        candidate = examples_dir() / name
        if candidate.exists():
            return candidate
    return None


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)
