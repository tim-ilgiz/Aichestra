# Feature Specification: Portable Aichestra Platform

**Feature Branch**: `001-portable-ai-orchestration`

**Created**: 2026-09-09

**Status**: Architecture contract realigned; Mode C production path is a thin
coordinator (`ModeCRunController.run_all` → one `mode_c_agents` Orca handoff +
local Spec Kit / maintenance / verification gates). Ready for maintainer
review once GitHub CI matrix is green.

**Input**: User description: "A developer can clone this repository onto macOS, Windows or Linux, execute the platform bootstrap, authenticate required providers, and receive an equivalent Orca-based AI development environment."

## Architecture status

This specification is the **target** architecture source of truth.

Mode C MUST remain a thin Orca adapter + local deterministic gates. If code
reintroduces a second worker scheduler or Orca-less Mode C fallback, treat that
as a regression against this document — do not weaken the spec.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fresh portable bootstrap (Priority: P1)

A developer clones this repository to an arbitrary path on macOS, Windows, or
Linux, runs the platform bootstrap for that OS, authenticates available
providers, and obtains an equivalent Orca-based development environment.

**Why this priority**: Without portable bootstrap, the platform cannot be adopted.

**Independent Test**: Fresh clone into a non-default path; run OS bootstrap;
doctor reports resolvable repo root and available providers without fixed home
paths.

**Acceptance Scenarios**:

1. **Given** a clone at an arbitrary path on a supported OS, **When** the
   developer runs the OS bootstrap entrypoint, **Then** bootstrap completes
   idempotently and preserves existing user configuration where practical.
2. **Given** machine-local provider settings, **When** the developer commits
   work, **Then** those settings, secrets, model weights, sessions, and caches
   remain untracked.
3. **Given** a later Git pull of orchestration policy changes, **When** the
   developer re-runs idempotent update/bootstrap, **Then** machine-local
   settings are preserved.

---

### User Story 2 - Three modes (Priority: P1)

A developer uses exactly three modes. Aichestra is **not** a second
general-purpose orchestrator.

#### Mode A — Native

The developer runs Codex and/or Cursor IDE / `cursor-agent` directly.
Aichestra and Orca MUST NOT intercept or wrap those invocations.

Examples: `codex`, `cursor`, `cursor-agent`.

#### Mode B — Orca Interactive

The developer opens Orca and works manually with one or more agents through
native Orca UI/CLI. Aichestra MUST NOT start a full Mode C workflow
automatically.

#### Mode C — Aichestra Orchestrated

Mode C **always** goes through Orca:

```text
User → Aichestra → ONE Orca Run → Orca orchestration
         (Codex / Cursor / OpenCode·local-worker / worktrees / tasks / handoffs)
```

Aichestra supplies bootstrap, discovery, policy, configuration, deterministic
gates, verification, and an Orca adapter. Orca owns worker execution, task
lifecycle, worktrees, and handoffs inside that single Run.

**Why this priority**: Interaction style and provider availability vary by
machine; Mode A must remain usable when Mode C cannot run.

**Independent Test**: Architecture contract tests assert Mode C fails closed
without Orca / project root; Mode A remains unintercepted; exactly one
`orchestration run-create` per Mode C invocation.

**Acceptance Scenarios**:

1. **Given** Mode C is requested and Orca is available with a resolved project
   root, **When** the developer runs `aichestra orchestrate`, **Then** exactly
   one Orca Run is created (or one existing Run resumed) and all subsequent
   Mode C agent work uses that same `run_id`.
2. **Given** Mode C is requested, **When** Orca is missing, unreachable, or
   cannot create a Run, **Then** Mode C fails with a clear outcome that Mode C
   is unavailable because Orca is unavailable, and Codex/Cursor/local-worker
   `execute_task` MUST NOT be called. Mode A remains separately usable.
3. **Given** Mode C is requested without a resolvable target project root,
   **When** orchestration would start, **Then** it fails before any Orca Run
   or provider execution (`FAIL: Mode C requires a target project root`).
4. **Given** Mode B (Orca interactive, no Mode C start), **When** the developer
   works in Orca, **Then** Aichestra does not auto-start full multi-role Mode C.
