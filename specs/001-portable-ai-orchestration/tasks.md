---
description: "Task list for portable Aichestra platform implementation"
---

# Tasks: Portable Aichestra Platform

**Input**: Design documents from `/specs/001-portable-ai-orchestration/`

**Prerequisites**: plan.md (required), spec.md (required)

**Architecture contract status (2026-09-09)**: Spec/plan/AGENTS realigned to
**single-Orca-Run Mode C**. Production Mode C is a thin coordinator
(`ModeCRunController.run_all` → one `mode_c_agents` Orca handoff + local
gates). Earlier Phase 20 `[x]` marks on T108–T124 were premature; acceptance
is Phase 21 (T125–T137). Local pytest green; GitHub CI matrix pending push
(T124/T137).

**Tests**: Included where required by success criteria (SC-001–SC-016) and
architecture contract tests (MODE-C-001–010). Prefer minimal high-value coverage.

**Organization**: Phases follow foundation → user stories → CI/smoke/docs →
architecture realignment.

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

## Phase 20: Architecture contract realignment (CURRENT)

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
- [x] T114 HIGH: Research via Orca policy package (not LocalWorkerProvider)
- [x] T115 HIGH: Writers via Orca under same run
- [x] T116 HIGH: Final lead review via Orca
- [x] T117 HIGH: No direct Mode C Codex/Cursor/local-worker fallback
- [x] T118 HIGH: No Mode C edit.lock ownership (Orca worktrees)
- [x] T119 HIGH: Provider enabled/disabled authoritative (MODE-C-009; FR-069)
- [x] T120 HIGH: Attachments outside parent checkout (MODE-C-010; FR-071)
- [x] T121 HIGH: Codex→Cursor handoff same-run executable UX (FR-035/036/073)
- [x] T122 MEDIUM: Capability-based profiles; Windows CIM; macOS GPU harden
- [x] T123 CRITICAL: Obsolete dual-orchestrator assertions removed
- [ ] T124: Full suite + GitHub CI matrix green — local pytest green;
      CI pending push/merge verification

**Checkpoint**: Architecture contract locked in docs+tests+production Mode C
path. Live Orca smoke on developer machines remains optional for converge.
Feature ready for maintainer architecture review after Phase 21 / T137.

---

## Phase 21: Dual-orchestrator removal & fail-closed hardening (CURRENT)

**Purpose**: Close gaps that Phase 20 prematurely marked complete.

- [x] T125 CRITICAL: Remove remaining Aichestra-owned Mode C agent phase
      scheduler (`run_all` thin coordinator; `run_phase` refuses agent phases)
- [x] T126 CRITICAL: Real MEDIUM Spec Kit artifact lifecycle
      (`.aichestra/speckit/` files; no `*_satisfied` metadata unlocks)
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
- [ ] T137 CRITICAL: Full green CI / convergence (local `pytest` green;
      GitHub Actions macOS/Windows/Linux after push — do not claim until green)

**Checkpoint**: Do not merge until T124/T137 GitHub CI matrix is green.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phases 1–13 historical foundation
- Phase 20 contract artifacts (T103–T107) precede production realignment
- Phase 21 (T125–T137) closes dual-orchestrator / fail-closed gaps
- T108–T124 depend on T103–T107; T124/T137 gate converge
- Converge / “feature complete” only after T137

### User Story Mapping

- **US1** (bootstrap/portability): Phases 2, 8, 9, T134
- **US2** (modes/providers/Orca): Phases 4, 5, 20, 21
- **US3** (research/reviewer/verify): Phase 6, 20, 21
- **US4** (staging security): Phase 7
- **US5** (isolation/CI/smoke): Phases 10–12, T137
- **US6** (machine/local AI): Phase 3, parts of 9, T122

### Parallel Opportunities

- After T107: T119/T122 can proceed in parallel with T108–T113
- T114–T116 parallel once T111 lands

---

## Requirement Traceability (high-signal)

| Requirement cluster | Tasks |
|---------------------|-------|
| MODE-C-001–010 architecture contract | T103–T107, T108–T123, T125–T136 |
| FR-067–073 hardening | T125–T135 |
| Dual-orchestrator regression tests | T136 |
| Windows/Linux native bootstrap | T006, T041–T043 |
| Arbitrary clone path / no fixed home | T005, T009, T053, T054 |
| Machine profiler / local runtime / routing | T010–T014, T122 |
| Three modes + graceful degradation | T015–T024, T108–T117 |
| maintenance-reviewer + verifier + audit | T025–T034, T114–T116 |
| Staging read-only + sanitization | T035–T039 |
| GitHub Actions matrix / no quota | T055–T057, T124, T137 |
| Isolation fixtures | T050–T052 |
| Truthful validation + operator UX | T058–T061, T064 |

---

## Implementation Strategy

1. Spec/plan/AGENTS are source of truth — do not soften for code
2. Production Mode C is thin coordinator + one Orca Run
3. Do not mark T124/T137 `[x]` until GitHub CI matrix is green
4. Do not claim Windows/Linux live validation from macOS-only execution
5. Do not reintroduce `while current_phase: launch Orca worker`
