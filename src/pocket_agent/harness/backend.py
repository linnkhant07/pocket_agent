"""OpenAI-compatible chat clients: Ollama, llama.cpp llama-server, and MLX-LM."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Completion:
    text: str
    latency_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class OpenAICompatBackend:
    """POST {base_url}/chat/completions.

    base_url includes /v1, for example http://127.0.0.1:11434/v1.
    Ollama is the default. llama-server and `mlx_lm.server` speak the same API.
    """

    base_url: str
    api_key: str = "ollama"
    timeout: float = 180.0
    extra_body: dict[str, Any] = field(default_factory=dict)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Completion:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body.update(self.extra_body)
        try:
            return self._post(body)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and self.extra_body:
                bare = {key: value for key, value in body.items() if key not in self.extra_body}
                return self._post(bare)
            raise RuntimeError(self._explain(exc)) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Cannot reach {self.base_url} ({exc}). "
                "For Ollama, start `ollama serve`. For llama.cpp, start `llama-server --jinja`. "
                "For MLX, start `mlx_lm.server`."
            ) from exc

    def _post(self, body: dict[str, Any]) -> Completion:
        url = self.base_url.rstrip("/") + "/chat/completions"
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
        latency = time.perf_counter() - started
        message = (payload.get("choices") or [{}])[0].get("message") or {}
        text = message.get("content") or ""
        if not str(text).strip():
            text = message.get("reasoning") or message.get("reasoning_content") or ""
        tool_calls = message.get("tool_calls") or []
        if tool_calls and not str(text).strip():
            text = _tool_call_to_action(tool_calls[0])
        usage = payload.get("usage") or {}
        return Completion(
            text=str(text),
            latency_s=latency,
            prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
            completion_tokens=_int_or_none(usage.get("completion_tokens")),
        )

    def _explain(self, exc: httpx.HTTPStatusError) -> str:
        detail = exc.response.text[:300]
        return f"Model server returned {exc.response.status_code} from {self.base_url}: {detail}"


@dataclass
class MockBackend:
    script: list[str]
    index: int = 0

    def complete(self, messages: list[dict[str, str]], *, model: str, temperature: float = 0.0, max_tokens: int = 400) -> Completion:
        del messages, model, temperature, max_tokens
        if self.index >= len(self.script):
            text = '{"action":"final","answer":"No further scripted steps."}'
        else:
            text = self.script[self.index]
            self.index += 1
        return Completion(text=text, latency_s=0.0, prompt_tokens=1, completion_tokens=max(1, len(text) // 4))


def backend_for(name: str, *, base_url: str | None = None, model: str | None = None, script: list[str] | None = None) -> Any:
    key = (name or "ollama").lower()
    if key == "mock":
        return MockBackend(script or [])
    defaults = {
        "ollama": "http://127.0.0.1:11434/v1",
        "llamacpp": "http://127.0.0.1:8080/v1",
        "llama.cpp": "http://127.0.0.1:8080/v1",
        "mlx": "http://127.0.0.1:8080/v1",
    }
    if key not in defaults:
        raise ValueError(f"unknown backend {name!r}")
    extra: dict[str, Any] = {}
    if key == "ollama":
        # Qwen3 thinking mode wraps JSON in <think> blocks. Ask Ollama to skip it.
        extra = {"think": False}
    return OpenAICompatBackend(base_url=base_url or defaults[key], extra_body=extra)


def _tool_call_to_action(tool_call: dict[str, Any]) -> str:
    import json

    function = tool_call.get("function") or {}
    raw_args = function.get("arguments") or {}
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError:
            raw_args = {}
    return json.dumps({"action": "tool", "tool": function.get("name"), "arguments": raw_args})


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