5. **Given** Mode A, **When** the developer runs native Codex/Cursor,
   **Then** those commands remain unchanged and unintercepted.
6. **Given** Codex preferred and available (and enabled), **When** Orca selects
   a lead worker under Mode C policy, **Then** Codex is preferred and Cursor is
   the fallback — selection happens inside Orca using Aichestra policy, not via
   a hidden Aichestra direct-execution fallback.
7. **Given** local-worker disabled or missing, **When** Mode C runs with Orca,
   **Then** Mode C remains useful as cloud-oriented Orca orchestration.
8. **Given** Codex and/or Cursor independently disabled in policy, **When**
   discovery/orchestration runs, **Then** the platform does not hard-crash;
   Mode C uses remaining enabled Orca-visible workers; Mode A for enabled
   natives remains independent.

---

### User Story 3 - Research, writers, review, verification (Priority: P2)

Mode C policy **inputs** (not a second Python orchestrator) may include:
classify / Spec Kit path → research if useful → lead implementation →
maintenance-reviewer decisions → optional test/doc writers → deterministic
verification → lead review.

**Agent steps** (research, implement, writers, lead review) MUST execute as
Orca tasks/workers on the **same** Orca Run. Aichestra MUST NOT call
`CodexProvider.execute_task`, `CursorProvider.execute_task`, or
`LocalWorkerProvider.execute_task` for those roles in Mode C.

**Deterministic gates** (classify heuristics, maintenance-reviewer,
verification-runner) MAY run in Aichestra and MUST feed results back into the
same Orca Run for final review.

Research via local-worker/OpenCode:

```text
Aichestra → existing Orca Run → Orca research task → OpenCode/local-worker
```

If local-worker is absent, Orca may assign a cloud research worker or research
may be skipped per policy — Aichestra must not become the worker scheduler.

Test/doc writers and final lead review follow the same rule: maintenance
decisions are local/deterministic; execution is Orca-owned under the same Run.

**Why this priority**: Quality gates without documentation/test bloat; no dual
scheduler.

**Independent Test**: Fixture maintenance decisions; verifier exit codes;
contract tests that lead/local-worker adapters receive zero Mode C
`execute_task` calls while Orca receives agent tasks under one `run_id`.

**Acceptance Scenarios**:

1. **Given** research is useful, **When** Mode C runs, **Then** research is an
   Orca task on the existing Run (compacted before cloud lead handoff).
2. **Given** implementation completes on the Run, **When** gates proceed,
   **Then** maintenance-reviewer runs before writers and may choose none.
3. **Given** TEST/DOC required, **When** writers run, **Then** they are Orca
   tasks on the same Run (prefer updating existing canonical artifacts).
4. **Given** verification is required, **When** verification-runner runs,
   **Then** real target-repo commands and exit codes are authoritative; results
   attach to the same Mode C / Orca Run for final review.
5. **Given** final lead review, **When** it executes, **Then** it is an Orca
   task/worker on the same Run — not a direct Aichestra→Codex call.

---

### User Story 4 - Staging-only secure diagnostics (Priority: P2)

A developer can run STAGING SSH diagnostics only. Dangerous commands are
rejected before remote execution; diagnostic output is sanitized before cloud
handoff.

**Why this priority**: Security boundaries must be structural.

**Independent Test**: Fixture suites reject destructive commands before any
remote execution and assert sanitization of sensitive output.

**Acceptance Scenarios**:

1. **Given** a staging diagnostic request on the allowlist, **When** executed,
   **Then** it runs read-only under structural enforcement.
2. **Given** a dangerous command (`sudo`, `rm`, restart/stop, `docker rm/stop`,
   `kubectl apply/delete`, or remote writes), **When** submitted, **Then** it is
   rejected before remote execution.
3. **Given** diagnostic output containing secrets-like material, **When** handed
   to a cloud agent, **Then** output is sanitized first.
4. **Given** any production SSH target, **When** integration is attempted,
   **Then** no production SSH path exists in the platform.

---

### User Story 5 - Isolation, CI, and truthful validation (Priority: P2)

