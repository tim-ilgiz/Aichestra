# Implementation Plan: Portable Aichestra Platform

**Branch**: `001-portable-ai-orchestration` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-portable-ai-orchestration/spec.md`

**Status**: Architecture source of truth: coordinator under Orca owns the
concrete workflow/DAG; Orca owns canonical Run/Task/Dispatch/worker/terminal
lifecycle; Aichestra owns discovery, ExecutionTarget resolution, policy,
security, and deterministic gates. Phase 24 (T163, T169–T176), Phase 25
(T177–T181), and review residual T182–T185 are implemented, including
structured `launch_proof_invocation`, Windows bridge attestation pipeline,
`abort-launch` opaque cleanup lease (Orca Dispatch probe + claim/restore),
and in-Run `AICHESTRA_GATE:verification`. Fresh GitHub CI on
current HEAD (T167) remains open. T168 live smoke is accepted for the
pre-T180 handshake. T180 in-Run verification live smoke is accepted on
`run_27377a46c199`.

## Summary

Deliver a portable, project-agnostic AI development environment where:

- **Mode A** — native Codex / Cursor (unintercepted)
- **Mode B** — Orca interactive (manual; no automatic full Mode C)
- **Mode C** — Aichestra accepts `task + project`, discovers Agent Runtimes,
  Model Providers / models, resolves proven ExecutionTargets, builds
  policy/context, creates/resumes **exactly one Orca Run**; launches one
  generic coordinator (bootstrap ExecutionTarget exception only); the
  coordinator under Orca owns the concrete workflow/DAG; Orca owns canonical
  orchestration lifecycle/state (Runs, Tasks, Dispatches, workers,
  terminals/worktrees, messages and handoffs); Aichestra owns discovery,
  policy, config, and deterministic gates/verification

Cross-platform Python owns portable helpers. OS bootstrap entrypoints wrap the
shared core. CI and fixtures prove portability without real provider quota.

Source-of-truth formula:

```text
The coordinator running under Orca owns the concrete workflow/DAG.

Orca owns the canonical orchestration lifecycle/state:
Runs, Tasks, Dispatches, worker lifecycle, terminal/worktree lifecycle,
messages and handoffs.

Aichestra owns neither the concrete DAG nor a parallel orchestration
state machine.
```

Capability/discovery formula:

```text
Aichestra discovers:
Agent Runtimes
+
Model Providers / Models
+
machine/project constraints
        ↓
resolves
ExecutionTargets
        ↓
passes targets/policy into ONE Orca Run
        ↓
Coordinator chooses/schedules workers (owns concrete DAG)
        ↓
Orca owns canonical Task/Dispatch/worker/worktree lifecycle
```

### Aichestra IS

- bootstrap / update
- discovery (Orca + Agent Runtimes + Model Providers/models + project-owned
  instructions/tooling + machine capabilities)
- ExecutionTarget resolution (compatibility + proven launch strategies)
- project-context / portable policy construction
- policy and configuration layering
- deterministic gates (classify / Spec Kit path, maintenance-reviewer,
  verification-runner, security allowlists, context compaction)
- Orca adapter (create/resume one Run; hand task/policy/context/attachments;
  bootstrap one coordinator; read Run state; feed gate results back)

### Aichestra is NOT

- a general-purpose workflow engine
- owner of the concrete Mode C workflow/DAG
- owner of a parallel agent-phase / orchestration state machine
- a worker scheduler (beyond the narrow coordinator bootstrap placement)
- a session manager
- a worktree manager (beyond thin policy checks)
- a replacement for Orca
- a generic LLM gateway
- an owner of an Aichestra agent loop
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
Aichestra → any Agent Runtime execute_task()    # Mode C agent work
```

```text
Orca unavailable → fallback directly to Codex
project_root=None → Orca skipped → workflow continues
if research → OpenCode / if implementation → Codex / if docs → local
Ollama endpoint alone → advertised as coding worker
```

This revision MUST NOT:

- make Ollama a standalone Orca coding worker without an agent runtime;
- make OpenCode or Ollama mandatory;
- forbid OpenCode or cloud models through OpenCode;
- turn Aichestra into a generic LLM gateway or agent loop;
- move worker scheduling from the coordinator into Python;
- change the exactly-one Orca Run rule;
- weaken fail-closed semantics.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Orca (runtime UI/control plane; not vendored),
Agent Runtime / Model Provider CLIs and endpoints discovered at runtime
(examples: Codex, Cursor, OpenCode, Ollama — none mandatory as architectural
roles), standard library-first helpers, pytest for tests

