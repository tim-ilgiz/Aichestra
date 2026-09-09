# Aichestra

Portable, project-agnostic AI development orchestration environment centered on
Orca, with Codex as preferred lead, Cursor as fallback lead, and an optional
OpenCode + Ollama local worker.

## Status

This repository is currently **spec-driven**. The Spec Kit foundation,
constitution, feature specification, plan, and tasks are in place.
Platform implementation has not started yet.

## Target architecture (high level)

- **Orca** — opt-in UI / orchestration control plane
- **Codex** — preferred lead provider
- **Cursor** — fallback lead provider
- **OpenCode + Ollama** — optional local worker
- **maintenance-reviewer** — gates tests and documentation to limit bloat
- **GitHub Spec Kit** — proportional to task size/risk
- Native Codex/Cursor usage remains independent of Orca

## Supported operating systems

- macOS (native)
- Windows (native; WSL not required)
- Linux (native)

## Active specification

Start here for the initial platform feature:

- Spec: [`specs/001-portable-ai-orchestration/spec.md`](specs/001-portable-ai-orchestration/spec.md)
- Plan: [`specs/001-portable-ai-orchestration/plan.md`](specs/001-portable-ai-orchestration/plan.md)
- Tasks: [`specs/001-portable-ai-orchestration/tasks.md`](specs/001-portable-ai-orchestration/tasks.md)
- Constitution: [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
- Agent contract: [`AGENTS.md`](AGENTS.md)

## How future agents should start

1. Read `AGENTS.md` and the constitution.
2. Read the active feature `spec.md`, `plan.md`, and `tasks.md`.
3. Implement tasks in dependency order with continuous verification.
4. Do not install or implement runtime components until executing the active
   feature tasks.

Spec Kit skills are installed for Cursor (`.cursor/skills`), Codex
(`.agents/skills`), and OpenCode (`.opencode/commands`). Cursor is the default
integration for the initial build.