Two fixture repositories prove project isolation. CI validates core code on
macOS, Windows, and Linux without consuming real Codex/Cursor quota. Smoke
reporting truthfully distinguishes live Mac validation from not-yet-live
Windows/Linux smoke tests.

**Why this priority**: Portability and honesty of validation status.

**Independent Test**: Isolation fixtures plus CI matrix plus smoke-report
assertions.

**Acceptance Scenarios**:

1. **Given** two fixture repositories, **When** tasks run against each,
   **Then** task/config/runtime state does not leak between them.
2. **Given** GitHub Actions, **When** the matrix runs, **Then** macOS, Windows,
   and Linux jobs validate core code without real provider quota use.
3. **Given** Mac live smoke testing, **When** results are reported, **Then**
   only actually available components are marked live-validated.
4. **Given** no Windows/Linux live smoke yet, **When** status is reported,
   **Then** those OSes are not claimed as live-tested.

---

### User Story 6 - Machine profile and optional local inference (Priority: P1)

A developer can inspect a deterministic machine profile and local-runtime
discovery results. Local inference remains optional even on powerful hardware.
Installed models are preferred over automatic downloads; large downloads require
explicit approval. Model/worker selection is **capability-based** (OS, CPU, RAM,
disk, GPU/accelerator, installed runtimes/models) — never a production
dependency on a specific Mac identity.

Production MUST NOT special-case host identities such as `M4_PRO_24GB` /
`apple-m4-pro-24gb-example` except as optional **test fixtures**. Capability
routing MUST order stronger tiers before weaker ones so `powerful` (≥32GB+GPU)
is reachable and not shadowed by a ≥24GB branch.

**Why this priority**: Hardware and local AI availability differ widely.

**Independent Test**: Fake hardware/runtime fixtures; capability ordering;
disabled local; vision vs text-only.

**Acceptance Scenarios**:

1. **Given** any supported OS, **When** machine-profiler runs, **Then** it
   reports structured OS/CPU/RAM/disk/GPU/local-AI facts without using an LLM
   as the source of hardware facts.
2. **Given** `local.enabled = false`, **When** Mode C runs (with Orca),
   **Then** cloud-oriented Mode C remains useful.
3. **Given** a suitable installed local model, **When** a local worker is
   needed via Orca, **Then** that model is preferred over downloading another.
4. **Given** no suitable model, **When** bootstrap/local setup would download,
   **Then** the user must explicitly approve before any large download.
5. **Given** a vision task and a text-only local model, **When** routing
   occurs, **Then** a vision-capable provider is selected instead.

---

### Edge Cases

- All optional providers (Codex, Cursor, local-worker) missing: doctor/bootstrap
  still succeed; Mode A for any remaining native tool; Mode C only if Orca plus
  at least one configured worker path remains viable.
- Orca missing: Mode C FAIL; Mode A remains usable. Never “fall back to Codex
  as orchestrator.”
- Partial bootstrap interruption then re-run: remains idempotent.
- Clone path contains spaces or non-ASCII characters: repo-root resolution still
  works.
- Staging alias unknown: fail closed with an actionable external-blocker message.
- Quota exhaustion on Codex: prefer handoff **inside** the existing Orca Run;
  if automatic detection is unreliable, one executable manual handoff action
  (not a placeholder command the user must assemble).
- Target project already has Factory tooling: preserve it unless migration is
  explicitly approved.
- Local runtime under resource pressure: degrade or disable that worker; do not
  terminate unrelated user applications to free memory.
- Concurrent editing: policy may forbid two agents editing one checkout;
  worktree create/lifecycle/ownership belongs to Orca — do not duplicate with a
  bespoke Aichestra worktree/session manager when Orca provides the primitive.
- Custom local endpoints: only when explicitly configured; no network-wide LLM
  scanning.
- Attachments (`--attach`): must be forwarded through supported Orca/provider
  file mechanisms; metadata-only path lists are insufficient. If Orca cannot
  attach, document the limitation honestly — do not fake acceptance.

## Requirements *(mandatory)*

### Mode C architectural MUST requirements

These are the non-negotiable Mode C contract. Contract tests MUST cover them.

