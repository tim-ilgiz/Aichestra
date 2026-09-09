# Implementation Plan: Portable Aichestra Platform

**Branch**: `001-portable-ai-orchestration` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-portable-ai-orchestration/spec.md`

## Summary

Deliver a portable, project-agnostic AI development orchestration environment
with Orca as the opt-in control plane, three user modes (Native / Orca
Interactive / Orchestrated), Codex as preferred lead, Cursor as fallback lead,
and an optional OpenCode + local-runtime worker. Cross-platform Python owns
portable helper logic including machine-profiler, local-runtime-discovery,
capability routing, verification-runner, and structural staging safety. OS
bootstrap entrypoints wrap the shared core. CI and fixtures prove portability
and isolation without consuming real provider quota. No Docker/VM required for
Aichestra itself; no duplicate CAO/general orchestrator.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Orca (runtime UI/control plane; not vendored),
provider CLIs discovered at runtime (Codex, Cursor, OpenCode, Ollama), standard
library-first helpers, pytest for tests

**Storage**: Filesystem config layering — tracked orchestration policies +
OS defaults + untracked machine-local + target-project + runtime overrides; no
application database

**Testing**: pytest unit/integration/contract/security tests; fake providers;
fixture repositories; GitHub Actions OS matrix

**Target Platform**: macOS, Windows, and Linux (native; WSL optional never
required; Docker/VM not required for Aichestra)

**Project Type**: Cross-platform CLI/library toolkit integrating with an
external orchestration UI (Orca)

**Performance Goals**: Bootstrap and doctor complete in interactive developer
time; research compaction keeps cloud handoff payloads bounded

**Constraints**: No hard-coded home paths; no secrets/models/sessions in Git;
no production SSH; CI must not use real Codex/Cursor quota; avoid extra daemons
if Orca already supplies the capability; inspect current Orca docs before
integration syntax

**Scale/Scope**: Single orchestration repository; ≥2 fixture projects; one
initial platform feature covering bootstrap→providers→roles→security→CI

## Constitution Check

*GATE: Must pass before design freeze. Re-checked after design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Portability First | PASS | Python package + OS entrypoints; no fixed paths |
| II. Graceful Degradation | PASS | Provider discovery with fakes/absent modes |
| III. Minimum Maintenance Cost | PASS | maintenance-reviewer + conditional tests/docs |
| IV. Security Is Structural | PASS | typed staging ops + sanitization outside prompts |
| V. Verify, Don't Claim | PASS | CI matrix + truthful smoke reporting |
| VI. Native Tools Remain Native | PASS | three modes; Orca opt-in; no command interception |
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
├── __main__.py             # python -m aichestra CLI entry
├── cli.py                  # doctor / profile / bootstrap / update dispatch
├── repo.py                 # arbitrary clone-path root resolution
├── platform_detect.py      # OS detection helpers
├── machine_profiler.py     # deterministic hardware facts
├── config/
│   ├── layering.py         # tracked → OS → machine-local → project → runtime
│   └── hardware_profiles.py
├── local_runtime/
│   ├── base.py             # extensible runtime interface
│   ├── discovery.py
│   ├── ollama.py           # initial runtime
│   ├── model_selector.py   # AVAILABLE/CAPABLE/ALLOWED/PREFERRED
│   └── resources.py        # one-worker / pressure handling
├── providers/
│   ├── discovery.py
│   ├── base.py
│   ├── orca.py
│   ├── codex.py
│   ├── cursor.py
│   └── local_worker.py     # OpenCode + local runtime optional
├── orchestration/
│   ├── modes.py            # Native / Interactive / Orchestrated
│   ├── workflow.py         # Mode C thin run controller (policy + gates)
│   ├── roles.py
│   ├── research_compact.py # repository-researcher + compaction
│   ├── maintenance_reviewer.py
│   ├── writers.py          # test/doc preference helpers
│   ├── verification.py     # verification-runner
│   ├── handoff.py          # Codex→Cursor bounded context
│   ├── worktrees.py        # serial/isolated edit policy
│   ├── media_routing.py    # files/vision capability routing
│   ├── factory_preserve.py
│   ├── speckit_policy.py
│   └── maintenance_audit.py
├── security/
│   ├── staging_ops.py      # typed diagnostic builders
│   ├── staging_allowlist.py
│   └── sanitize.py
├── doctor.py
└── bootstrap/
    └── core.py             # idempotent shared bootstrap/update logic

bootstrap/
├── macos/setup.sh
├── windows/setup.ps1
└── linux/setup.sh

scripts/
├── doctor.py
├── machine_profile.py
└── smoke_mac.py

fixtures/
├── project_a/              # Python/pytest sample
└── project_b/              # Node sample (different layout)

tests/
├── unit/
├── integration/
├── contract/
├── security/
└── fakes/

.github/workflows/
└── ci.yml                  # macOS + Windows + Linux; fake providers

policies/                   # tracked portable defaults
└── defaults.yml
```

