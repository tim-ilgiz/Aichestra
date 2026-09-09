# Feature Specification: Portable Aichestra Platform

**Feature Branch**: `001-portable-ai-orchestration`

**Created**: 2026-09-09

**Status**: Clarified

**Input**: User description: "A developer can clone this repository onto macOS, Windows or Linux, execute the platform bootstrap, authenticate required providers, and receive an equivalent Orca-based AI development environment."

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

### User Story 2 - Three modes and graceful multi-provider orchestration (Priority: P1)

A developer can use three distinct modes: (A) native Codex/Cursor without
Aichestra orchestration, (B) Orca interactive with one agent and no automatic
full orchestration, and (C) an explicitly started Mode C session that creates
or resumes **one Orca Run** as the orchestration engine. Aichestra supplies
policy, bootstrap, and deterministic gates; it MUST NOT act as a second
general-purpose orchestrator. Orca is the opt-in control plane. Codex is
preferred lead, Cursor is fallback, and OpenCode+local-runtime is an optional
worker. Missing providers do not disable the whole platform. Aichestra does
not require Docker or a VM to run.

**Why this priority**: Provider availability and interaction style vary by
machine and account.

**Independent Test**: Fake/absent-provider fixtures prove useful operation with
no local worker, no Codex, and no Cursor independently; mode separation is
asserted by contract tests.

**Acceptance Scenarios**:

1. **Given** Mode C is requested and Orca is available, **When** the developer
   starts Mode C with `--project-root`, **Then** Aichestra creates or resumes
   exactly one Orca Run and Orca is the orchestration control plane for agent
   work (Aichestra supplies policy/gates only).
2. **Given** Mode C is requested, **When** Orca is unavailable or unreachable,
   **Then** Mode C fails closed before any worker execution with a clear
   ORCA_UNAVAILABLE outcome and MUST NOT fall back to direct Codex/Cursor
   (Mode A remains separate).
3. **Given** Mode C is requested without a valid `--project-root`, **When**
   orchestration starts, **Then** it fails before any provider execution
   (no ambient-cwd write-agent).
4. **Given** the developer opens Orca and works with one agent (Mode B),
   **When** no Mode C Run is started, **Then** full multi-role orchestration
   is not automatically started.
5. **Given** Codex is available, **When** a lead is selected, **Then** Codex is
   preferred.
6. **Given** Codex is unavailable and Cursor is available, **When** a lead is
   selected, **Then** Cursor is used as fallback.
7. **Given** local inference is disabled or missing, **When** Mode C runs,
   **Then** the platform remains useful without the local worker (other Orca
   agents / gates still apply).
8. **Given** native Codex or Cursor CLI/IDE usage (Mode A), **When** the
   developer works outside Orca, **Then** native commands remain unchanged and
   unintercepted.
9. **Given** cloud-only configuration, **When** local runtime is absent,
   **Then** research/maintenance utilities that require local inference degrade
   without blocking lead workflows.

---

### User Story 3 - Research, review, verification, and anti-bloat gates (Priority: P2)

Mode C policy schedule is: classify → research if useful → lead implementation
→ maintenance-reviewer gate → optional test/docs workers → deterministic
verification → lead review. **Agent steps** (research, implement, writers,
lead review) execute as tasks/workers on the **same Orca Run** (typically in
an Orca child worktree). **Deterministic gates** (classify heuristics,
maintenance-reviewer, verification-runner) run in Aichestra against the
adopted worktree. The repository-researcher prefers the local worker via Orca
and produces a compact structured summary. The maintenance-reviewer emits
structured TEST/DOC/ADR/SPEC decisions and may explicitly choose none. Tests
and docs are never unconditional stages. A verification-runner executes real
target-repo commands; LLM opinion is not authoritative for pass/fail.

**Why this priority**: Controls quality and prevents documentation/test bloat.

**Independent Test**: Fixture decisions prove "no tests needed", "tests needed",
"no docs needed", and "docs update needed"; verifier uses exit codes.

**Acceptance Scenarios**:

1. **Given** an orchestrated research task, **When** research completes,
   **Then** output is compacted before cloud lead handoff.
2. **Given** production implementation is complete, **When** the workflow
   proceeds, **Then** maintenance-reviewer runs before test or documentation
   generation and emits structured decisions.
3. **Given** maintenance-reviewer decides no tests or no docs are needed,
   **When** downstream writers run, **Then** they are skipped and prefer
   updating existing canonical artifacts when work is needed.
4. **Given** verification is required, **When** the verification-runner runs,
   **Then** it uses the target project's real commands and exit codes.

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
explicit approval. Model/worker selection is capability-based. Machine-local
model choice is never committed.

**Why this priority**: Hardware and local AI availability differ widely; wrong
defaults create hard dependencies or unwanted downloads.

