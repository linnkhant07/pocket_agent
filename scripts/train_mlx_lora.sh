#!/bin/sh
# Apple Silicon path. This Linux VM does not run it.
# Gold traces are the same JSONL the CPU trainer builds.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python -m pocket_agent.trainset data/train/train.jsonl
python -m mlx_lm.lora \
  --model Qwen/Qwen3-0.6B \
  --train \
  --data data/train \
  --iters "${ITERS:-100}" \
  --batch-size 1 \
  --learning-rate 2e-4 \
  --adapter-path "${ADAPTER_PATH:-adapters/pocket-qwen3-0.6b}"
echo "Adapter written to ${ADAPTER_PATH:-adapters/pocket-qwen3-0.6b}"
echo "Score it with: pocket-agent eval --backend transformers --model Qwen/Qwen3-0.6B --adapter ${ADAPTER_PATH:-adapters/pocket-qwen3-0.6b}"
