---
description: "Task list for portable Aichestra platform implementation"
---

# Tasks: Portable Aichestra Platform

**Input**: Design documents from `/specs/001-portable-ai-orchestration/`

**Prerequisites**: plan.md (required), spec.md (required)

**Architecture contract status (2026-09-10):** Spec/plan are source of truth for
**single-Orca-Run Mode C** with provider/runtime/model-agnostic
**ExecutionTargets**. Coordinator under Orca owns the concrete workflow/DAG;
Orca owns canonical Run/Task/Dispatch/worker/terminal/worktree lifecycle;
Aichestra owns discovery, ExecutionTarget resolution, policy, security,
deterministic gates and verification.

Phase 24 T163/T169–T176 remains the ExecutionTarget/launch-proof foundation.
Phase 25 (T177–T181) closes the post-c3de84f review blockers: prove-launch
secret boundary, stable Aichestra `--repo-root`, existing-terminal `launch_ref`,
in-Run `AICHESTRA_GATE:verification`, and Windows-native terminal-bridge.
Review residual T182–T185: orchestrate/research config-root safety, structured
`launch_proof_invocation` argv contract, Windows attestation pipeline proof,
and `abort-launch` owner-side cleanup.

Validation: **T167** ACCEPTED on final implementation HEAD `8c159f85`
(GitHub Actions run #78: Ubuntu + macOS + Windows green). Intermediate
matrices: run #70 on `c3de84f`, #72 on `10831fc`, #74 on `85bde480`, #76 on
`cbdf5a0`. **T168** live smoke on `run_3d12b2f31039` remains historical
evidence for the pre-T180 handshake. **T180** in-Run verification live smoke
is ACCEPTED on `run_27377a46c199` (2026-09-10). Local-only live validation
remains NOT VALIDATED. Merge blockers: none.

**Tests**: Included where required by success criteria (SC-001–SC-016) and
architecture contract tests (MODE-C-001–010, MODE-C-017–020). Prefer minimal
high-value coverage.

**Organization**: Phases follow foundation → user stories → CI/smoke/docs →
architecture realignment → ExecutionTarget / launch-proof work.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US6 traceability to spec.md
- **REOPENED**: previously marked done; invalid under new architecture contract
- **SUPERSEDED**: historical task kept for audit; do not treat as acceptance

## Phase 1: Repository / Python foundation

**Purpose**: Create the installable Python package skeleton without provider
runtime implementation.

- [x] T001 Create package layout `src/aichestra/` and `tests/` per plan.md
- [x] T002 Add `pyproject.toml` for Python 3.11+ package metadata and pytest
- [x] T003 [P] Extend root `.gitignore` excluding secrets, `.env`, model weights,
      sessions, caches, machine-local config, and large logs (FR-016/019/020)
- [x] T004 [P] Add minimal `tests/conftest.py` with shared path fixtures only

**Checkpoint**: Package imports; no Orca/provider installs yet beyond stubs

---

## Phase 2: Cross-platform configuration and platform detection

**Purpose**: Arbitrary clone path + OS detection + config layering (US1)

- [x] T005 [US1] Implement repo-root resolution in `src/aichestra/repo.py`
      for arbitrary clone paths (FR-010/011; SC-001)
- [x] T006 [P] [US1] Implement OS detection helpers in
      `src/aichestra/platform_detect.py` (FR-009/012/044)
- [x] T007 [US1] Implement config layering (tracked → OS → machine-local →
      project → runtime) in `src/aichestra/config/layering.py`
      (FR-016/041/060; SC-010)
- [x] T008 [P] [US1] REOPENED: hardware profiles must be capability-based;
      remove/quarantine production `M4_PRO_24GB` / `apple-m4-pro-24gb-example`
      special cases to test fixtures only; ensure `powerful` tier is reachable
      before `capable` (FR-017/018/051; Phase 20)
- [x] T009 [US1] Unit tests for repo root, OS detect, and config layering in
      `tests/unit/test_repo_config.py` (SC-001/SC-003 path invariants)

**Checkpoint**: Path/config foundation ready; no fixed home paths in tracked
defaults

---

## Phase 3: Machine profiler and local runtime discovery

**Purpose**: Deterministic hardware + extensible local AI discovery (US6)

- [x] T010 [US6] Implement deterministic machine-profiler in
      `src/aichestra/machine_profiler.py` (FR-046; SC-011)
- [x] T011 [P] [US6] Define local-runtime interface in
      `src/aichestra/local_runtime/base.py` (FR-047)
- [x] T012 [US6] Implement Ollama runtime + discovery in
      `src/aichestra/local_runtime/ollama.py` and `discovery.py` (FR-047)
- [x] T013 [US6] Implement capability-based model selector and resource policy
      in `model_selector.py` and `resources.py` (FR-048–052; SC-012/013/014)
- [x] T014 [US6] Tests for profiler, discovery, selection, disabled local,
      capability mismatch, multi-model, and no auto-download in
      `tests/unit/test_machine_local_ai.py` and
      `tests/integration/test_local_routing.py` (SC-011–014)

**Checkpoint**: Local AI optional and capability-routed; profiler deterministic

---

## Phase 4: Provider discovery / graceful degradation

**Purpose**: Discover providers and degrade usefully (US2)

- [x] T015 [US2] Define provider adapter interfaces in
      `src/aichestra/providers/base.py` including failure classes (FR-062)
- [x] T016 [P] [US2] Implement discovery in
      `src/aichestra/providers/discovery.py` (FR-003–008)
- [x] T017 [P] [US2] Add fake/absent provider test doubles under
      `tests/fakes/` for success, unavailable, quota, auth, timeout, error
      (FR-039/062; SC-004)
- [x] T018 [US2] Integration tests proving useful operation with no local
      worker, no Codex, and no Cursor in
      `tests/integration/test_provider_degradation.py` (SC-004)

**Checkpoint**: Discovery returns structured availability; missing optional
providers do not hard-fail bootstrap / Mode A. Missing Orca fails Mode C only.

---

## Phase 5: Modes, Orca integration, lead/handoff

**Purpose**: Three modes; Orca opt-in; native tools untouched (US2)

- [x] T019 [US2] Implement mode helpers in
      `src/aichestra/orchestration/modes.py` (FR-043)
- [x] T020 [US2] REOPENED: Orca adapter must be the sole Mode C execution path;
      create/resume exactly one Run; no dual control-plane (FR-001/045;
      MODE-C-001/002; Phase 20)
- [x] T021 [US2] Implement Codex preferred / Cursor fallback **policy** for
      Orca (not hidden direct-execution fallback) in
      `src/aichestra/orchestration/roles.py` (FR-002/003/004)
- [x] T022 [US2] REOPENED: Codex→Cursor handoff MUST prefer continuation inside
      the existing Orca Run; manual path MUST be one executable action
      (FR-035/036; Phase 20)
- [x] T023 [US2] REOPENED: local-worker adapter is discovery/capability only for
      Mode C; execution MUST be via Orca worker dispatch (FR-005/006;
      MODE-C-004; Phase 20)
- [x] T024 [US2] Contract/unit tests for modes, lead selection, and handoff in
      `tests/contract/test_lead_handoff.py` and `tests/contract/test_modes.py`

**Checkpoint**: Mode C is opt-in via Orca; native and interactive modes remain

---

## Phase 6: Agent roles, gates, verification, maintenance-audit

**Purpose**: Research compaction + anti-bloat gates + verifier (US3)

- [x] T025 [US3] REOPENED: repository-researcher compaction helpers OK;
      research **execution** MUST be Orca task on existing Run (FR-021/022/053;
      Phase 20)
- [x] T026 [US3] REOPENED / SUPERSEDES dual-orchestrator: replace
      `OrchestratedWorkflow` general-purpose phase engine with thin Mode C
      controller (policy + gates + one Orca Run). Do NOT keep a Python engine
      that schedules workers (FR-043/045/054; Phase 20)
- [x] T027 [US3] Implement maintenance-reviewer with structured output in
      `src/aichestra/orchestration/maintenance_reviewer.py`
      (FR-023/024/025/055)
- [x] T028 [US3] REOPENED: test/doc writer **preference** helpers may remain;
      writer **execution** MUST be Orca tasks (FR-026/027; Phase 20)
- [x] T029 [US3] Implement verification-runner in
      `src/aichestra/orchestration/verification.py` (FR-056; SC-015)
- [x] T030 [P] [US3] Encode Spec Kit proportionality helper in
      `src/aichestra/orchestration/speckit_policy.py` (FR-028/063)
- [x] T031 [P] [US3] Implement Factory-preservation guard in
      `src/aichestra/orchestration/factory_preserve.py` (FR-029)
- [x] T032 [P] [US3] REOPENED: rework/remove custom worktree edit-lock
      (`worktrees.py`, `.aichestra/edit.lock`) if it duplicates Orca worktree
      ownership; keep only thin concurrency policy if needed (FR-057; Phase 20)
- [x] T033 [US3] Implement maintenance-audit analysis-only workflow in
      `src/aichestra/orchestration/maintenance_audit.py` (FR-042)
- [x] T034 [US3] REOPENED: rewrite fixture/integration tests that encode
      dual-orchestrator / direct provider Mode C execution
      (SC-005/SC-015/SC-016; Phase 20)

**Checkpoint**: Reviewer can block unnecessary tests/docs; research compacted;
verifier authoritative; agents owned by Orca

---

## Phase 7: Stage security and sanitization

**Purpose**: Structural staging-only diagnostics (US4)

- [x] T035 [US4] Implement typed staging diagnostic builders in
      `src/aichestra/security/staging_ops.py` (FR-059)
- [x] T036 [US4] Implement staging-only boundary and deny production SSH in
      `src/aichestra/security/staging_allowlist.py` (FR-030/031)
- [x] T037 [US4] Enforce read-only allowlist and reject dangerous commands
      before remote execution (FR-032/033; SC-006)
- [x] T038 [US4] Implement diagnostic sanitization in
      `src/aichestra/security/sanitize.py` (FR-034)
- [x] T039 [US4] Security fixture tests in
      `tests/security/test_staging_commands.py` and
      `tests/security/test_sanitize.py` (SC-006)

**Checkpoint**: Destructive commands never reach remote execution

---

## Phase 8: Bootstrap / update / CLI entrypoints

**Purpose**: Idempotent OS entrypoints (US1)

- [x] T040 [US1] Implement shared idempotent bootstrap/update core in
      `src/aichestra/bootstrap/core.py` (FR-014/015/041/048/049)
- [x] T041 [P] [US1] Add `bootstrap/macos/setup.sh` (FR-013)
- [x] T042 [P] [US1] Add `bootstrap/windows/setup.ps1` (FR-013; Windows native)
- [x] T043 [P] [US1] Add `bootstrap/linux/setup.sh` (FR-013; Linux native)
- [x] T044 [US1] Implement CLI dispatch in `src/aichestra/cli.py` and
      `__main__.py` for doctor/profile/bootstrap/update (FR-061/064)
- [x] T045 [US1] Tests for idempotent bootstrap and machine-local preservation
      in `tests/integration/test_bootstrap.py` (SC-010)

**Checkpoint**: Fresh clone can bootstrap on each OS entrypoint path

---

## Phase 9: Doctor and machine-profile UX

**Purpose**: Cross-platform health check (US1/US5/US6)

- [x] T046 [US1] Implement doctor/health check in
      `src/aichestra/doctor.py` and `scripts/doctor.py` (FR-040/061)
- [x] T047 [US6] Expose machine profile CLI via `scripts/machine_profile.py`
      and doctor local-AI section (FR-046/061; SC-011)
- [x] T048 [US1] Doctor reports PASS/WARN/FAIL without claiming unrun live
      validation or printing credentials
- [x] T049 [US1] Unit/integration tests in `tests/unit/test_doctor.py`

**Checkpoint**: Doctor and machine profile are canonical local health entrypoints

---

## Phase 10: Fixture repositories and integration tests

**Purpose**: Project isolation proof (US5)

- [x] T050 [P] [US5] Create fixture repository A under `fixtures/project_a/`
      (Python/pytest) (FR-037)
- [x] T051 [P] [US5] Create fixture repository B under `fixtures/project_b/`
      (Node layout) (FR-037)
- [x] T052 [US5] Isolation tests proving no task/config/runtime leakage in
      `tests/integration/test_fixture_isolation.py` (SC-007)
- [x] T053 [US5] Scan/assert tracked production config has no developer home
      paths or credentials in `tests/contract/test_no_machine_paths.py`
      (SC-003; FR-011)
- [x] T054 [US5] Platform path/space/line-ending/precedence tests in
      `tests/unit/test_platform_paths.py` (FR-009/010/012)

**Checkpoint**: Two fixtures prove project-agnostic isolation

---

## Phase 11: GitHub Actions matrix

**Purpose**: CI on macOS/Windows/Linux without real quota (US5)

- [x] T055 [US5] Add `.github/workflows/ci.yml` matrix for macOS, Windows,
      Linux (FR-038; SC-002)
- [x] T056 [US5] Wire fake providers so CI never consumes real Codex/Cursor
      quota (FR-039/062)
- [x] T057 [US5] Ensure CI runs core unit/integration/security suites and fails
      on regressions

**Checkpoint**: Automated matrix is the portability proof until live smoke exists

---

## Phase 12: Current Mac live smoke validation

**Purpose**: Truthful live reporting (US5)

- [x] T058 [US5] Add Mac live smoke script/checklist runner that records only
      actually available components (SC-008)
- [x] T059 [US5] Ensure Windows/Linux live status remains NOT VALIDATED until
      real smoke runs (SC-009)
- [x] T060 [US5] Document smoke report format and daily-use commands in README
      without claiming unrun platforms (FR-064)

**Checkpoint**: Validation claims match executed evidence

---

## Phase 13: Operator documentation / final convergence

**Purpose**: Canonical docs only; converge to Definition of Done

- [x] T061 Update root `README.md` with implemented bootstrap/doctor/profile/
      update usage only after those entrypoints exist (FR-064)
- [x] T062 Add tracked `policies/defaults.yml` portable defaults (no machine
      paths) (FR-060)
- [x] T063 Verify Spec Kit proportionality note and AGENTS.md remain the
      process entrypoints (FR-028/063)
- [x] T064 REOPENED: full pytest + architecture contract suite after Phase 20
      production realignment
- [x] T065 REOPENED: cross-check FR/MODE-C/SC against implementation after
      Phase 20; do not claim complete while dual-orchestrator remains
- [x] T066 Final repo hygiene: no secrets, no model weights, no machine-local
      paths in tracked files (FR-016/019/020)
- [x] T067 [P] [US3] Implement file/vision routing helper in
      `src/aichestra/orchestration/media_routing.py` (FR-058; SC-012)

**Checkpoint**: Feature ready for Spec Kit converge / PR **only after Phase 20**

---

## Phase 14–19: Historical remediation (audit)

Earlier convergence/remediation tasks (T068–T102) partially moved Mode C toward
Orca, but several still encode or accept a Python phase engine /
`OrchestratedWorkflow` as Mode C. Treat the following as **SUPERSEDED** by
Phase 20 — do not leave them as acceptance of the dual-orchestrator design:

- [ ] T077 SUPERSEDED: “wire OrchestratedWorkflow to real phase execution” for
      Codex/Cursor/local-worker — **forbidden** Mode C shape
- [ ] T081–T084 SUPERSEDED-in-part: intent (Orca-only, one Run) kept; completion
      claims invalid while phase engine remains the Mode C core
- [ ] T096–T099 SUPERSEDED-in-part: renaming to `ModeCRunController` is
      insufficient if it remains a general-purpose phase/worker scheduler
- [x] T026/T032/T034 and related workflow/worktree tasks: see REOPENED above

Historical [x] marks on T068–T076, T078–T080, T085–T095, T100–T102 may remain
for non-architecture work (staging, doctor, smoke labels) but **do not** imply
Mode C architecture completion.

---

## Phase 20: Architecture contract realignment (COMPLETE / HISTORICAL)

**Purpose**: Make spec/plan/tasks/tests the source of truth for single-Orca-Run
Mode C. Then refactor production. **Do not mark feature complete until this
phase finishes.**

### Contract artifacts (this pass — no production refactor required yet)

- [x] T103 CRITICAL: Rewrite `spec.md` with MODE-C-001–010 and three-mode
      contract; do not soften to match current workflow engine
- [x] T104 CRITICAL: Rewrite `plan.md` with Aichestra IS / IS NOT, Mode C
      10-step flow, and obsolete-component list
- [x] T105 CRITICAL: Reopen/supersede dual-orchestrator tasks; add Phase 20
- [x] T106 HIGH: Update `AGENTS.md` (+ constitution graceful-degradation /
      Mode C constraints) for Orca-required Mode C
- [x] T107 CRITICAL: Add architecture contract tests (names below) and remove
      tests that assert Orca-less Mode C / direct lead fallback /
      project_root=None continuation (SC-016)

### Required architecture contract tests (T107)

```text
test_mode_c_requires_orca
test_mode_c_requires_project_root
test_mode_c_creates_exactly_one_orca_run
test_mode_c_never_directly_executes_codex
test_mode_c_never_directly_executes_cursor
test_mode_c_never_directly_executes_local_worker
test_all_mode_c_tasks_use_same_orca_run
test_mode_a_allows_direct_codex
test_mode_a_allows_direct_cursor
test_orca_unavailable_does_not_fallback_directly_to_lead
test_provider_can_be_independently_disabled
test_attachments_are_forwarded_to_orca
```

### Production realignment

Earlier `[x]` on T108–T124 were premature (agent phase scheduler, soft-fail
`run-use`, synthetic run ids, Spec Kit `*_satisfied` hacks, `/tmp/mode-c-proj`
CI failures). Re-verified under Phase 21.

- [x] T108 CRITICAL: Thin Mode C controller (`run_all` → `mode_c_agents`);
      `run_phase` refuses agent-phase worker scheduling
- [x] T109 CRITICAL: Project-root mandatory/resolvable (MODE-C-005)
- [x] T110 CRITICAL: Fail Mode C when Orca unavailable (MODE-C-001/006)
- [x] T111 CRITICAL: Agent work via Orca only (MODE-C-003/004)
- [x] T112 CRITICAL: Exactly one `run-create` / resume (MODE-C-002)
- [x] T113 CRITICAL: Same-run association fail-closed (MODE-C-007; FR-068)
- [x] T114 HIGH SUPERSEDED by T163/T169–T175: repository research must be a
      real Orca dispatch; when local-worker selected by policy, dispatch Orca
      worker/task with local agent (OpenCode) rather than metadata-only
      `research_agent`. Historical value: enforced real Orca dispatch (not
      prompt metadata). Product-specific `local-worker` → OpenCode routing is
      no longer the architectural contract; ExecutionTargets are
      provider/runtime/model-agnostic.
- [x] T115 HIGH: Writers via Orca under same run
- [x] T116 HIGH: Final lead review via Orca
- [x] T117 HIGH: No direct Mode C Codex/Cursor/local-worker fallback
- [x] T118 HIGH: No Mode C edit.lock ownership (Orca worktrees)
- [x] T119 HIGH: Provider enabled/disabled authoritative (MODE-C-009; FR-069)
- [x] T120 HIGH: Attachments outside parent checkout (MODE-C-010; FR-071)
- [x] T121 HIGH: Codex→Cursor handoff same-run executable UX (FR-035/036/073)
- [x] T122 MEDIUM: Capability-based profiles; Windows CIM; macOS GPU harden
- [x] T123 CRITICAL: Obsolete dual-orchestrator assertions removed
- [x] T124: Historical HEAD `6c83bc2` CI matrix green on macOS/Windows/Linux
      (run `34396617374`, evidence supplied in PR review). New fixes need fresh CI.

**Checkpoint**: Architecture contract locked in docs+tests+production Mode C
path. SUPERSEDED (optional live smoke): “Live Orca smoke on developer machines
remains optional for converge.” Current contract: T168 real Mode C smoke is
required for convergence / feature complete. Feature ready for maintainer
architecture review after Phase 21 / T137.

---

## Phase 21: Dual-orchestrator removal & fail-closed hardening (COMPLETE / HISTORICAL)

**Purpose**: Close gaps that Phase 20 prematurely marked complete.

- [x] T125 CRITICAL: Remove remaining Aichestra-owned Mode C agent phase
      scheduler (`run_all` thin coordinator; `run_phase` refuses agent phases)
- [x] T126 CRITICAL SUPERSEDED by T162 / current FR-070: Real MEDIUM Spec Kit
      artifact lifecycle (`.aichestra/speckit/` files; no `*_satisfied`
      metadata unlocks). Historical file-backed implementation may remain in
      history, but the current contract requires preserving the canonical
      target-project Spec Kit structure and must not create a competing
      `.aichestra/speckit/`.
- [x] T127 CRITICAL: LARGE Spec Kit clarify/plan/tasks lifecycle (file-backed)
- [x] T128 CRITICAL: Provider disable authoritative for Orca dispatch
      (never invent disabled lead; never force opencode when local disabled)
- [x] T129 CRITICAL: Same-run task association fail-closed
      (`run-use` failure → no `task-create`; no unbound retry)
- [x] T130 CRITICAL: Remove synthetic `aichestra-run-*` run ids (fail closed)
- [x] T131 HIGH: Isolate attachments from parent checkout
- [x] T132 HIGH: Executable same-run handoff (`--run-id` / recent-run resolve;
      README matches)
- [x] T133 HIGH: Arbitrary-project verification detection/bootstrap
      (.NET / Python / Node safe fallback)
- [x] T134 HIGH: Bootstrap can honestly reach `bootstrap_complete=true`
- [x] T135 MEDIUM: Orca wait completion correlated to expected dispatch_id
- [x] T136 CRITICAL: Architecture tests against dual-orchestrator regression
- [x] T137 CRITICAL: Historical HEAD CI matrix green (run `34396617374`,
      evidence supplied in PR review); this is not evidence for subsequent fixes.

**Checkpoint**: Do not merge until T124/T137 GitHub CI matrix is green.

---

## Phase 22: Architect REQUEST CHANGES (COMPLETE / HISTORICAL)

**Purpose**: Close remaining gaps from maintainer architecture review
(dual-orchestrator residual, Spec Kit via Orca, policy-only lead, dead seams,
contract-test honesty). Do not mark T124/T137/`converged` until this phase and
GitHub CI are green.

- [x] T138 CRITICAL: Spec Kit MEDIUM/LARGE artifacts produced via Orca under the
      Mode C Run (role `speckit_artifacts`); Aichestra only file-gates — no
      Python stub unlock (FR-070; architect P0#2)
- [x] T139 CRITICAL: Collapse writer agent scheduling into one same-run Orca
      handoff (`mode_c_writers`); delete unreachable per-phase agent handlers
      from `_execute_phase` (FR-043/045; architect P0#1 / P2#15)
- [x] T140 CRITICAL: Mode C CLI/bindings pass lead/local as **policy**
      (`preferred_lead` / `fallback_lead` / `providers`) — do not construct
      Codex/Cursor adapters for Mode C dispatch (FR-003/004/069; architect P1#9)
- [x] T141 HIGH: Contract tests assert Orca-owned Spec Kit + single writers
      handoff + refuse dual-orchestrator regression (SC-016; architect P1#8)
- [x] T142 HIGH: `prepare_manual_handoff` auto-resolves recent run when
      `project_root` given; README bootstrap honestly = probe/config not
      installer (FR-072/073; architect P1#11/#12)
- [x] T143 MEDIUM: Windows profiler prefers CIM/PowerShell before deprecated
      `wmic` (FR-046; architect P2#17)
- [x] T144 CRITICAL: Full local pytest green after Phase 22 (confirmed).
      T124/T137 remain open until GitHub Actions matrix verified — do not merge.

**Checkpoint**: Architecture review gaps addressed in code+tests; merge still
gated on T137 GitHub CI.

---

## Phase 23: Corrective alignment follow-up (COMPLETE / HISTORICAL)

**Purpose**: Close remaining contract gaps discovered after Phase 22:
real Orca local-worker dispatch, wait-event correlation on mixed dispatch
streams, truthful bootstrap readiness semantics, and legacy seam removal.

- [x] T145 CRITICAL: Split Mode C research into explicit Orca task on same run;
      use policy-selected agent and enforce local-worker real dispatch when
      AVAILABLE+CAPABLE+ALLOWED+PREFERRED
- [x] T146 HIGH: Route `mode_c_writers` through policy-selected local-worker
      when available; fallback to enabled cloud lead
- [x] T147 CRITICAL: Harden Orca wait loop to correlate by dispatch identity,
      ignore/ack unrelated events, and continue bounded wait
- [x] T148 HIGH: Make `BootstrapResult.ok` truthful with doctor/readiness
      blockers (no READY lie when doctor fails)
- [x] T149 HIGH: Remove legacy dual-orchestrator signals from production path
      (`OrchestratedWorkflow` alias, unreachable `_phase_*` agent handlers,
      `writer_fn`, `bound_writer_from_lead`)
- [x] T150 CRITICAL: Add positive routing tests:
      local research/writers real Orca local-worker dispatch + local disabled
      zero local dispatch + writer fallback
- [x] T151 CRITICAL: Add Orca event-correlation tests for mixed-dispatch stream
      (dispatch B events must not fail dispatch A wait)
- [x] T152 HIGH: Add bootstrap truthfulness tests for `ok` vs doctor blockers
- [x] T153 CRITICAL: Run full pytest and update task statuses only for
      behaviors proved by passing tests

---

## Phase 24: Workflow ownership / ExecutionTarget realignment (CURRENT)

**Purpose**: Remove the remaining architectural assumption that Mode C has a
fixed Aichestra-owned agent-phase workflow. Make the coordinator under Orca
own the concrete workflow/DAG; Orca own canonical Run/Task/Dispatch/worker/
terminal/worktree lifecycle; and Aichestra own discovery, ExecutionTarget
resolution, policy, security, deterministic gates and verification
(provider/runtime/model-agnostic).

Key contract:

```text
Agent Runtime != Model Provider

Agent Runtime + compatible provider/model + proven Orca launch path
= ExecutionTarget

Coordinator under Orca owns concrete workflow/DAG.
Orca owns canonical Run/Task/Dispatch/worker/terminal/worktree lifecycle.
Aichestra owns discovery, ExecutionTarget resolution, policy,
security, deterministic gates and verification.
```

### Architecture source-of-truth correction

- [x] T154 CRITICAL: Update `spec.md` so coordinator under Orca owns the
      concrete workflow/DAG and Orca owns canonical lifecycle/state; forbid a
      universal Aichestra research→implement→writers→review pipeline
      (MODE-C-011/012).

- [x] T155 CRITICAL: Update `spec.md` with project-context discovery and
      project-owned instruction precedence requirements
      (MODE-C-013/014; FR-078/079/080).

- [x] T156 CRITICAL: Update `plan.md` to replace the fixed Mode C step pipeline
      with `task + project → Aichestra context/policy/ExecutionTargets → one
      Orca Run → coordinator DAG under Orca lifecycle`.

- [x] T157 HIGH: Update `plan.md` ownership boundaries: concrete DAG belongs to
      the coordinator under Orca; Run/Task/Dispatch/worker/handoff/worktree
      lifecycle belongs to Orca; discovery/ExecutionTarget resolution/
      project context/policy/security/verification belong to Aichestra.

### Production realignment — workflow ownership (landed)

- [x] T158 CRITICAL: Refactor Mode C production controller so `run_all()` no
      longer hard-codes a universal research/implement/writers/review workflow.

- [x] T159 CRITICAL: Remove production dependence on fixed agent `Phase` values.
      Phase-like structures may not act as the canonical workflow state machine.

- [x] T160 CRITICAL: Introduce project-context discovery and an explicit bounded
      `ProjectContext` handed to Orca.

- [x] T161 CRITICAL: Discover and preserve target-project `AGENTS.md`, Spec Kit,
      Factory/AI tooling and supported project-owned instructions with explicit
      precedence rules.

- [x] T162 CRITICAL: Stop creating a competing `.aichestra/speckit/` canonical
      structure when the target project already has a canonical Spec Kit
      lifecycle. Define compatibility/migration behavior.

- [x] T164 HIGH: Derive Mode C execution status from Orca Run state plus
      deterministic Aichestra gate results; do not maintain a parallel agent
      workflow state machine.

- [x] T165 HIGH: Add architecture contract tests proving two materially
      different task/project workflows can execute without adding/changing
      Python `Phase` scheduling code.

- [x] T166 HIGH: Add contract tests proving target-project instructions are
      discovered, passed to Orca and remain authoritative.

### Production realignment — ExecutionTarget / launch proof (COMPLETE)

- [x] T163 CRITICAL TRACKING:
      Complete provider/runtime/model-agnostic ExecutionTarget support.

      T163 is an umbrella acceptance task and MUST NOT cause a second duplicate
      implementation path.

      T163 closes only when T169–T175 are implemented, integrated into Mode C,
      covered by required tests, and only proven runnable ExecutionTargets are
      exposed to the coordinator.

      Aichestra must independently discover Agent Runtimes, Model
      Providers/models, compatibility and proven Orca launch strategies, and
      expose only runnable ExecutionTargets to Mode C.

      Legacy `local-worker` is compatibility input only and must not remain the
      canonical execution abstraction.

      OpenCode + Ollama MAY appear as one example/implementation target only —
      not an architectural or mandatory local path.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

- [x] T169 CRITICAL: Introduce canonical domain models for:

      - AgentRuntime
      - ModelProvider
      - ExecutionTarget
      - ExecutionCapabilities
      - ExecutionPolicy
      - LaunchStrategy

      ExecutionTarget must include enough data to identify:

      - runtime
      - optional provider
      - optional model
      - optional endpoint
      - locality
      - capabilities
      - enabled
      - available
      - launch_strategy

      Existing provider/local-worker structures may temporarily adapt into this
      model, but must not remain the Mode C canonical abstraction.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

- [x] T170 CRITICAL: Separate discovery of:

      - installed/configured Agent Runtimes
      - Model Providers / inference backends
      - available models
      - configured endpoints
      - model capabilities
      - machine capabilities

      Discovery facts alone must NOT mark an ExecutionTarget runnable.

      Examples (`OpenCode installed = true`, `Ollama reachable = true`,
      `model installed = true`) must still require compatibility + launch proof.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

- [x] T171 CRITICAL: Implement ExecutionTarget compatibility/resolution.

      Resolver must combine runtime/provider/model/machine/project facts and
      produce only valid target candidates.

      Target state must distinguish at least:

      - enabled / disabled
      - available / unavailable
      - capable / incapable
      - allowed / forbidden
      - preferred / non-preferred
      - launchable / unsupported

      Disabling one runtime/provider/model removes only affected targets.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

- [x] T172 HIGH: Pass resolved ExecutionTargets and their capabilities/policy
      into the generic coordinator bootstrap context.

      Coordinator must select inner workers using capabilities/policy.

      Aichestra must NOT introduce product-name phase routing such as:

      research = OpenCode
      implementation = Codex
      docs = local-worker

      Product preferences are allowed only as explicit user/project/default
      policy.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

- [x] T173 CRITICAL: Resolve exactly one runnable bootstrap ExecutionTarget
      solely for launching the generic Mode C coordinator.

      This is a narrow bootstrap placement exception.

      It MUST NOT allow Aichestra to select/schedule research, implementation,
      writers, review or other inner DAG workers.

      No runnable coordinator target → Mode C fail closed.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

      Implemented: bootstrap selects one runnable target before Run creation;
      the production Orca adapter uses that target and validates its effective
      native launch receipt. No legacy lead/local fallback controls startup.
      Adapter tests launch an arbitrary registered native runtime with no legacy
      providers, assert one Run, and reject a mismatched launch receipt.

- [x] T174 CRITICAL: Implement launch-strategy abstraction for ExecutionTargets.

      Supported strategy types:

      - orca-native
      - orca-existing-terminal
      - orca-terminal-bridge
      - unsupported

      Prefer native Orca worker launch.

      A terminal bridge may be used only when native Orca launch cannot express
      the required runtime/provider/model binding and when the actual
      Orca-supervised agent process can be proven to use the intended
      configuration.

      The bridge must not create an Aichestra-owned agent loop.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

      Progress: launch adapter protocol/registry with prepare/confirm, Orca
      schema probing, native runtime-managed launches, native opaque `--model`
      when `launch.effective.model` can confirm, terminal-bridge OpenCode
      builder, existing-terminal attestation via live handle
      (`ORCA_WORKER_TERMINAL_HANDLE` / `terminal_handle`), bridge-owned terminal
      cleanup on prepare/dispatch failure, and fail-closed unsupported bindings.
      Bootstrap preflight: `prove_launch` / `prove_bootstrap_launch` prepare/attest
      runs before any Orca Run create/resume; `PreparedLaunch` is reused at
      worker-start (no second bridge terminal); Run-create failure aborts owned
      terminals. `select_bootstrap` returns only runnable targets; provisionable
      bridges are preflight/candidate only. Coordinator package contract v2:
      `execution_targets` exposes **only runnable** targets; provisionable
      bridges are separate non-dispatchable `execution_target_candidates` that
      require deterministic `aichestra.prove_launch` via callable
      `aichestra prove-launch --candidate-id` (trusted re-resolve; no DIY
      `launch_recipe` / `create_command` in candidates). Domain split:
      `preparable` (runnable|provisionable) vs `dispatchable` (runnable only).
      Controller-level regressions cover proof-fail→0 run-create, proof-ok→1
      terminal+1 run-create, run-create-fail→abort owned terminal, prepared
      launch reuse, and prove-launch surface → worker-start handle reuse.
      Reviewer re-accepted the proof/lifecycle semantics on 2026-09-10 after
      the callable proof surface, trusted candidate re-resolution, exact
      prepared-handle reuse, and failure cleanup passed the production adapter
      and controller-level regressions.

- [x] T175 CRITICAL: An ExecutionTarget may be advertised as runnable only when
      its launch strategy is proven to bind the actual agent process to the
      intended runtime + provider/model/endpoint where applicable.

      Proof must cover the dimensions the concrete Agent Runtime exposes or
      uses. Do not require provider/model/endpoint binding for a runtime that
      does not expose or use those dimensions.

      The following are insufficient proof:

      - endpoint discovery alone
      - installed model alone
      - model/provider text in prompt
      - environment variables applied only to the Aichestra/RPC client process

      If proof is unavailable:

      launch_strategy = unsupported

      and Mode C must not select the target.
      (MODE-C-017–020; FR-005/047/050/051/065/066/069)

      Progress: native schema → `launch_proven` / runnable; terminal-bridge
      schema+builder → provisionable only (not runnable) until `prove_launch`
      proves structured live process attestation (create-receipt
      `startupCommand` and screen/tail substring tokens are ignored);
      existing-terminal requires attested handle + structured process evidence
      to become runnable; prompt/discovery/client-env alone cannot mark
      runnable. Exact compare of runtime/provider/model/endpoint from process
      argv / effective config / binding metadata; non-zero terminal show/read
      fails closed. Provisionable bridges are advertised only as
      `execution_target_candidates` (not in dispatchable `execution_targets`)
      until deterministic `aichestra prove-launch` / `aichestra.prove_launch`
      promotes them (`dispatchable` only after runnable). Reviewer re-accepted
      the runnable/proof boundary on 2026-09-10; the focused contracts and full
      local suite (308 tests) passed.

- [x] T176 HIGH: Implement Orca worker-release recovery semantics.

      Handle at least:

      - released
      - already_released
      - release_pending
      - release_unknown

      `release_pending` must not automatically be treated as hard failure merely
      because the worker was not immediately released.

      Follow the exact recovery action / `projection.nextAction` supplied by
      Orca, within a bounded allowlisted recovery flow, then re-check exact
      worker state.

      Never substitute broad `orca terminal close`.

      Success requires no unresolved reclaimable worker resources.

      Add production-adapter tests for:

      * released
      * already_released
      * release_pending recovery
      * release_unknown
      * unresolved reclaimable resources

### Review follow-up — prove-launch / verification / Windows (COMPLETE)

- [x] T177 CRITICAL: `prove-launch` / process evidence MUST NOT copy process
      `env` into coordinator-visible payloads. Allowlist only binding-safe
      fields (e.g. parse `OPENCODE_CONFIG_CONTENT` into structured config).
      Sanitize the entire `aichestra prove-launch` JSON payload before stdout.
- [x] T178 CRITICAL: Candidate promotion MUST pass a stable Aichestra config
      root (`--repo-root` / `aichestra_repo_root` in the trusted package).
      `prove_launch_by_candidate_id` MUST NOT `find_repo_root()` from a foreign
      target-project cwd. T163/T175 remain accepted only with this seam.
      <!-- Residual (2026-09-10 review): `orchestrate` / `research` CLI also
           MUST use `resolve_aichestra_config_root()` (not bare
           `find_repo_root()` from a foreign target cwd). -->
- [x] T179 CRITICAL: `orca-existing-terminal` is dispatchable only with a
      transferable opaque `launch_ref` (attested terminal handle). Proven
      without a handle MUST NOT appear in canonical `execution_targets`.
- [x] T180 CRITICAL: Deterministic verification is an in-Run coordinator gate
      (`AICHESTRA_GATE:verification`), symmetric with maintenance. Coordinator
      MUST NOT `worker_done outcome=succeeded` until Aichestra returns the
      authoritative verification result; verification failure completes the
      Dispatch with `outcome=failed` so Orca and Aichestra agree.
      <!-- LIVE ACCEPTED (2026-09-10): `aichestra orchestrate` with
           ORCA_TERMINAL_HANDLE against the isolated acceptance fixture created
           exactly one Run `run_27377a46c199`. Bootstrap ExecutionTarget was
           cursor (`--no-codex --no-local`). Coordinator Dispatch
           `ctx_ee792ba5c418` / `task_69844cd1dccb` dynamically created child
           `task_f2432a42e443` / `ctx_40ecf96220e8` which wrote
           `result.txt`=`accepted`. Live ask order:
           `AICHESTRA_GATE:maintenance` (msg_24640ff359f7 @ 14:02:13Z) then
           `AICHESTRA_GATE:verification` (msg_701912090358 @ 14:02:18Z);
           Aichestra answered both in-wait (`skipped_answered_questions=2`);
           only then `worker_done outcome=succeeded` (msg_46cc6dcec3f8 @
           14:02:25Z). Deterministic verification ok; both workers released;
           `orca_run_status.ok=true`. Gates completed: project_context, policy,
           attachments, maintenance, verification, orca_handoff. -->
- [x] T181 HIGH: `orca-terminal-bridge` command builder is OS-specific.
      Windows uses PowerShell + base64 config; POSIX keeps `env KEY=VAL`.
      Binding proof still uses structured process evidence, not screen text.
      <!-- Residual (2026-09-10 review): full Windows process-argv →
           extract_attested_binding → structured_binding_matches pipeline
           covered; PowerShell wrapper must not treat script argv[-1] as
           runtime binary. -->

### Review residual — config root / argv / abort (COMPLETE)

- [x] T182 CRITICAL: Mode C `orchestrate` and `research` resolve Aichestra
      config root via `resolve_aichestra_config_root()` (same as prove-launch);
      never treat a foreign target project as the platform config root.
- [x] T183 CRITICAL: `launch_proof_invocation` is a structured
      `{command, args}` argv contract (not a shell-concatenated string).
      Spaces in paths and JSON-shaped candidate ids remain intact; OS-specific
      `render_cli_invocation` is display-only.
- [x] T184 HIGH: Windows bridge attestation proven end-to-end for PowerShell
      process argv (builder → argv → extract → binding match → prepare).
- [x] T185 MEDIUM: Owner-side `aichestra abort-launch --token` claims an
      Aichestra-issued opaque cleanup lease, then closes only the stored
      owned bridge handle. Callers cannot pass a raw terminal handle
      (`--launch-ref` is rejected). `orca-existing-terminal` never receives
      a lease. Abort structurally asks Orca `worker-list` whether that
      handle is bound to a Dispatch (inner coordinator `worker-start`
      included); a structured binding consumes the lease and does not
      close the terminal. Lease state is available → claimed → consumed;
      probe or close failure restores available so abort is retriable.

### Validation gates

- [x] T167 CRITICAL:
      After T163/T169–T176 production implementation is complete, run the full
      local test suite and a fresh GitHub Actions matrix on that final
      implementation HEAD.

      Required:
      - Ubuntu green
      - macOS green
      - Windows green

      ACCEPTED on final implementation HEAD `8c159f85` via GitHub Actions
      run #78 (Ubuntu + macOS + Windows). Intermediate evidence retained for
      audit: run #70 on `c3de84f`; #72 on `10831fc`; #74 on `85bde480`;
      #76 on `cbdf5a0`. Docs-only follow-ups do not reopen T167; any later
      production-code change would require a new matrix.

- [x] T168 CRITICAL: Run at least one real Mode C integration smoke proving:

      ```text
      real Aichestra CLI
      → real Orca runtime
      → exactly ONE Run
      → generic coordinator through runnable bootstrap ExecutionTarget
      → coordinator dynamically creates at least one child Task/Dispatch
      → child uses an allowed ExecutionTarget
      → maintenance ask/reply gate
      → convergence
      → worker_done
      → worker cleanup
      → deterministic verification
      → canonical task/run validation
      → SUCCESS
      ```

      Smoke must prove that Aichestra did NOT execute a fixed
      research→implement→writers→review Python pipeline.

      Before local-only Mode C is considered validated/feature-complete, run a
      separate real smoke on a machine with a proven local ExecutionTarget:

      ```text
      Codex unavailable/disabled
      Cursor unavailable/disabled
      → local ExecutionTarget
      → Orca Task/Dispatch
      → real repository change
      → worker_done
      → cleanup
      → verification
      → canonical success
      ```

      If no suitable local target is available on the current machine, keep
      local-only live validation explicitly NOT VALIDATED rather than silently
      passing it.
      <!-- ACCEPTED (2026-09-10): real `aichestra orchestrate` from a live Orca
           terminal (`ORCA_TERMINAL_HANDLE`) against the isolated acceptance
           fixture created exactly one Run `run_3d12b2f31039`. Bootstrap
           ExecutionTarget was cursor (`--no-codex --no-local`). Coordinator
           Dispatch `ctx_b871e527c75f` / task_0750fab8d84a bound the Run with
           run-use and dynamically created child task_1692c2579ac6 /
           Dispatch `ctx_682974cd5eed` which wrote `result.txt`=`accepted`.
           Maintenance ask/reply completed; both workers succeeded and were
           released; deterministic verification passed; canonical
           orca_run_status ok. Gates completed:
           project_context, policy, attachments, maintenance, orca_handoff,
           verification. No Aichestra-owned research→implement→writers→review
           Python pipeline. Local-only Mode C live validation: NOT VALIDATED;
           no proven local provider/model/endpoint launch adapter on this
           machine. Phase 25 T180 in-Run verification live path separately
           ACCEPTED on `run_27377a46c199` (see T180). -->

Do not mark T163/T169–T176 complete merely because documentation describes the
architecture. Coordinator bootstrap, project-context, and workflow-ownership
fixes already landed (T158–T162, T164–T166) do not satisfy ExecutionTarget
discovery/resolution or launch-proof work.

**Checkpoint**: Architecture converged — T163, T167–T181 complete (T167
close-out: run #78 on `8c159f85`).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phases 1–13 historical foundation
- Phase 20 contract artifacts (T103–T107) precede production realignment
- Phase 21 (T125–T137) closes dual-orchestrator / fail-closed gaps
- Phase 22 (T138–T144) closes architect REQUEST CHANGES residuals
- Phase 23 (T145–T153) closes corrective alignment follow-up
- Phase 24 workflow-ownership slice (T158–T162, T164–T166) and ExecutionTarget
  foundation and launch-proof stack (T163, T169–T176) accepted; Phase 25
  review follow-up (T177–T181) accepted; live Mode C smoke (T168) accepted
  for pre-T180 HEAD; T180 in-Run verification live smoke accepted on
  `run_27377a46c199`; fresh CI (T167) accepted on `8c159f85` (run #78)
- T108–T124 depend on T103–T107; T167 merge gate closed on run #78
- Converge / “feature complete” only after Phase 24 T163 + T167–T176
- Phase 24 supersedes any earlier interpretation of T108/T125/T139 that allows
  `ModeCRunController.run_all()` to remain the owner of a fixed agent workflow
  or that treats legacy `local-worker` as the Mode C canonical abstraction
- Earlier green tests prove the previous contract only; they do not prove
  MODE-C-011–020 ExecutionTarget launch proof

### User Story Mapping

- **US1** (bootstrap/portability): Phases 2, 8, 9, T134
- **US2** (modes/providers/Orca/ExecutionTargets): Phases 4, 5, 20, 21, 24
- **US3** (project-aware Orca execution): Phase 6, 20, 21, 24
- **US4** (staging security): Phase 7
- **US5** (isolation/CI/smoke): Phases 10–12, T137, T167–T168
- **US6** (machine/local AI / ExecutionTarget inputs): Phase 3, parts of 9,
  T122, T169–T175

### Parallel Opportunities

- After T107: T119/T122 can proceed in parallel with T108–T113
- T114–T116 parallel once T111 lands
- After T154–T157: T160–T162 can proceed in parallel with T158–T159 once
  controller seams are identified
- T169 → T170 → T171 → T172 → T174 → T175 → T173
  (T173 requires a runnable bootstrap ExecutionTarget; runnable requires
  proven launch strategy/proof from T174/T175);
  T176 can proceed in parallel with T169–T175 once Orca adapter seams exist
- T167 closed on final implementation HEAD after production work landed

---

## Requirement Traceability (high-signal)

| Requirement cluster | Tasks |
|---------------------|-------|
| MODE-C-001–010 architecture contract | T103–T107, T108–T123, T125–T136 |
| MODE-C-011–016 workflow ownership | T154–T157 (docs), T158–T162, T164–T166 (code landed); T167–T168 (validation) |
| MODE-C-017–020 ExecutionTarget / launch proof | T163, T169–T175 |
| FR-005 / FR-047 / FR-050 / FR-051 / FR-065 / FR-066 / FR-069 | T163, T169–T175 |
| FR-078–083 project-execution control plane | T154–T157 (docs), T158–T162, T164–T166 (code) |
| Orca release lifecycle | T176 |
| FR-067–073 hardening | T125–T135 |
| Dual-orchestrator regression tests | T136 |
| Windows/Linux native bootstrap | T006, T041–T043 |
| Arbitrary clone path / no fixed home | T005, T009, T053, T054 |
| Machine profiler / local runtime / routing | T010–T014, T122 (inputs); T169–T175 (ExecutionTarget resolution) |
| Three modes + graceful degradation | T015–T024, T108–T117, T163, T169–T175 |
| maintenance-reviewer + verifier + audit | T025–T034, T114–T116 |
| Staging read-only + sanitization | T035–T039 |
| GitHub Actions matrix / no quota | T055–T057, T124, T137, T167 |
| Isolation fixtures | T050–T052 |
| Truthful validation + operator UX | T058–T061, T064, T168 |

---

## Implementation Strategy

1. Spec/plan/AGENTS are source of truth — do not soften for code
2. Mode C is `task + project → discovery/ExecutionTargets/policy → one Orca Run
   → bootstrap coordinator`; coordinator under Orca owns the concrete DAG;
   Orca owns canonical lifecycle — Aichestra is not a fixed agent-phase engine
   and must not treat legacy `local-worker` as the canonical abstraction
3. T167 is closed only by a **fresh** GitHub CI matrix green on the final
   implementation HEAD after T163/T169–T176 (Ubuntu + macOS + Windows);
   evidence: run #78 on `8c159f85`. Do not reuse older HEAD CI as close-out
   for later production-code changes
4. Do not claim Windows/Linux live validation from macOS-only execution
5. Do not reintroduce `while current_phase: launch Orca worker` or a universal
   research→implement→writers→review `run_all()` pipeline
6. Do not treat architecture as converged until Phase 24 T163 + T167–T176
   complete; docs describing ExecutionTargets do not close those tasks
7. Advertise an ExecutionTarget as runnable only with proven launch binding;
   discovery/install alone is insufficient

Review follow-up: maintenance uses an in-dispatch ask/reply callback; verification
is the symmetric in-Run `AICHESTRA_GATE:verification`. Canonical Run/task
confirmation gates success. Task specs use explicit Target, Change,
Constraints, Ownership and Observable acceptance sections. Mutating adapter
routes require live authority. T174/T175 and umbrella T163 remain accepted with
Phase 25 T177–T181 (secret boundary, `--repo-root`, existing-terminal
`launch_ref`, in-Run verification, Windows bridge). Live Mode C smoke (T168)
accepted on run_3d12b2f31039 for the pre-T180 handshake. T180 in-Run
verification live smoke accepted on run_27377a46c199. T167 fresh CI accepted
on final implementation HEAD `8c159f85` (GitHub Actions run #78: Ubuntu +
macOS + Windows). Local-only live validation remains NOT VALIDATED.
OpenCode + Ollama is one possible example target only — not a mandatory
architecture.