- **MODE-C-001**: Mode C MUST require Orca.
- **MODE-C-002**: One Mode C invocation MUST create exactly one Orca Run
  (`orca orchestration run-create` count == 1 for a fresh start; resume reuses
  one existing `run_id` without additional `run-create` for classify /
  implement / research / maintenance / writers / verification report / review).
- **MODE-C-003**: All Mode C agent executions MUST be owned/dispatched by Orca.
- **MODE-C-004**: Aichestra MUST NOT directly execute Codex/Cursor/local-worker
  inside Mode C for implementation, research, writers, or lead review.
- **MODE-C-005**: Mode C MUST require a resolved target project root
  (`--project-root` or safe cwd resolution). Unresolved root → FAIL before
  orchestration.
- **MODE-C-006**: Orca unavailability MUST fail Mode C rather than invoke a
  provider directly. Correct message shape: Mode C unavailable because Orca is
  unavailable; use Mode A for direct Codex/Cursor.
- **MODE-C-007**: All Mode C tasks/workers/worktrees MUST belong to the same
  Orca orchestration context (`run_id`).
- **MODE-C-008**: Mode A MUST continue to support direct Codex/Cursor
  independently.
- **MODE-C-009**: Provider availability/enabled state MUST be configurable
  independently (Codex, Cursor, local-worker; Mode C still requires Orca).
- **MODE-C-010**: Attachments supplied to Mode C MUST be forwarded through
  supported Orca/provider mechanisms rather than metadata-only routing.

### Functional Requirements

- **FR-001**: Orca is the primary interactive UI and the **only** Mode C
  orchestration control plane for agent/worker execution.
- **FR-002**: Direct native use of Codex and Cursor remains independent and
  unchanged (Mode A).
- **FR-003**: Codex is the preferred lead provider (policy input to Orca).
- **FR-004**: Cursor is a supported fallback lead (policy input to Orca).
- **FR-005**: A local worker based on OpenCode + an optional local inference
  runtime (initially Ollama) is optional and referred to as `local-worker`.
- **FR-006**: The platform works when local inference is disabled/unavailable
  (Mode C cloud-oriented via Orca; Mode A unaffected).
- **FR-007**: The platform gracefully handles missing Codex (Orca may use
  Cursor; Mode A Cursor remains).
- **FR-008**: The platform gracefully handles missing Cursor (Orca may use
  Codex; Mode A Codex remains).
- **FR-009**: The same tracked repository supports macOS, Windows and Linux.
- **FR-010**: Repository clone location is arbitrary.
- **FR-011**: No production code/config may depend on a fixed developer home
  path.
- **FR-012**: Important portable helper logic is cross-platform.
- **FR-013**: Platform-specific bootstrap entrypoints exist for macOS, Windows
  and Linux.
- **FR-014**: Bootstrap is idempotent.
- **FR-015**: Bootstrap preserves existing user configuration where practical.
- **FR-016**: Machine-local model/provider settings are untracked.
- **FR-017**: Local model size is machine-specific rather than globally
  hard-coded.
- **FR-018**: Hardware guidance is capability-based. Named Mac examples (e.g.
  M4 Pro 24 GB) are test fixtures / illustrations only — not production
  architecture identity. Profile routing MUST NOT make stronger tiers
  unreachable.
- **FR-019**: Orca/Codex/Cursor/OpenCode/Ollama binaries and model weights are
  not stored in Git.
- **FR-020**: Runtime sessions/caches/logs/secrets are not stored in Git.
- **FR-021**: Orchestrated research is an Orca task on the Mode C Run;
  repository-researcher prefers local-worker **via Orca** when enabled.
- **FR-022**: Research output is compacted before being handed to cloud lead
  agents.
- **FR-023**: A maintenance-reviewer (deterministic Aichestra gate) runs after
  production implementation signals and before test or documentation writers.
- **FR-024**: maintenance-reviewer can explicitly decide that NO new tests are
  needed.
- **FR-025**: maintenance-reviewer can explicitly decide that NO documentation
  update is needed.
- **FR-026**: Test writer (Orca task) prefers updating/parameterizing existing
  tests before creating new files.
- **FR-027**: Documentation writer (Orca task) prefers updating an existing
  canonical document.
- **FR-028**: Spec Kit is used proportionally to task size/risk as **policy
  input** for Orca orchestration — not justification for a second Python
  workflow engine.