**Structure Decision**: Single Python package at repo root with thin OS
bootstrap scripts under `bootstrap/`. No microservice/daemon layer beyond what
Orca already provides. Fixtures and fake providers live in-repo for isolation
and CI.

## Architecture Notes

### Config layering

1. Tracked common defaults/policies
2. OS-specific tracked defaults
3. Untracked machine-local overrides (models/providers/local.enabled)
4. Target-project configuration
5. Runtime/task override
Resolution never requires a fixed home-directory string in tracked files.

### Three modes

- **A Native**: Codex/Cursor used directly; Aichestra does not intercept.
- **B Orca Interactive**: single-agent session; no automatic full orchestration.
- **C Mode C**: Aichestra creates/resumes **one Orca Run**, applies policy and
  deterministic gates; Orca owns tasks/workers/worktrees/agent scheduling.
  Mode C fails closed if Orca is unavailable (no direct Codex/Cursor fallback).

### Provider discovery

Discovery returns structured availability including failure classes where
practical (quota, auth, network, timeout, crash, cancel). Missing
Codex/Cursor/local worker yields degraded but useful modes. Native Codex/Cursor
invocation paths are not wrapped globally.

### Orca integration

Inspect current installed/version-matched Orca documentation before wiring
adapters. Prefer Orca primitives for runs, tasks, workers, worktrees, diffs,
handoff, and provider visibility. **ONE user task = ONE Orca Run** (create once,
reuse `run_id` for all task-create/worker-start; resume by `run_id`).
Child worktree results MUST be adopted before verification/reviewer.
Smallest reliable adapters only; no CAO / second workflow engine.

### Machine profiler + local runtime

`machine_profiler` uses `platform`, `os`, `shutil`, and OS-specific safe probes.
`local_runtime` abstracts discovery; Ollama is the first implementation.
Selection is capability-based. Hardware profiles suggest model class (M4 Pro
24 GB example → conservative ~14B, one worker) without committing weights,
host paths, or global mandatory model ids.

### Mode C policy schedule (not a second orchestrator)

Policy order: classify → research if useful → lead (Codex→Cursor via Orca) →
maintenance-reviewer → optional test/docs writers → verification-runner →
lead review.

- **Agent steps** → Orca tasks/workers on the same Run (child worktree for edits).
- **Gates** → Aichestra local: Spec Kit scale/heuristics, maintenance-reviewer,
  verification-runner (exit codes), against adopted worktree.
- Canonical lifecycle identity is the Orca `run_id`, not a Python phase engine.

### Staging safety

Typed diagnostic operations build argv arrays. Allowlist/deny execute before
any remote shell. Reject sudo, rm, restart/stop, docker rm/stop, kubectl
apply/delete, package installs, chmod/chown, and remote writes. Sanitize
outputs before cloud handoff. Production SSH targets have no integration path.
Never inspect/copy private key contents.

### Quota handoff

Prefer automatic Codex→Cursor fallback only if Orca primitives make it reliable;
otherwise document and implement one-action manual handoff preserving bounded
task context (FR-036). Bounded packet fields: original request, accepted
decisions, repo/worktree, git status/diff, workflow phase, completed/remaining
work, known failures, next action, compacted research. Do not hard-code fragile
stderr matching alone. Do not transfer huge full transcripts.

### Maintenance audit

Optional analysis-only first phase. Doc classes: KEEP_CANONICAL, MERGE, UPDATE,
ARCHIVE_HISTORY, GENERATED_OR_DERIVABLE, OBSOLETE, DELETE_CANDIDATE. Test
classes: HIGH_VALUE, BUSINESS_INVARIANT, INTEGRATION_BEHAVIOR, CONTRACT,
SECURITY_OR_MONEY_CRITICAL, DUPLICATE, IMPLEMENTATION_DETAIL, TRIVIAL,
OVER_MOCKED, REDUNDANT_PARAMETER_VARIATION, FLAKY, OBSOLETE,
CONSOLIDATION_CANDIDATE. Cleanup is separate and reviewable.

### Files / vision routing

Prefer Orca native attachments. Provide a small routing helper that marks
vision-required tasks and refuses text-only local models. Large text inputs may
be locally summarized before cloud lead handoff.

### CI

Matrix on macOS, Windows, Linux. Inject fake providers. Never call real
Codex/Cursor account APIs in CI.

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