**Storage**: Filesystem config layering — tracked orchestration policies +
OS defaults + untracked machine-local + target-project + runtime overrides; no
application database

**Testing**: pytest unit/integration/contract/security tests; fake providers;
fixture repositories; GitHub Actions OS matrix; **architecture contract tests**
for MODE-C-001–010 and MODE-C-017–020 before further production realignment

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
│   ├── layering.py         # runtime/provider enabled flags; legacy aliases
│   └── hardware_profiles.py  # capability tiers; fixture-only named examples
├── local_runtime/          # local inference discovery helpers (not workers)
├── providers/
│   ├── discovery.py        # Agent Runtime / Model Provider discovery
│   ├── orca.py             # Mode C control-plane adapter (one Run)
│   ├── codex.py            # discovery + Mode A direct use (not Mode C exec)
│   ├── cursor.py
│   └── ...                 # future runtime adapters as needed
├── execution/
│   ├── runtimes.py         # AgentRuntime facts
│   ├── model_providers.py  # ModelProvider / inference backend facts
│   ├── targets.py          # ExecutionTarget construction
│   ├── compatibility.py    # runtime↔provider↔model compatibility
│   └── launch_strategies.py  # proven Orca launch paths
├── orchestration/
│   ├── modes.py            # A / B / C
│   ├── mode_c.py           # thin: resolve project → targets/policy → one Run → gates
│   ├── roles.py            # preferred/fallback policy for coordinator under Orca
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

Legacy `providers/local_worker.py` (if present) is a compatibility seam during
migration — target architecture does NOT treat `local-worker` as a canonical
domain type. Map legacy config into ExecutionTargets instead.

**Remove or radically shrink** (current conflicting components):

| Component | Why obsolete / conflicting |
|-----------|----------------------------|
| `orchestration/workflow.py` `OrchestratedWorkflow` / `ModeCRunController` as a multi-phase agent scheduler | Second general-purpose orchestration engine; owns phase machine, agent switching, writers, research, edit lease, provider fallback |
| Direct Mode C paths calling Codex/Cursor/`local_worker` `execute_task` | Violates MODE-C-003/004 |
| Treating OpenCode+Ollama or `local-worker` as universal workflow roles | Violates MODE-C-017 / FR-005 |
| Advertising a Model Provider as a coding worker without Agent Runtime + launch proof | Violates MODE-C-018/020 |
| Custom worktree/session manager + `.aichestra/edit.lock` if it duplicates Orca | Violates FR-057 / “worktrees belong to Orca” |
| Production `M4_PRO_24GB` / host-identity special cases | Violates capability-based profiling |
| Tests asserting “Orca unavailable → lead adapter” or “project_root=None → classify succeeds” | Lock wrong architecture |

Allowed to remain as **small deterministic helpers**: classify, maintenance
decision, verification runner, ExecutionTarget/security policy, compaction.

## Mode C control flow — project-execution control plane

Mode C does NOT have a universal hard-coded agent-phase sequence.

Canonical flow:

```text
User
 ↓
Aichestra
 ├─ ProjectContext discovery
 ├─ Agent Runtime discovery
 ├─ Model Provider/model discovery
 ├─ ExecutionTarget resolution
 ├─ policy/security
 └─ deterministic gates
       ↓
   ONE Orca Run
       ↓
Generic Coordinator
       │
       ├─ dynamic Task/Dispatch DAG
       ├─ target selection from allowed ExecutionTargets
       ├─ dependencies / retries
       └─ convergence
              ↓
        Orca lifecycle
              ↓
   ┌──────────┼──────────┐
   ↓          ↓          ↓
Target A    Target B    Target C

Examples only:
Codex       Cursor      OpenCode
OpenAI                  ↓
                        Ollama / LM Studio /
                        OpenRouter / vLLM / ...
```

Examples are non-normative.
The architecture must not depend on any particular runtime/provider pair.

Aichestra MAY resolve one runnable bootstrap ExecutionTarget solely to start
the single generic Mode C coordinator. That is bootstrap placement only — not
ownership of research/implement/review/writer scheduling.

Aichestra deterministic services may participate at explicit boundaries:

- policy/classification
- security checks
- context compaction
- maintenance policy
- verification commands / authoritative exit codes

