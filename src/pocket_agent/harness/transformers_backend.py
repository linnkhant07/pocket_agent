"""Local transformers backend so a LoRA adapter can be scored without Ollama.

Generation is capped at 96 new tokens. Gold tool calls fit in that budget, and
a CPU eval of the base model will not run away to the harness default of 400.
"""

from __future__ import annotations

import time
from typing import Any

from pocket_agent.harness.backend import Completion

_MAX_NEW_TOKENS = 96


class TransformersBackend:
    def __init__(self, model_path: str, adapter_path: str | None = None) -> None:
        self.model_path = model_path
        self.adapter_path = adapter_path or None
        self._model = None
        self._tokenizer = None

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Completion:
        del model, temperature
        self._load()
        import torch

        tokenizer = self._tokenizer
        prompt = _render(tokenizer, messages)
        inputs = tokenizer(prompt, return_tensors="pt")
        limit = min(max_tokens, _MAX_NEW_TOKENS)
        started = time.perf_counter()
        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=limit,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        latency = time.perf_counter() - started
        new_tokens = output[0][inputs["input_ids"].shape[1] :]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        return Completion(
            text=text,
            latency_s=latency,
            prompt_tokens=int(inputs["input_ids"].shape[1]),
            completion_tokens=int(new_tokens.shape[0]),
        )

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(4)
        tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        try:
            model = AutoModelForCausalLM.from_pretrained(self.model_path, dtype=torch.float32)
        except TypeError:
            model = AutoModelForCausalLM.from_pretrained(self.model_path, torch_dtype=torch.float32)
        if self.adapter_path:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, self.adapter_path)
        model.eval()
        self._model = model
        self._tokenizer = tokenizer


def _render(tokenizer: Any, messages: list[dict[str, str]]) -> str:
    try:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
