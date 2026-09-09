# Implementation Plan: Portable Aichestra Platform

**Branch**: `001-portable-ai-orchestration` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-portable-ai-orchestration/spec.md`

## Summary

Deliver a portable, project-agnostic AI development orchestration environment
with Orca as the opt-in control plane, Codex as preferred lead, Cursor as
fallback lead, and optional OpenCode+Ollama local worker. Cross-platform Python
owns portable helper logic; OS-specific bootstrap entrypoints wrap it; security
boundaries for staging diagnostics are structural; CI and fixtures prove
portability and isolation without consuming real provider quota.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Orca (runtime UI/control plane; not vendored),
provider CLIs discovered at runtime (Codex, Cursor, OpenCode, Ollama), standard
library-first helpers, pytest for tests

**Storage**: Filesystem config layering — tracked orchestration policies +
untracked machine-local provider/hardware settings; no application database

**Testing**: pytest unit/integration/contract tests; fake providers; fixture
repositories; GitHub Actions OS matrix

**Target Platform**: macOS, Windows, and Linux (native; WSL optional never
required)

**Project Type**: Cross-platform CLI/library toolkit integrating with an
external orchestration UI (Orca)

**Performance Goals**: Bootstrap and doctor complete in interactive developer
time; research compaction keeps cloud handoff payloads bounded

**Constraints**: No hard-coded home paths; no secrets/models/sessions in Git;
no production SSH; CI must not use real Codex/Cursor quota; avoid extra daemons
if Orca already supplies the capability

**Scale/Scope**: Single orchestration repository; ≥2 fixture projects; one
initial platform feature covering bootstrap→providers→roles→security→CI

## Constitution Check

*GATE: Must pass before design freeze. Re-checked after design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Portability First | PASS | Python package + OS entrypoints; no fixed paths |
| II. Graceful Degradation | PASS | Provider discovery with fakes/absent modes |
| III. Minimum Maintenance Cost | PASS | maintenance-reviewer + conditional tests/docs |
| IV. Security Is Structural | PASS | staging allowlist + sanitization outside prompts |
| V. Verify, Don't Claim | PASS | CI matrix + truthful smoke reporting |
| VI. Native Tools Remain Native | PASS | Orca opt-in; no command interception |
| VII. Project Agnosticism | PASS | fixtures prove isolation; no app-specific assumptions |
| VIII. One Canonical Source | PASS | Spec Kit + AGENTS.md + constitution; avoid doc sprawl |

## Project Structure

### Documentation (this feature)

```text
specs/001-portable-ai-orchestration/
├── plan.md
├── spec.md
├── tasks.md
└── checklists/
    ├── requirements.md
    └── quality.md
```

Optional Spec Kit research/data-model/contracts/quickstart artifacts are
intentionally omitted to reduce documentation debt; durable decisions live here
and in `spec.md`.

### Source Code (repository root)

```text
src/aichestra/
├── __init__.py
├── repo.py                 # arbitrary clone-path root resolution
├── platform_detect.py      # OS detection helpers
├── config/
│   ├── layering.py         # tracked vs machine-local config
│   └── hardware_profiles.py
├── providers/
│   ├── discovery.py
│   ├── base.py
│   ├── orca.py
│   ├── codex.py
│   ├── cursor.py
│   └── local_worker.py     # OpenCode + Ollama optional
├── orchestration/
│   ├── roles.py
│   ├── research_compact.py
│   ├── maintenance_reviewer.py
│   ├── handoff.py          # Codex→Cursor bounded context
│   └── factory_preserve.py
├── security/
│   ├── staging_allowlist.py
│   └── sanitize.py
├── doctor.py
└── bootstrap/
    └── core.py             # idempotent shared bootstrap logic

scripts/
├── bootstrap_macos.py
├── bootstrap_windows.py
├── bootstrap_linux.py
└── doctor.py

fixtures/
├── project_a/
└── project_b/

tests/
├── unit/
├── integration/
├── contract/
└── security/

.github/workflows/
└── ci.yml                  # macOS + Windows + Linux; fake providers
```

**Structure Decision**: Single Python package at repo root with thin OS
bootstrap scripts. No microservice/daemon layer beyond what Orca already
provides. Fixtures and fake providers live in-repo for isolation and CI.

## Architecture Notes

### Config layering

1. Tracked defaults/policies in repository
2. Untracked machine-local overrides for models/providers/paths
3. Process env only for ephemeral overrides
Resolution never requires a fixed home-directory string in tracked files.

### Provider discovery

Discovery returns structured availability. Missing Codex/Cursor/local worker
yields degraded but useful modes. Native Codex/Cursor invocation paths are not
wrapped globally.

### Orca integration

Orca is the opt-in interactive control plane. Integration adapters configure
roles and workflows; they do not replace native IDE/CLI usage.

### Local worker

OpenCode + Ollama remain optional. Hardware profiles suggest local model class
and worker count (M4 Pro 24 GB → conservative ~14B, one worker) without
committing weights or host paths.

### Staging safety

Allowlist and deny patterns execute before any remote shell. Reject sudo, rm,
restart/stop, docker rm/stop, kubectl apply/delete, and remote writes. Sanitize
outputs before cloud handoff. Production SSH targets have no integration path.

### Quota handoff

Prefer automatic Codex→Cursor fallback only if Orca primitives make it reliable;
otherwise document and implement one-action manual handoff preserving bounded
task context (FR-036).

### CI

Matrix on macOS, Windows, Linux. Inject fake providers. Never call real
Codex/Cursor account APIs in CI.

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