Their results are fed back to the same Orca Run via coordinator ask/reply
(`AICHESTRA_GATE:maintenance`, `AICHESTRA_GATE:verification`). The coordinator
MUST NOT send `worker_done outcome=succeeded` until Aichestra returns the
authoritative verification result.

Aichestra MUST NOT translate this into a fixed internal sequence such as:

```text
classify
→ research
→ implement
→ maintenance
→ test_writer
→ doc_writer
→ verification
→ lead_review
```

That sequence may be a valid Orca execution plan for one task, but it is not the
Aichestra architecture.

Canonical lifecycle identity: Orca `run_id`.

Aichestra may expose neutral execution/gate status for observability.

It MUST NOT maintain a second agent-phase state machine that mirrors or predicts
the coordinator's workflow/DAG or Orca Run state.

Canonical orchestration state belongs to the Orca Run.

### Project context discovery

Before handing Mode C execution to Orca, Aichestra builds a bounded
`ProjectContext`.

The discovery layer SHOULD inspect, when present:

- AGENTS.md and supported nested agent instructions
- existing Spec Kit configuration/artifacts
- Factory / AI Factory tooling
- `.aichestra/project.json`
- repository language/build metadata
- existing build/test/lint commands
- existing documentation conventions
- supported project-specific agent configuration
- repository status / branch / relevant workspace metadata

Precedence:

1. global security invariants
2. explicit runtime/user overrides
3. target-project authoritative instructions
4. machine-local provider/capability policy
5. portable Aichestra defaults

Aichestra must preserve project-owned tooling instead of replacing it with a
parallel Aichestra-owned workflow.

### Ownership boundary

| Concern | Owner |
|---------|-------|
| Concrete workflow/DAG | coordinator under Orca |
| Task dependencies/order | coordinator under Orca |
| Run/Task/Dispatch lifecycle state | Orca |
| Worker lifecycle/accounting | Orca |
| Terminal/worktree lifecycle | Orca |
| Handoffs inside Mode C | Orca |
| User task + project entrypoint | Aichestra |
| Project-context discovery | Aichestra |
| Agent Runtime discovery | Aichestra |
| Model Provider/model discovery | Aichestra |
| Compatibility resolution | Aichestra |
| Construction of runnable ExecutionTargets | Aichestra |
| Policy describing preferred/allowed targets | Aichestra |
| Concrete inner worker selection | coordinator under Orca |
| Security policy | Aichestra |
| Deterministic verification | Aichestra |
| Project-owned AGENTS/Spec Kit/Factory rules | Target project |
| Canonical Mode C lifecycle identity | Orca `run_id` |

Critical boundary:

Aichestra MAY determine that a combination is or is not technically runnable.

Aichestra MUST NOT use that responsibility to become the inner worker scheduler.

### Forbidden production seams (Mode C)

- `aichestra-run-{id}` synthetic run ids
- Soft-fail `run-use` then unbound `task-create`
- `bound_writer_from_lead` / `writer_fn` direct lead execution
- `OrchestratedWorkflow` alias and unreachable `_phase_*` agent handlers
- `WorkflowBindings.lead` / `local_worker` used for Mode C `execute_task`
- Hard-coded `agent=opencode` or product-name phase routing as architecture
- Treating discovery (endpoint/model present) as proven ExecutionTarget
- Parent-checkout `.aichestra/attachments/` staging
- `brief_satisfied` / `plan_satisfied` metadata unlocks
- fixed `Phase` graph used as the production Mode C workflow
- `run_all()` hard-coding research → implement → writers → review
- Aichestra deciding universal worker/task ordering
- requiring a Python code change to introduce a different orchestration shape
- treating project-context detection as informational metadata only
- silently creating `.aichestra/speckit/` or another competing Spec Kit
  hierarchy (discover/preserve project canonical location; only establish a
  structure when none exists and configured Spec Kit policy allows it)
- expanding coordinator bootstrap into Aichestra-owned phase→runtime mapping

## Architecture Notes

### Config layering

1. Tracked common defaults/policies
2. OS-specific tracked defaults
3. Untracked machine-local overrides (models/providers/local.enabled /
   Agent Runtime and Model Provider enabled flags; legacy
   `providers.codex|cursor|local-worker.enabled` as compatibility input)
4. Target-project configuration
5. Runtime/task override

### Three modes

- **A Native**: Codex/Cursor used directly; Aichestra does not intercept.
- **B Orca Interactive**: manual Orca use; no automatic full Mode C.
- **C Mode C**: exactly one Orca Run; fails closed without Orca or project root.

