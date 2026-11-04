"""Model tiers. Tags and licenses were checked on 2026-11-04.

Ollama library pages (HTTP 200):
  https://ollama.com/library/qwen3:0.6b
  https://ollama.com/library/qwen3:1.7b
  https://ollama.com/library/qwen3:8b
  https://ollama.com/library/qwen3:8b-q8_0
  https://ollama.com/library/qwen3:14b
  https://ollama.com/library/qwen3.5:4b

Hugging Face LICENSE files begin with the Apache License 2.0 text:
  https://huggingface.co/Qwen/Qwen3-0.6B
  https://huggingface.co/Qwen/Qwen3-1.7B
  https://huggingface.co/Qwen/Qwen3-14B
  https://huggingface.co/Qwen/Qwen3.5-4B

LFM2.5 is mentioned in the original plan as an 8GB alternative. It is not
pinned here; the defaults are the Apache-2.0 Qwen tags above.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    tag: str
    hf_repo: str
    license: str
    approx_download: str
    notes: str
    alternative_tag: str | None = None


# Detected RAM is mapped onto these tiers in hardware.select_tier.
TIERS: dict[str, ModelSpec] = {
    "8gb": ModelSpec(
        tag="qwen3:1.7b",
        hf_repo="Qwen/Qwen3-1.7B",
        license="Apache-2.0",
        approx_download="1.4GB",
        notes="Qwen3-1.7B, the 8GB default.",
        alternative_tag="qwen3:0.6b",
    ),
    "16gb": ModelSpec(
        tag="qwen3.5:4b",
        hf_repo="Qwen/Qwen3.5-4B",
        license="Apache-2.0",
        approx_download="3.4GB",
        notes="Qwen3.5-4B Q4, the 16GB default. Text-only use; the Ollama tag may also fetch a small vision projector.",
        alternative_tag="qwen3:8b",
    ),
    "32gb": ModelSpec(
        tag="qwen3:14b",
        hf_repo="Qwen/Qwen3-14B",
        license="Apache-2.0",
        approx_download="9.3GB",
        notes="Qwen3-14B Q4 for 32GB and above.",
        alternative_tag="qwen3:8b-q8_0",
    ),
}

# Smallest verified tag. Used for CPU smoke tests, not as a quality target.
SMOKE_MODEL = ModelSpec(
    tag="qwen3:0.6b",
    hf_repo="Qwen/Qwen3-0.6B",
    license="Apache-2.0",
    approx_download="523MB",
    notes="CPU smoke model only.",
)

PERMISSIVE_LICENSES = {"Apache-2.0", "MIT", "BSD-3-Clause"}


def spec_for_tag(tag: str) -> ModelSpec | None:
    if tag == SMOKE_MODEL.tag:
        return SMOKE_MODEL
    for spec in TIERS.values():
        if tag in {spec.tag, spec.alternative_tag}:
            return spec
    return None