**Independent Test**: Fake hardware/runtime fixtures cover capable/weak
machines, missing runtimes, disabled local, capability mismatch, and
multi-model selection.

**Acceptance Scenarios**:

1. **Given** any supported OS, **When** machine-profiler runs, **Then** it
   reports structured OS/CPU/RAM/disk/GPU/local-AI facts without using an LLM
   as the source of hardware facts.
2. **Given** `local.enabled = false`, **When** orchestration runs, **Then**
   cloud-only mode remains fully useful.
3. **Given** a suitable installed local model, **When** a local worker is
   needed, **Then** that model is preferred over downloading another.
4. **Given** no suitable model, **When** bootstrap/local setup would download,
   **Then** the user must explicitly approve before any large download.
5. **Given** a vision task and a text-only local model, **When** routing
   occurs, **Then** a vision-capable provider is selected instead.

---

### Edge Cases

- All optional providers missing: doctor and bootstrap still succeed with clear
  degraded-mode guidance.
- Partial bootstrap interruption then re-run: remains idempotent.
- Clone path contains spaces or non-ASCII characters: repo-root resolution still
  works.
- Staging alias unknown: fail closed with an actionable external-blocker message.
- Quota exhaustion on Codex: if automatic fallback is unreliable with current
  Orca primitives, provide one-action manual handoff to Cursor and document it.
- Target project already has Factory tooling: preserve it unless migration is
  explicitly approved.
- Local runtime under resource pressure: degrade or disable that worker; do not
  terminate unrelated user applications to free memory.
- Independent editing agents: must not silently share one checkout; prefer Orca
  worktrees or serial editing.
- Custom local endpoints: only when explicitly configured; no network-wide LLM
  scanning.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Orca is the primary interactive UI/control plane for orchestrated
  work.
- **FR-002**: Direct native use of Codex and Cursor remains independent and
  unchanged.
- **FR-003**: Codex is the preferred lead provider.
- **FR-004**: Cursor is a supported fallback lead.
- **FR-005**: A local worker based on OpenCode + an optional local inference
  runtime (initially Ollama) is optional and referred to as `local-worker`.
- **FR-006**: The platform works when local inference is disabled/unavailable.
- **FR-007**: The platform gracefully handles missing Codex.
- **FR-008**: The platform gracefully handles missing Cursor.
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
- **FR-018**: Hardware profiles are examples / validation fixtures only. The
  Apple M4 Pro 24 GB profile illustrates a ~14B-class local worker with one
  inference worker for current Mac validation; it MUST NOT be treated as a
  global product requirement. Profile selection is driven by measured
  machine facts (memory/GPU), not a hard-coded Mac identity.
- **FR-019**: Orca/Codex/Cursor/OpenCode/Ollama binaries and model weights are
  not stored in Git.
- **FR-020**: Runtime sessions/caches/logs/secrets are not stored in Git.
- **FR-021**: Orchestrated tasks support local repository research.
- **FR-022**: Research output is compacted before being handed to cloud lead
  agents.
- **FR-023**: A maintenance-reviewer runs after production implementation and
  before test or documentation generation.
- **FR-024**: maintenance-reviewer can explicitly decide that NO new tests are
  needed.
- **FR-025**: maintenance-reviewer can explicitly decide that NO documentation
  update is needed.
- **FR-026**: Test writer prefers updating/parameterizing existing tests before
  creating new files.
- **FR-027**: Documentation writer prefers updating an existing canonical
  document.
- **FR-028**: Spec Kit is used proportionally to task size/risk.
- **FR-029**: Target-project AI Factory/Factory tooling is preserved unless
  migration is explicitly approved.
- **FR-030**: Only STAGING SSH diagnostics are integrated.
- **FR-031**: No production SSH integration exists.
- **FR-032**: Stage diagnostics enforce typed/controlled read-only operations
  and an allowlist outside of the LLM prompt.
- **FR-033**: Dangerous commands such as sudo, rm, restart, stop, docker
  rm/stop, kubectl apply/delete and remote writes are rejected.
- **FR-034**: Diagnostic output is sanitized before cloud handoff.
- **FR-035**: Codex-to-Cursor handoff preserves bounded useful task context.
- **FR-036**: Automatic quota fallback is implemented only if reliable with
  current Orca primitives; otherwise one-action manual handoff is provided and
  documented.
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
  single-agent (B), and Mode C (explicitly started or resumed **one Orca Run**
  with Aichestra policy/gates — not a second workflow engine).
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
- **FR-053**: A reusable `repository-researcher` role prefers local-worker,
  is normally read-only for production code, and emits a compact structured
  research summary.
