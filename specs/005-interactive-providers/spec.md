# Feature Specification: Interactive provider setup

**Feature Branch**: `005-interactive-providers`

**Created**: 2026-09-11

**Status**: In progress

**Input**: Users must add their own LLM/inference backends and agent runtimes
on their machine without editing JSON by hand. Prefer an interactive
`aichestra settings` flow. Reuse Orca account/agent signals when available.
Do not turn Aichestra into a second orchestrator.

## Architecture constraints

- Orca remains the only Mode C control plane.
- Distinguish Agent Runtime vs Model Provider vs ExecutionTarget.
- Machine-local provider/endpoint settings stay untracked (`machine.local.json`).
- Never store API keys/tokens in config; only optional `api_key_env` names.
- A configured provider without a reachable probe stays `available=false`.
- Adding a provider MUST NOT add Mode C workflow phases (MODE-C-019).
- Raw model providers are not workers (MODE-C-020); pair with a runtime
  (typically OpenCode) and a proven Orca launch path.

## User stories

### US1 — Interactive add model provider (P1)

Developer runs `aichestra settings`, chooses Providers, adds Ollama or an
OpenAI-compatible endpoint (LM Studio / vLLM / OpenRouter / custom), and can
optionally pair it with OpenCode so models appear for role binding.

**Acceptance**

1. Interactive menu item for Providers without leaving settings.
2. Ollama path writes `execution.model_providers.ollama` + can set `local.enabled`.
3. OpenAI-compatible path writes `execution.model_providers.<id>` with
   `api_style=openai`, endpoint, optional `api_key_env`.
4. Reachable endpoints list models on next discovery; unreachable stay configured-only.
5. Optional OpenCode pairing adds an `execution.bindings` row and does not
   invent a Python worker scheduler.

### US2 — Interactive add agent runtime (P1)

Developer registers a known or custom Agent Runtime binary id in machine-local
config so role bindings can select it.

**Acceptance**

1. Built-in ids (`claude`, `gemini`, `opencode`, …) and custom id+binary work.
2. Unknown runtime ids remain fail-closed until registered.
3. Registration lives in machine-local (or project when explicitly chosen later);
   secrets are never written.

### US3 — Orca hints (P2)

When Orca is available, settings can list agents/accounts Orca already knows
and suggest enabling matching runtimes — without copying credentials.

**Acceptance**

1. Missing Orca degrades gracefully (no crash; manual add still works).
2. Suggestions are runtime enablement hints, not Mode C dispatch proof.

## Out of scope

- Storing provider API keys in Git or project.json
- Claiming a new provider is Mode C-runnable without Orca launch proof
- New orchestration backends
- Full GUI installer