- **FR-029**: Target-project AI Factory/Factory tooling is preserved unless
  migration is explicitly approved.
- **FR-030**: Only STAGING SSH diagnostics are integrated.
- **FR-031**: No production SSH integration exists.
- **FR-032**: Stage diagnostics enforce typed/controlled read-only operations
  and an allowlist outside of the LLM prompt.
- **FR-033**: Dangerous commands such as sudo, rm, restart, stop, docker
  rm/stop, kubectl apply/delete and remote writes are rejected.
- **FR-034**: Diagnostic output is sanitized before cloud handoff.
- **FR-035**: Codex-to-Cursor handoff preserves bounded useful task context and
  SHOULD occur inside the existing Orca Run.
- **FR-036**: Automatic quota fallback is implemented only if reliable with
  current Orca primitives; otherwise one-action **executable** manual handoff
  is provided (not a non-executable suggested-command placeholder).
- **FR-037**: At least two independent fixture repositories prove project
  isolation.
- **FR-038**: GitHub Actions validate core code on macOS, Windows and Linux.
- **FR-039**: CI must not consume real Codex/Cursor account quota.
- **FR-040**: A cross-platform doctor/health check exists.
- **FR-041**: The platform can update through Git pull + an idempotent
  update/bootstrap operation without losing machine-local settings.
- **FR-042**: The project includes a reusable later maintenance-audit workflow
  for reducing test/documentation/spec bloat in target projects.
- **FR-043**: Three distinct modes are supported: Native (A), Orca Interactive
  (B), and Mode C (exactly one Orca Run + Aichestra policy/gates). Aichestra
  MUST NOT implement a general-purpose workflow engine that duplicates Orca
  (phase state machine that itself schedules workers is forbidden as the Mode C
  architecture).
- **FR-044**: Aichestra itself MUST NOT require Docker or a virtual machine to
  run; WSL MUST NOT be required for Windows.
- **FR-045**: Aichestra MUST NOT build a duplicate general-purpose orchestrator
  or require CAO; Mode C agent work MUST go through Orca under one Run;
  adapters must be the smallest reliable wrappers over Orca.
- **FR-046**: A deterministic machine-profiler obtains OS, architecture, CPU,
  memory, disk, and GPU/accelerator facts via ordinary system APIs (not an LLM).
- **FR-047**: Local-runtime-discovery supports Ollama initially and is
  extensible to other local runtimes without rewriting orchestration logic.
- **FR-048**: `local.enabled = false` remains valid on any hardware; local
  inference is always optional.
- **FR-049**: Before recommending/installing a local model, installed models are
  inspected and reused when capable; large downloads require explicit approval.
- **FR-050**: Model/worker selection evaluates AVAILABLE, CAPABLE, ALLOWED, and
  PREFERRED; vision tasks must not be sent to text-only models merely because
  they are local.
- **FR-051**: Orchestration refers to `local-worker` rather than a hard-coded
  global model id; machine-local selection stays untracked.
- **FR-052**: Local inference is resource-aware (prefer one local worker; unload
  idle models where supported); never terminate unrelated user apps to free
  memory.
- **FR-053**: A reusable `repository-researcher` role prefers local-worker via
  Orca, is normally read-only for production code, and emits a compact
  structured research summary.
- **FR-054**: Mode C uses Orca for agent lifecycle. Aichestra may apply
  deterministic policy helpers (classify / Spec Kit path, maintenance decision,
  verification runner, provider/security policy, context compaction). Tests and
  docs are not unconditional stages.
- **FR-055**: maintenance-reviewer emits structured TEST_DECISION, TEST_SCOPE,
  DOC_DECISION, DOC_TARGETS, ADR_REQUIRED, SPEC_UPDATE, and RATIONALE.
- **FR-056**: A verification-runner executes real target-repository build/test
  commands; process exit codes are authoritative; LLM opinion cannot override a
  non-zero exit code. Results belong to the same Mode C Run.
- **FR-057**: Editing agents MUST NOT edit the same checkout concurrently.
  Prefer native Orca worktree primitives for create/lifecycle/ownership. Custom
  Aichestra edit-lock/worktree managers that duplicate Orca MUST be removed or
  reduced to thin policy checks.
