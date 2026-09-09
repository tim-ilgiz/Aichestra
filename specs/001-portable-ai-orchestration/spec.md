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

### User Story 2 - Graceful multi-provider orchestration (Priority: P1)

A developer uses Orca as the interactive control plane with Codex preferred as
lead, Cursor as fallback lead, and an optional OpenCode+Ollama local worker.
Missing providers do not disable the whole platform.

**Why this priority**: Provider availability varies by machine and account.

**Independent Test**: Fake/absent-provider fixtures prove useful operation with
no local worker, no Codex, and no Cursor independently.

**Acceptance Scenarios**:

1. **Given** Orca is available, **When** the developer starts orchestrated
   work, **Then** Orca is the primary interactive UI/control plane.
2. **Given** Codex is available, **When** a lead is selected, **Then** Codex is
   preferred.
3. **Given** Codex is unavailable and Cursor is available, **When** a lead is
   selected, **Then** Cursor is used as fallback.
4. **Given** local inference is disabled or missing, **When** orchestrated
   workflows run, **Then** the platform remains useful without the local worker.
5. **Given** native Codex or Cursor CLI/IDE usage, **When** the developer works
   outside Orca, **Then** native commands remain unchanged and unintercepted.

---

### User Story 3 - Research, review, and anti-bloat gates (Priority: P2)

Orchestrated tasks can research a local repository, compact findings before
cloud lead handoff, and run a maintenance-reviewer that may explicitly decide
no new tests or docs are needed.

**Why this priority**: Controls quality and prevents documentation/test bloat.

**Independent Test**: Fixture decisions prove "no tests needed", "tests needed",
"no docs needed", and "docs update needed".

**Acceptance Scenarios**:

1. **Given** an orchestrated research task, **When** research completes,
   **Then** output is compacted before cloud lead handoff.
2. **Given** production implementation is complete, **When** the workflow
   proceeds, **Then** maintenance-reviewer runs before test or documentation
   generation.
3. **Given** maintenance-reviewer decides no tests or no docs are needed,
   **When** downstream writers run, **Then** they respect that decision and
   prefer updating existing canonical artifacts when work is needed.

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

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Orca is the primary interactive UI/control plane for orchestrated
  work.
- **FR-002**: Direct native use of Codex and Cursor remains independent and
  unchanged.
- **FR-003**: Codex is the preferred lead provider.
- **FR-004**: Cursor is a supported fallback lead.
- **FR-005**: A local worker based on OpenCode + Ollama is optional.
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
- **FR-018**: The current M4 Pro 24 GB hardware profile defaults to a
  conservative approximately 14B-class local worker with one inference worker.
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
- **FR-032**: Stage diagnostics enforce a read-only command allowlist outside of
  the LLM prompt.
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

### Key Entities

- **Provider Profile**: Discovered availability and role of Codex, Cursor,
  OpenCode/Ollama, and Orca on the current machine.
- **Hardware Profile**: Machine-local defaults for local model class and worker
  count (untracked).
- **Orchestration Policy**: Tracked, portable rules for roles, Spec Kit
  proportionality, and maintenance-reviewer gates.
- **Staging Diagnostic Request**: Candidate remote command subject to allowlist
  and sanitization.
- **Fixture Repository**: Isolated sample project used to prove non-leakage.
- **Validation Report**: Truthful record of automated vs live smoke coverage by
  OS and component.

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
