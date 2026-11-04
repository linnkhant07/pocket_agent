"""LoRA-SFT Qwen3-0.6B on the gold traces with TRL and PEFT. CPU.

Reproduce:

    python scripts/train_lora_cpu.py --output /opt/cursor/artifacts/pocket-qwen3-0.6b-lora

The adapter is not committed when it is large. This script does not call a paid API.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

from pocket_agent.trainset import iter_gold_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=8)
    args = parser.parse_args()
    torch.set_num_threads(4)
    rows = iter_gold_rows()
    dataset = Dataset.from_list([{"messages": row["messages"]} for row in rows])
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    try:
        model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32)
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32)
    args.output.mkdir(parents=True, exist_ok=True)
    config = SFTConfig(
        output_dir=str(args.output),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        max_steps=args.max_steps,
        learning_rate=args.lr,
        logging_steps=5,
        save_strategy="no",
        report_to=[],
        max_length=768,
        assistant_only_loss=True,
        dataset_num_proc=1,
        use_cpu=True,
        bf16=False,
        fp16=False,
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=args.rank,
            lora_alpha=args.rank * 2,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        ),
    )
    trainer.train()
    trainer.model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print({"examples": len(rows), "output": str(args.output), "max_steps": args.max_steps})


if __name__ == "__main__":
    main()