- **FR-058**: Ordinary files/logs/PDFs/images participate via Orca/provider
  attachment capabilities (MODE-C-010); large text may be locally summarized;
  vision routes only to vision-capable providers.
- **FR-059**: Staging diagnostics expose typed/controlled read operations via
  structured builders, not unrestricted remote shell strings.
- **FR-060**: Config precedence is tracked defaults → OS defaults →
  machine-local → target-project → runtime/task override.
- **FR-061**: Doctor reports PASS/WARN/FAIL for Aichestra, machine, platform,
  cloud providers, local AI, project, and staging without printing credentials.
- **FR-062**: Automated tests use fake providers covering success, unavailable,
  quota, auth failure, timeout, and generic error without consuming real quota.
- **FR-063**: Spec Kit proportionality distinguishes SMALL / MEDIUM /
  LARGE-HIGH-RISK paths as policy for Orca; AI Factory is not added to
  Aichestra v1.
- **FR-064**: Operator documentation describes actual daily commands for native
  use, Orca interactive, orchestrated start, handoff, local disable, research,
  machine profile, staging diagnostics, worktree diff, and Git update.
- **FR-065**: Graceful degradation means: missing Codex → Orca may use Cursor;
  missing Cursor → Orca may use Codex; missing local-worker → cloud-oriented
  Mode C; both Codex and Cursor unavailable → fail or degraded Orca flow per
  remaining configured workers; **missing Orca → Mode C FAIL, Mode A remains**.
  Missing Orca MUST NEVER authorize Aichestra to become the orchestrator.
- **FR-066**: Providers Codex, Cursor, and local-worker MUST support independent
  `enabled` configuration without breaking the platform (MODE-C-009).
- **FR-067**: Mode C MUST NOT synthesize Orca `run_id` values (including
  `aichestra-run-*`). If Orca is reachable but returns no `run_id`, Mode C
  FAIL CLOSED.
- **FR-068**: Same-run task binding is fail-closed: `run-use` failure MUST
  prevent `task-create`; never retry `task-create` without `--run` after a
  failed `--run` attempt. Handoff execute MUST bind `--run` to the Mode C
  `run_id`.
- **FR-069**: Provider disable/`available=False` is authoritative — Mode C MUST
  NOT dispatch disabled or unavailable Codex/Cursor/local-worker agents, and
  MUST NOT invent a preferred lead when none is available.
- **FR-070**: Spec Kit MEDIUM/LARGE artifacts MUST be produced by the production
  lifecycle under the target project's `.aichestra/speckit/` (or Spec Kit
  tooling). Readiness is file-backed; test-only `*_satisfied` metadata MUST NOT
  unlock implement.
- **FR-071**: Mode C attachments MUST NOT be staged into the parent project
  checkout (no parent `.aichestra/attachments/`). Stage outside the repo or
  pass absolute paths to Orca `--attach`.
- **FR-072**: When verify commands are empty, verification-runner MUST use safe
  auto-detect (`.aichestra/project.json` verify first; else .NET / Python /
  Node heuristics). Bootstrap MAY set `bootstrap_complete=True` when doctor is
  ok and no blocking remaining steps remain (no forever-pending advisories).
- **FR-073**: Handoff CLI MUST require `--run-id` or reliably auto-resolve a
  recent Mode C run; README MUST match executable UX. Wait-event success
  requires dispatch-id correlation when an expected dispatch id exists.

### Key Entities

- **Provider Profile**: Discovered availability and role of Codex, Cursor,
  OpenCode/local-runtime, and Orca on the current machine; per-provider enabled
  flags.
- **Hardware Profile / Machine Profile**: Deterministic capability facts and
  machine-local defaults for local model class and worker count (untracked).
- **Local Runtime Profile**: Discovered local inference runtimes, models, and
  capabilities.
- **Orchestration Policy**: Tracked, portable rules for roles, Spec Kit
  proportionality, and maintenance-reviewer gates (inputs to Orca Mode C).
- **User Mode**: Native (A), Orca Interactive (B), or Mode C (Orchestrated).
- **Orca Run**: The single orchestration context for one Mode C invocation
  (`run_id`).
