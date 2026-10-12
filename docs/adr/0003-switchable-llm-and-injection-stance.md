# 3. Switchable LLM provider and untrusted-log stance
Status: accepted

## Context
Bedrock costs money, local models are free, and tests need determinism. Logs may contain attacker-controlled text.
## Decision
One `LLM.complete(system, user)` interface with mock, Ollama and Bedrock implementations chosen by `LLM_PROVIDER`. Default is mock. Output must validate against a Pydantic schema. The model may suggest a memory value but never the target; policy clamps values and downgrades low confidence.
## Consequences
Free by default, deterministic tests, a heuristic fallback if the model fails. The mock is not a quality benchmark.
