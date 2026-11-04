"""Constrained JSON decision for a field the matcher did not resolve.

The model may only choose a memory key that was already retrieved. It cannot
invent a value. If llama-cpp or the GGUF file is missing, the caller asks
the user instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pocket_agent.paths import data_home

MODEL_FILES = {
    "0.6b": "Qwen3-0.6B-Q8_0.gguf",
    "1.7b": "Qwen3-1.7B-Q8_0.gguf",
    "4b": "Qwen3-4B-Q4_K_M.gguf",
}
MODEL_REPOS = {
    "0.6b": "Qwen/Qwen3-0.6B-GGUF",
    "1.7b": "Qwen/Qwen3-1.7B-GGUF",
    "4b": "Qwen/Qwen3-4B-GGUF",
}


def model_path(size: str) -> Path:
    name = MODEL_FILES[size]
    return data_home() / "models" / name


@dataclass
class LLMChoice:
    action: str
    key: str = ""
    question: str = ""


class LocalDecider:
    """llama.cpp with a JSON grammar. Metal is used when llama.cpp was built with it."""

    def __init__(self, size: str = "0.6b", path: Path | None = None) -> None:
        self.size = size
        self.path = path or model_path(size)
        self._llm = None
        self.error = ""
        if size == "none":
            self.error = "deterministic"
            return
        if not self.path.exists():
            self.error = f"missing {self.path}"
            return
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            self.error = f"llama-cpp-python is not installed ({exc})"
            return
        self._llm = Llama(model_path=str(self.path), n_ctx=2048, verbose=False)

    @property
    def available(self) -> bool:
        return self._llm is not None

    def choose(self, *, label: str, kind: str, options: list[str], candidates: list[tuple[str, str]]) -> LLMChoice | None:
        """candidates are (memory key, stored value). Returns None when the model cannot be used."""
        if self._llm is None or not candidates:
            return None
        keys = [key for key, _value in candidates]
        grammar = _grammar(keys)
        payload = {
            "label": label,
            "kind": kind,
            "options": options,
            "memory": [{"key": key, "value": value} for key, value in candidates],
        }
        prompt = (
            "Pick a memory key for this form field, or ask the user. "
            "Reply with JSON only.\n"
            + json.dumps(payload)
            + "\n"
        )
        try:
            from llama_cpp import LlamaGrammar

            result = self._llm.create_completion(
                prompt,
                max_tokens=120,
                temperature=0,
                grammar=LlamaGrammar.from_string(grammar),
            )
            text = result["choices"][0]["text"]
            data = json.loads(text)
        except Exception:
            return None
        action = data.get("action")
        if action == "ask":
            return LLMChoice("ask", question=str(data.get("question") or label))
        if action == "fill" and data.get("key") in keys:
            return LLMChoice("fill", key=str(data["key"]))
        return None


def _gbnf(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _grammar(keys: list[str]) -> str:
    choices = " | ".join(_gbnf(json.dumps(key)) for key in keys)
    return "\n".join([
        "root ::= fill | ask",
        "fill ::= " + _gbnf('{"action":"fill","key":') + " key " + _gbnf("}"),
        "ask ::= " + _gbnf('{"action":"ask","question":"') + " chars " + _gbnf('"}'),
        "key ::= " + choices,
        "chars ::= [a-zA-Z0-9 ,.?]{1,120}",
    ])