- **Staging Diagnostic Request**: Candidate remote command subject to typed
  builders, allowlist, and sanitization.
- **Fixture Repository**: Isolated sample project used to prove non-leakage.
- **Validation Report**: Truthful record of automated vs live smoke coverage by
  OS and component.
- **Maintenance Review Decision**: Structured gate output controlling tests,
  docs, ADR, and spec updates.
- **Handoff Packet**: Bounded Codex→Cursor continuation context inside the
  same Orca Run when possible.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A fresh clone can resolve its own repository root from an
  arbitrary path on each supported OS.
- **SC-002**: Automated test matrix passes on macOS, Windows and Linux.
- **SC-003**: No tracked production configuration contains the current
  developer's home path or private credentials.
- **SC-004**: Provider-availability tests prove useful operation with no local
  worker, no Codex, and no Cursor (Mode A / degraded Mode C as applicable).
- **SC-005**: maintenance-reviewer fixture tests prove "no tests needed",
  "tests needed", "no docs needed", and "docs update needed".
- **SC-006**: Destructive stage command fixture tests are rejected before remote
  execution.
- **SC-007**: Two fixture repositories do not leak task/config/runtime state
  into each other.
- **SC-008**: Current Mac live smoke testing truthfully verifies the components
  actually available there.
- **SC-009**: Windows/Linux are not reported as live-tested until real smoke
  tests have actually run there.
- **SC-010**: A developer can update orchestration policies through Git and
  preserve machine-local settings.
- **SC-011**: Machine-profiler returns structured facts without LLM guessing.
- **SC-012**: Capability routing refuses vision tasks for text-only local
  models.
- **SC-013**: With `local.enabled = false`, cloud-oriented Mode C (with Orca)
  remains useful.
- **SC-014**: Large local model download path requires explicit approval in
  tests/fixtures.
- **SC-015**: verification-runner uses real exit codes from fixture project
  commands.
- **SC-016**: Architecture contract tests prove MODE-C-001–MODE-C-010
  (including exactly one `run-create` and no direct Mode C provider execution).

## Clarifications

### Session 2026-09-09

- Q: Should automatic Codex→Cursor quota fallback be mandatory in v1?
  → A: Only if reliable with current Orca primitives; otherwise ship documented
  one-action manual handoff (FR-036). Prefer handoff inside the existing Run.
- Q: Is WSL required for Windows support?
  → A: No. Windows native is first-class; WSL is optional and never required.
- Q: May production SSH diagnostics be added behind a flag?
  → A: No. Production SSH integration is out of scope permanently for this
  feature.
- Q: Should Factory tooling be migrated into this repo?
  → A: No. Do not install AI Factory into this repository; preserve
  target-project Factory tooling unless explicitly approved to migrate.
- Q: Where do machine-local model defaults live?
  → A: Untracked machine-local configuration. Capability-based suggestions
  only; no production hard-coding of a specific Mac identity.
- Q: If Orca is unavailable, may Mode C fall back to direct Codex?
  → A: No. Mode C fails. Mode A remains for direct Codex/Cursor (MODE-C-006).
- Q: May Aichestra keep a general-purpose phase engine that schedules workers?
  → A: No. That duplicates Orca and is forbidden as Mode C architecture
  (FR-043/045). Small deterministic policy helpers are allowed.

## Assumptions

- Developers can install or authenticate Orca and providers on their machines;
  this feature supplies policy, bootstrap, discovery, gates, and Orca adapters.
- Spec Kit remains the process layer for large/high-risk work; small changes may
  skip full SDD. Spec Kit does not justify a second orchestrator.
- CI uses fakes/stubs for providers so no real account quota is consumed.
- Live Windows/Linux smoke tests may follow after Mac live validation and CI
  matrix confidence.
- "Equivalent environment" means equivalent orchestration capabilities and
  policies across OSes, not identical binary install paths.
- Current production Mode C uses a thin coordinator (`run_all` → one
  `mode_c_agents` Orca handoff + local gates). Reintroducing an Aichestra-owned
  agent phase scheduler is a regression against this spec — not a reason to
  soften requirements.