### Graceful degradation (precise)

| Condition | Behavior |
|-----------|----------|
| One Agent Runtime unavailable | remove only ExecutionTargets requiring that runtime |
| One Model Provider unavailable | remove only targets requiring that provider |
| One model unavailable | remove only targets requiring that model |
| No local ExecutionTarget | continue with allowed cloud/remote targets |
| No cloud ExecutionTarget | local-only Mode C is allowed when a proven local target exists |
| Codex and Cursor unavailable | continue if any other allowed/capable ExecutionTarget exists |
| Model provider exists but no compatible runtime exists | provider is discovered but not runnable as a worker |
| No runnable coordinator ExecutionTarget | Mode C FAIL |
| **Orca unavailable** | **Mode C FAIL**; Mode A remains independent |

### Orca integration

Inspect current Orca docs before wiring. Prefer Orca primitives for runs,
tasks, workers, worktrees, diffs, handoff, attachments, and provider visibility.
**ONE Mode C invocation = ONE Orca Run.** Child worktree ownership is Orca’s;
adopt results before verification/review. Smallest reliable adapters only.

For `orca orchestration check --wait`, use dispatch-scoped correlation:
ignore/ack unrelated deliveries, continue bounded waiting, and complete only
when the expected dispatch reaches terminal status.

### Attachments

`--attach` paths MUST reach Orca via supported attachment/file primitives.
If the installed Orca version lacks the primitive: document limitation; do not
fake success. Vision tasks MUST NOT go to text-only local models.

### Machine profiler

Capability-based discovery forms independent facts:

```text
machine capabilities
installed Agent Runtimes
configured Model Providers
available models
provider endpoints
runtime/provider compatibility
model capabilities
proven launch strategies
```

The resolver then constructs only real `ExecutionTargets[]`.

Example of non-equivalence:

```text
OpenCode installed       = true
Ollama reachable         = true
qwen model installed     = true

does NOT automatically mean:

ExecutionTarget runnable = true
```

Launch/binding must still be proven. Order stronger hardware tiers before
weaker ones (`powerful` before `capable`). Local inference/execution always
optional.

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

### Execution target launch contract

Aichestra MUST distinguish discovery from proven execution.

A discovered runtime/provider/model combination is runnable only when there is a
verified launch strategy that binds the intended configuration to the actual
agent process supervised by Orca.

Prefer native Orca worker launch when the installed Orca version supports the
required runtime/model configuration.

If native worker launch cannot express a required runtime/provider/model binding,
a bounded Orca-owned terminal bridge MAY be used when supported:

1. create/start the configured Agent Runtime in an Orca-managed terminal;
2. prove that the actual process uses the intended provider/model/endpoint;
3. attach that terminal to an Orca Task/Dispatch using supported Orca primitives;
4. retain Orca Task/Dispatch/lifecycle authority.

The bridge MUST NOT create an Aichestra-owned agent loop or workflow scheduler.

If neither native launch nor a proven terminal bridge exists, that combination is
reported as `unsupported` and MUST NOT be selected.

No specific Agent Runtime or Model Provider is mandatory. OpenCode + Ollama is
one possible execution target, not the canonical local architecture.

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## Implementation sequencing (after this contract update)

1. Land architecture contract tests (MODE-C-001–010, MODE-C-017–020) — this pass.
2. Refactor/remove dual-orchestrator `workflow.py` into thin Mode C controller.
3. Ensure all agent dispatch is Orca-only under one `run_id`.
4. Introduce ExecutionTarget discovery/resolution (migrate legacy `local-worker`).
5. Rework/remove duplicate worktree edit-lock if Orca owns worktrees.
6. Complete real attachment forwarding; runtime/provider enable flags; handoff-in-run.
7. Delete obsolete tests; keep minimal regression net.
8. Only then mark feature tasks complete / converge.

### Gate boundary decision

Maintenance is a synchronous callback reached through the coordinator's Orca
`ask` and Aichestra's `reply`, before writer dispatch. The callback computes
policy only; it does not create Tasks or choose workers. Completion without the
handshake fails closed. Run identity plus settled canonical Tasks and passing
Aichestra gates determine success; a Run is a namespace and need not have its
own terminal status field. A reported failure or an unverifiable receipt blocks
success. Coordinator terminal cleanup uses Orca worker-release and worker-list.
