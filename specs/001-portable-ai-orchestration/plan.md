# Implementation Plan: Portable Aichestra Platform

**Branch**: `001-portable-ai-orchestration` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-portable-ai-orchestration/spec.md`

**Status**: Architecture contract realigned. Mode C production path uses
`ModeCRunController` / `mode_c.py` (one Orca Run + local gates). Do not merge
until maintainer architecture review confirms no dual-orchestrator regression.

## Summary

Deliver a portable, project-agnostic AI development environment where:

- **Mode A** — native Codex / Cursor (unintercepted)
- **Mode B** — Orca interactive (manual; no automatic full Mode C)
- **Mode C** — Aichestra → **exactly one Orca Run** → Orca owns workers/tasks/
  worktrees/handoffs; Aichestra owns policy, discovery, config, and
  deterministic gates/verification

Cross-platform Python owns portable helpers. OS bootstrap entrypoints wrap the
shared core. CI and fixtures prove portability without real provider quota.

### Aichestra IS

- bootstrap / update
- discovery (Orca + providers)
- policy and configuration layering
- deterministic gates (classify / Spec Kit path, maintenance-reviewer,
  verification-runner, security allowlists, context compaction)
- Orca adapter (create/resume one Run; create tasks; read state; forward
  attachments)

### Aichestra is NOT

- a general-purpose workflow engine
- a worker scheduler
- a session manager
- a worktree manager (beyond thin policy checks)
- a replacement for Orca
- authorized to fall back to direct Codex/Cursor when Orca is missing (Mode C)

### Forbidden Mode C shapes

```text
classify → run-create
implement → another run-create
phase report → another run-create
review → another run-create
```

```text
Aichestra → CodexProvider.execute_task()     # Mode C implement/review/writers
Aichestra → CursorProvider.execute_task()
Aichestra → LocalWorkerProvider.execute_task()  # Mode C research/writers
```

```text
Orca unavailable → fallback directly to Codex
project_root=None → Orca skipped → workflow continues
```

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Orca (runtime UI/control plane; not vendored),
provider CLIs discovered at runtime (Codex, Cursor, OpenCode, Ollama), standard
library-first helpers, pytest for tests

**Storage**: Filesystem config layering — tracked orchestration policies +
OS defaults + untracked machine-local + target-project + runtime overrides; no
application database

**Testing**: pytest unit/integration/contract/security tests; fake providers;
fixture repositories; GitHub Actions OS matrix; **architecture contract tests**
for MODE-C-001–010 before further production realignment

**Target Platform**: macOS, Windows, and Linux (native; WSL optional never
required; Docker/VM not required for Aichestra)

**Project Type**: Cross-platform CLI/library toolkit integrating with an
external orchestration UI (Orca)

**Performance Goals**: Bootstrap and doctor complete in interactive developer
time; research compaction keeps cloud handoff payloads bounded

**Constraints**: No hard-coded home paths; no secrets/models/sessions in Git;
no production SSH; CI must not use real Codex/Cursor quota; avoid extra daemons
if Orca already supplies the capability; inspect current Orca docs before
integration syntax; **do not soft-pedal requirements to match current
`OrchestratedWorkflow`**

**Scale/Scope**: Single orchestration repository; ≥2 fixture projects

## Constitution Check

*GATE: Must pass before design freeze. Re-checked after design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Portability First | PASS | Python package + OS entrypoints; no fixed paths |
| II. Graceful Degradation | PASS | Optional providers; Orca required only for Mode C; Mode A remains |
| III. Minimum Maintenance Cost | PASS | maintenance-reviewer + conditional tests/docs |
| IV. Security Is Structural | PASS | typed staging ops + sanitization outside prompts |
| V. Verify, Don't Claim | PASS | CI matrix + truthful smoke reporting |
| VI. Native Tools Remain Native | PASS | three modes; Orca opt-in; no command interception |
| VII. Project Agnosticism | PASS | fixtures prove isolation; Factory preserved |
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

### Source Code (target shape)

```text
src/aichestra/
├── cli.py                  # doctor / profile / bootstrap / update / orchestrate
├── repo.py
├── platform_detect.py
├── machine_profiler.py     # capability facts (no Mac identity dependency)
├── config/
│   ├── layering.py         # includes per-provider enabled flags
│   └── hardware_profiles.py  # capability tiers; fixture-only named examples
├── local_runtime/
├── providers/
│   ├── discovery.py
│   ├── orca.py             # Mode C control-plane adapter (one Run)
│   ├── codex.py            # discovery + Mode A direct use (not Mode C exec)
│   ├── cursor.py
│   └── local_worker.py
├── orchestration/
│   ├── modes.py            # A / B / C
│   ├── mode_c.py           # thin: resolve project → policy → one Run → gates
│   ├── roles.py            # preferred/fallback policy for Orca
│   ├── research_compact.py # compaction helpers (dispatch via Orca)
│   ├── maintenance_reviewer.py
│   ├── writers.py          # preference helpers (dispatch via Orca)
│   ├── verification.py
│   ├── handoff.py          # inside existing Run when possible
│   ├── media_routing.py
│   ├── factory_preserve.py
│   ├── speckit_policy.py   # policy input — not a workflow engine
│   └── maintenance_audit.py
├── security/
├── doctor.py
└── bootstrap/
```

**Remove or radically shrink** (current conflicting components):

| Component | Why obsolete / conflicting |
|-----------|----------------------------|
| `orchestration/workflow.py` `OrchestratedWorkflow` / `ModeCRunController` as a multi-phase agent scheduler | Second general-purpose orchestration engine; owns phase machine, agent switching, writers, research, edit lease, provider fallback |
| Direct Mode C paths calling Codex/Cursor/local-worker `execute_task` | Violates MODE-C-003/004 |
| Custom worktree/session manager + `.aichestra/edit.lock` if it duplicates Orca | Violates FR-057 / “worktrees belong to Orca” |
| Production `M4_PRO_24GB` / host-identity special cases | Violates capability-based profiling |
| Tests asserting “Orca unavailable → lead adapter” or “project_root=None → classify succeeds” | Lock wrong architecture |

Allowed to remain as **small deterministic helpers**: classify, maintenance
decision, verification runner, provider/security policy, compaction.

## Mode C control flow (target — thin coordinator)

```text
1. validate existing project root (fail if missing/not a dir)
2. require Orca binding (fail closed; never direct Codex/Cursor/local-worker)
3. local classify + Spec Kit policy (scale/steps only — not artifact stubs)
4. ensure exactly ONE Orca Run (resume or create); no run_id → FAIL CLOSED
5. MEDIUM/LARGE: ONE Orca handoff role=speckit_artifacts under that Run;
   Aichestra file-gates readiness (no Python stub unlock / *_satisfied)