- **FR-054**: Mode C policy schedule is classify → research if useful → lead
  implement → maintenance-reviewer → optional test/docs workers → deterministic
  verification → lead review; agent steps are Orca tasks on one Run; gates are
  local/deterministic on the adopted worktree; tests/docs are not unconditional.
- **FR-055**: maintenance-reviewer emits structured TEST_DECISION, TEST_SCOPE,
  DOC_DECISION, DOC_TARGETS, ADR_REQUIRED, SPEC_UPDATE, and RATIONALE.
- **FR-056**: A verification-runner executes real target-repository build/test
  commands; process exit codes are authoritative.
- **FR-057**: Editing agents prefer Orca worktrees or serial execution; never
  `git reset --hard` the user's real working copy merely to revert agent work.
- **FR-058**: Ordinary files/logs/PDFs/images participate via Orca/provider
  capabilities; large text may be locally summarized; vision routes only to
  vision-capable providers.
- **FR-059**: Staging diagnostics expose typed/controlled read operations via
  structured builders, not unrestricted remote shell strings.
- **FR-060**: Config precedence is tracked defaults → OS defaults →
  machine-local → target-project → runtime/task override.
- **FR-061**: Doctor reports PASS/WARN/FAIL for Aichestra, machine, platform,
  cloud providers, local AI, project, and staging without printing credentials.
- **FR-062**: Automated tests use fake providers covering success, unavailable,
  quota, auth failure, timeout, and generic error without consuming real quota.
- **FR-063**: Spec Kit proportionality distinguishes SMALL / MEDIUM /
  LARGE-HIGH-RISK paths; AI Factory is not added to Aichestra v1.
- **FR-064**: Operator documentation describes actual daily commands for native
  use, Orca interactive, orchestrated start, handoff, local disable, research,
  machine profile, staging diagnostics, worktree diff, and Git update.

### Key Entities

- **Provider Profile**: Discovered availability and role of Codex, Cursor,
  OpenCode/local-runtime, and Orca on the current machine.
- **Hardware Profile / Machine Profile**: Deterministic machine facts and
  machine-local defaults for local model class and worker count (untracked).
- **Local Runtime Profile**: Discovered local inference runtimes, models, and
  capabilities.
- **Orchestration Policy**: Tracked, portable rules for roles, Spec Kit
  proportionality, and maintenance-reviewer gates.
- **User Mode**: Native, Orca Interactive, or Orchestrated.
- **Staging Diagnostic Request**: Candidate remote command subject to typed
  builders, allowlist, and sanitization.
- **Fixture Repository**: Isolated sample project used to prove non-leakage.
- **Validation Report**: Truthful record of automated vs live smoke coverage by
  OS and component.
- **Maintenance Review Decision**: Structured gate output controlling tests,
  docs, ADR, and spec updates.
- **Handoff Packet**: Bounded Codex→Cursor continuation context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A fresh clone can resolve its own repository root from an
  arbitrary path on each supported OS.
- **SC-002**: Automated test matrix passes on macOS, Windows and Linux.
- **SC-003**: No tracked production configuration contains the current
  developer's home path or private credentials.
- **SC-004**: Provider-availability tests prove useful operation with no local
  worker, no Codex, and no Cursor.
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
- **SC-013**: With `local.enabled = false`, cloud-only workflows remain useful.
- **SC-014**: Large local model download path requires explicit approval in
  tests/fixtures.
- **SC-015**: verification-runner uses real exit codes from fixture project
  commands.

## Clarifications

### Session 2026-09-09

- Q: Should automatic Codex→Cursor quota fallback be mandatory in v1?
  → A: Only if reliable with current Orca primitives; otherwise ship documented
  one-action manual handoff (FR-036).
- Q: Is WSL required for Windows support?
  → A: No. Windows native is first-class; WSL is optional and never required.
- Q: May production SSH diagnostics be added behind a flag?
  → A: No. Production SSH integration is out of scope permanently for this
  feature.
- Q: Should Factory tooling be migrated into this repo?
  → A: No. Do not install AI Factory into this repository; preserve
  target-project Factory tooling unless explicitly approved to migrate.
- Q: Where do machine-local model defaults live?
  → A: Untracked machine-local configuration. Tracked code may ship named
  hardware profiles (for example an M4 Pro 24 GB conservative 14B /
  one-worker default) without embedding host-specific paths.

## Assumptions

- Developers can install or authenticate Orca and providers on their machines;
  this feature supplies orchestration, bootstrap, discovery, and safety rails.
- Spec Kit remains the process layer for large/high-risk work; small changes may
  skip full SDD.
- CI uses fakes/stubs for providers so no real account quota is consumed.
- Live Windows/Linux smoke tests may follow after Mac live validation and CI
  matrix confidence.
- "Equivalent environment" means equivalent orchestration capabilities and
  policies across OSes, not identical binary install paths.
