# Luau Model Training & Fine-Tuning Specification

This directory defines the dataset format, data sources, and training pipeline for fine-tuning the local Roblox Engineer LLM.

## 1. Data Sources for Training
The model requires high-quality, idiomatic, strict Luau code from production-grade Roblox codebases:

1. **ProfileService & ReplicaService (Madwork / Sleitnick)**:
   - Session locking, DataStore transaction integrity, server-authoritative state replication.
2. **Knit & RbxUtil (Sleitnick)**:
   - Modern service/controller lifecycle, Janitor/Trove instance cleanup, Component architecture.
3. **Matter ECS (evaera)**:
   - Pure functional entity component system in strict Luau.
4. **ByteNet & Blink (1axen)**:
   - High-throughput buffer-based networking.
5. **Roblox/Luau Official Test Suites**:
   - Grammar, type-inference, and edge-case syntax verification.
6. **Venture Agents Gold Standards**:
   - Custom systems generated and verified against `verify.ps1` (e.g., `DataService.luau`).

## 2. Dataset JSONL Schema (Alpaca / Instruction Format)
Each line in `seed_dataset.jsonl` contains:
```json
{
  "instruction": "High-level goal and acceptance criteria for a Roblox system.",
  "input": "Rojo project context, existing files, and schema rules.",
  "output": "{"files": [{"path": "src/server/...luau", "content": "--!strict\n..."}], "services": ["..."], "summary": "..."}"
}
```

## 3. Training & Quantization Pipeline
- **Base Model**: `qwen2.5-coder:14b` or `qwen2.5-coder:7b`.
- **Method**: QLoRA (Rank 64, Alpha 128) using Unsloth or Axolotl.
- **Target**: Output strictly formatted JSON with 100% compliant, `--!strict` Luau without dropped tokens.