6. ONE Orca-owned agents handoff (role mode_c_agents + ModeCPolicyPackage)
7. local maintenance-reviewer gate
8. writers via ONE Orca handoff role=mode_c_writers under SAME run_id when needed
9. local verification (explicit config or safe auto-detect)
10. final lead review via Orca under SAME run_id
```

Canonical lifecycle identity: Orca `run_id`.

`Phase` / `WorkflowState` may remain as observability/result representation.
Production `run_all()` MUST NOT be a `while current_phase: _execute_phase`
worker scheduler.

### Ownership boundary

| Concern | Owner |
|---------|-------|
| Agent / task / worker / worktree lifecycle | Orca (under one Run) |
| Classify / Spec Kit path / proportionality | Aichestra (local policy) |
| Spec Kit artifact files (MEDIUM/LARGE) | Orca task under Mode C Run → project `.aichestra/speckit/`; Aichestra gates on files |
| maintenance-reviewer | Aichestra (deterministic) |
| verification-runner | Aichestra (deterministic; results feed same Run) |
| Attachments delivery | Orca `--attach` / temp outside parent checkout |
| Codex/Cursor native CLIs | Mode A only (unintercepted) |
| Lead/local selection | Policy input to Orca — not Mode C `execute_task` |

### Forbidden production seams (Mode C)

- `aichestra-run-{id}` synthetic run ids
- Soft-fail `run-use` then unbound `task-create`
- `bound_writer_from_lead` / `writer_fn` direct lead execution
- `WorkflowBindings.lead` / `local_worker` used for Mode C `execute_task`
- Hard-coded `agent=opencode` when `local.enabled=false`
- Parent-checkout `.aichestra/attachments/` staging
- `brief_satisfied` / `plan_satisfied` metadata unlocks

## Architecture Notes

### Config layering

1. Tracked common defaults/policies
2. OS-specific tracked defaults
3. Untracked machine-local overrides (models/providers/local.enabled /
   providers.codex|cursor|local-worker.enabled)
4. Target-project configuration
5. Runtime/task override

### Three modes

- **A Native**: Codex/Cursor used directly; Aichestra does not intercept.
- **B Orca Interactive**: manual Orca use; no automatic full Mode C.
- **C Mode C**: exactly one Orca Run; fails closed without Orca or project root.

### Graceful degradation (precise)

| Condition | Behavior |
|-----------|----------|
| Codex missing | Orca may use Cursor |
| Cursor missing | Orca may use Codex |
| local-worker missing | cloud-oriented Mode C |
| both Codex and Cursor unavailable | fail or degraded Orca flow per remaining workers |
| **Orca missing** | **Mode C FAIL**; Mode A remains |

### Orca integration

Inspect current Orca docs before wiring. Prefer Orca primitives for runs,
tasks, workers, worktrees, diffs, handoff, attachments, and provider visibility.
**ONE Mode C invocation = ONE Orca Run.** Child worktree ownership is Orca’s;
adopt results before verification/review. Smallest reliable adapters only.

### Attachments

`--attach` paths MUST reach Orca via supported attachment/file primitives.
If the installed Orca version lacks the primitive: document limitation; do not
fake success. Vision tasks MUST NOT go to text-only local models.

### Machine profiler

Capability-based: OS, CPU, RAM, disk, GPU/accelerator, installed runtimes and
models. Order stronger tiers before weaker ones (`powerful` before `capable`).
Local inference always optional.

### Quota handoff

Prefer Codex→Cursor continuation **inside** the existing Orca Run. Manual
fallback must be one executable action with bounded context, not a placeholder
shell recipe.

### Spec Kit

SMALL / MEDIUM / LARGE-HIGH-RISK remain policy inputs to Orca — not a reason to
keep a Python phase engine that schedules agents.

### Staging safety / CI / maintenance audit

Unchanged in intent from prior plan: structural staging allowlists; fake
providers in CI; analysis-only maintenance audit classes.

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## Implementation sequencing (after this contract update)

1. Land architecture contract tests (MODE-C-001–010) — this pass.
2. Refactor/remove dual-orchestrator `workflow.py` into thin Mode C controller.
3. Ensure all agent dispatch is Orca-only under one `run_id`.
4. Rework/remove duplicate worktree edit-lock if Orca owns worktrees.
5. Complete real attachment forwarding; provider enable flags; handoff-in-run.
6. Delete obsolete tests; keep minimal regression net.
7. Only then mark feature tasks complete / converge.
