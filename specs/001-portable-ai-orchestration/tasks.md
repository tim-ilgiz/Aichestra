---
description: "Task list for portable Aichestra platform implementation"
---

# Tasks: Portable Aichestra Platform

**Input**: Design documents from `/specs/001-portable-ai-orchestration/`

**Prerequisites**: plan.md (required), spec.md (required)

**Tests**: Included where required by success criteria (SC-001–SC-015) and
security/isolation invariants. Prefer minimal high-value coverage.

**Organization**: Phases follow foundation → user stories → CI/smoke/docs.
Story labels map to spec user stories (US1–US6).

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US6 traceability to spec.md

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
- [x] T008 [P] [US1] Implement hardware profiles in
      `src/aichestra/config/hardware_profiles.py` including M4 Pro 24 GB as an
      example profile only (FR-017/018/051)
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

**Checkpoint**: Discovery returns structured availability; missing providers do
not hard-fail core workflows

---

## Phase 5: Modes, Orca integration, lead/handoff

**Purpose**: Three modes; Orca opt-in; native tools untouched (US2)

- [x] T019 [US2] Implement mode helpers in
      `src/aichestra/orchestration/modes.py` (FR-043)
- [x] T020 [US2] Implement Orca adapter/integration hooks in
      `src/aichestra/providers/orca.py` after inspecting current Orca docs
      (FR-001/045)
- [x] T021 [US2] Implement Codex preferred / Cursor fallback lead selection and
      native-unwrapped policy in `src/aichestra/orchestration/roles.py`
      (FR-002/003/004)
- [x] T022 [US2] Implement Codex→Cursor handoff with bounded context and
      documented manual fallback policy in
      `src/aichestra/orchestration/handoff.py` (FR-035/036)
- [x] T023 [US2] Implement optional local worker adapter in
      `src/aichestra/providers/local_worker.py` (FR-005/006)
- [x] T024 [US2] Contract/unit tests for modes, lead selection, and handoff in
      `tests/contract/test_lead_handoff.py` and `tests/contract/test_modes.py`

**Checkpoint**: Orchestrated mode is opt-in; native and interactive modes remain

---

## Phase 6: Agent roles, workflow, verification, maintenance-audit

**Purpose**: Research compaction + anti-bloat gates + verifier (US3)

- [x] T025 [US3] Implement repository-researcher + compaction in
      `src/aichestra/orchestration/research_compact.py` (FR-021/022/053)
- [x] T026 [US3] Implement default orchestrated workflow in
      `src/aichestra/orchestration/workflow.py` (FR-054)
- [x] T027 [US3] Implement maintenance-reviewer with structured output in
      `src/aichestra/orchestration/maintenance_reviewer.py`
      (FR-023/024/025/055)
- [x] T028 [US3] Implement test/doc writer preference helpers in
      `src/aichestra/orchestration/writers.py` (FR-026/027)
- [x] T029 [US3] Implement verification-runner in
      `src/aichestra/orchestration/verification.py` (FR-056; SC-015)
- [x] T030 [P] [US3] Encode Spec Kit proportionality helper in
      `src/aichestra/orchestration/speckit_policy.py` (FR-028/063)
- [x] T031 [P] [US3] Implement Factory-preservation guard in
      `src/aichestra/orchestration/factory_preserve.py` (FR-029)
- [x] T032 [P] [US3] Implement worktree/serial edit policy in
      `src/aichestra/orchestration/worktrees.py` (FR-057)
- [x] T033 [US3] Implement maintenance-audit analysis-only workflow in
      `src/aichestra/orchestration/maintenance_audit.py` (FR-042)
- [x] T034 [US3] Fixture tests for maintenance-reviewer, skipped writers,
      research compaction, and verifier exit codes in
      `tests/integration/test_maintenance_reviewer.py` and
      `tests/integration/test_workflow.py` (SC-005/SC-015)

**Checkpoint**: Reviewer can block unnecessary tests/docs; research compacted;
verifier authoritative

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
- [x] T064 Run full local pytest suite; fix failures; mark any blocked live
      validation explicitly
- [x] T065 Cross-check FR-001–FR-064 and SC-001–SC-015 against implemented
      tasks; close gaps or record NOT VALIDATED blockers
- [x] T066 Final repo hygiene: no secrets, no model weights, no machine-local
      paths in tracked files (FR-016/019/020)
- [x] T067 [P] [US3] Implement file/vision routing helper in
      `src/aichestra/orchestration/media_routing.py` (FR-058; SC-012)

**Checkpoint**: Feature ready for Spec Kit converge / PR

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 → Phase 2 → Phase 3 → Phase 4
- Phase 5 depends on Phase 4
- Phase 6 depends on Phases 3–5
- Phase 7 can proceed after Phase 1–2 (security mostly independent)
- Phase 8 depends on Phases 2–4
- Phase 9 depends on Phases 3, 4, and 8
- Phase 10 depends on Phases 2–7
- Phase 11 depends on Phases 1–10 tests existing
- Phase 12 depends on Phase 8–9
- Phase 13 depends on all prior desired phases

### User Story Mapping

- **US1** (bootstrap/portability): Phases 2, 8, 9
- **US2** (modes/providers/Orca): Phases 4, 5
- **US3** (research/reviewer/verify): Phase 6
- **US4** (staging security): Phase 7
- **US5** (isolation/CI/smoke): Phases 10–12
- **US6** (machine/local AI): Phase 3, parts of 9

### Parallel Opportunities

- T003/T004; T006/T008; T011; T016/T017; T030–T032; T041–T043; T050/T051
- After Phase 4: Phase 5 and Phase 7 can proceed in parallel

---

## Requirement Traceability (high-signal)

| Requirement cluster | Tasks |
|---------------------|-------|
| Windows/Linux native bootstrap | T006, T041–T043 |
| Arbitrary clone path / no fixed home | T005, T009, T053, T054 |
| Machine profiler / local runtime / routing | T010–T014 |
| Three modes + graceful degradation | T015–T024 |
| maintenance-reviewer + verifier + audit | T025–T034 |
| Staging read-only + sanitization | T035–T039 |
| GitHub Actions matrix / no quota | T055–T057 |
| Machine-local config separation | T007, T008, T040, T045 |
| Isolation fixtures | T050–T052 |
| Truthful validation + operator UX | T058–T061, T064 |

---

## Implementation Strategy

1. Complete Phases 1–2 (foundation)
2. Complete Phase 3 (profiler/local AI) + Phase 4 (discovery)
3. Add Phase 5 (modes/Orca/leads)
4. Add Phases 6–7 (workflow + security)
5. Add Phases 8–9 (bootstrap + doctor)
6. Add Phases 10–12 (fixtures, CI, Mac smoke)
7. Phase 13 convergence

Do not stop at scaffolding. Run tests after each meaningful batch. Do not claim
Windows/Linux live validation from macOS-only execution.

---

## Phase 14: Convergence

**Purpose**: Close remaining gaps found by Spec Kit converge against FR/SC/edge cases (append-only; do not renumber prior tasks).

- [x] T068 Implement staging SSH alias resolution with fail-closed unknown-alias messaging and an allowlist-gated remote diagnostic runner that never uses unrestricted shell strings, never reads private key contents, and sanitizes output before cloud handoff per US4 edge case / FR-030/032/034 / Constitution IV (partial)
- [x] T069 Complete operator daily-command documentation in `README.md` for repository research, staging diagnostics invocation, and worktree diff (Orca/native) per FR-064 (partial)


## Phase 15: Convergence

**Purpose**: Close remaining gaps found by Spec Kit converge against FR/SC/edge cases (append-only; do not renumber prior tasks).

- [x] T070 CRITICAL: Tighten staging curl allowlist in `src/aichestra/security/staging_allowlist.py` so read-only structural enforcement rejects remote writes (deny `-X POST/PUT/PATCH/DELETE`, `-d/--data`, `-T/-F/--upload-file`, and local write-out `-o/-O`); keep typed `CURL_HEALTH` GET-style argv allowed; add security fixture tests covering curl write cases before any executor/SSH call per Constitution IV / FR-032 / FR-033 / US4/AC1-AC2 (partial)

## Phase 16: Convergence

**Purpose**: Close remaining gaps found by Spec Kit converge against FR/SC/edge cases (append-only; do not renumber prior tasks).

- [x] T071 CRITICAL: Deny shell redirect / write tokens (`>`, `>>`, and equivalent) in `src/aichestra/security/staging_allowlist.py` before SSH execution, and restrict the operator/public staging path to typed `StagingOp` / `op_kind` only (keep free-form `remote_argv` test-only if retained); add security fixture tests for redirect argv rejected before any executor call per Constitution IV / FR-032 / FR-033 / FR-059 (partial)
- [x] T072 Wire `assess_resources` into local-worker selection/doctor paths and invoke real unload when supported under HIGH/CRITICAL pressure (replace no-op `OllamaRuntime.unload_idle`); never terminate unrelated user apps; add regression tests per FR-052 / resource edge case (partial)
- [x] T073 Honor `AICHESTRA_FAKE_PROVIDERS` / `AICHESTRA_NO_REAL_QUOTA` in `src/aichestra/providers/discovery.py` so CI-injected fake providers are used instead of probing real Codex/Cursor binaries; add a regression test per FR-039 / plan: CI fake providers (partial)
- [x] T074 Add tests for worktree/serial edit lease refuse-shared-checkout and `reset_hard_helper_allowed() is False` in `src/aichestra/orchestration/worktrees.py` per FR-057 (partial)
- [x] T075 Make `repository-researcher` prefer an available local-worker and degrade to compact filesystem research without mislabeling `PROVIDER` when local-worker is unused per FR-053 (partial)
- [x] T076 Persist `--approve-model-download` into existing `.local/machine.local.json` on re-bootstrap (today only recorded when creating the file) per FR-049 / SC-014 (partial)

## Phase 17: Review remediation (Mode C execution + staging quoting)

**Purpose**: Address PR review blockers — executable Mode C provider interface,
strict staging parameter allowlist + remote quoting, truthful phase status /
maintenance-reviewer gate, honest local-worker research labeling.

- [x] T077 CRITICAL: Add Mode C provider execution interface
      (`start_session` / `send` / `execute_task`) on
      `src/aichestra/providers/base.py`, implement for Orca/Codex/Cursor/
      local-worker, wire `OrchestratedWorkflow` to real phase execution;
      keep native Codex/Cursor unintercepted (FR-001/043/045/054/064)
- [x] T078 CRITICAL: Replace staging typed-parameter blacklist with strict
      allowlist grammars + POSIX remote quoting in
      `staging_ops.py` / `staging_ssh.py`; add regression tests proving typed
      params cannot cause an extra remote command (Constitution IV / FR-032/059)
- [x] T079 HIGH: Model workflow phase status
      (pending/running/succeeded/failed/skipped); mark succeeded only after
      real operation ok; enforce maintenance-reviewer before writer phases
      (FR-023/054)
- [x] T080 HIGH: Invoke local-worker for research when selected or keep
      `PROVIDER=filesystem`; honor `--query` inside research
      (FR-021/053); update CLI `research` / add `orchestrate`

## Phase 18: Architecture remediation (Orca control plane)

**Purpose**: Close PR #1 P0–P1 gaps against target architecture —
Aichestra is policy/bootstrap/integration; Mode C is one Orca Run with
tasks/workers/worktrees; Mode A remains native Codex/Cursor outside Mode C.
Green CI with fake providers must not encode Orca bypass.

### P0 — Mode C / Orca

- [x] T081 CRITICAL: Mode C agent phases (implement, review, writers) execute
      only through Orca; on Orca UNAVAILABLE fail Mode C — do not fall back to
      direct Codex/Cursor `_run_lead` (FR-001/043/045; Constitution VI). Mode A
      remains native and separate.
- [x] T082 CRITICAL: Create exactly one Orca Run per Mode C workflow; persist
      `orca_run_id` on `WorkflowState`; reuse for all task-create/worker-start;
      stop `_report_orca_phase` / control_plane from creating additional Runs
      (FR-001/045).
- [x] T083 CRITICAL: After `worker-start --worktree new-child`, capture
      worktree path/id from receipt; adopt as effective project root for
      maintenance-reviewer, writers, and verification; record explicit
      integration policy (continue on child — no silent verify-on-parent)
      (FR-057).
- [x] T084 CRITICAL: Replace tests that require Orca-unavailable→lead bypass
      (`test_phase_succeeded_only_after_operation_ok` and similar); add contract
      tests: Mode C without Orca fails; one run_id reused; worktree adoption
      switches effective root (FR-045/062).

### P1 — policy fidelity

- [x] T085 HIGH: maintenance-reviewer inputs include implementation diff (not
      only changed paths), and do not treat “any test file touched” as
      `existing_tests_cover=True` without stronger signal (FR-023/055).
- [x] T086 HIGH: Spec Kit MEDIUM/LARGE execute real brief/plan/tasks gates
      (or hard-block implement) rather than metadata-only stubs (FR-028/063).
      *(hard-block until metadata statuses ready/approved or *_satisfied)*
- [x] T087 HIGH: Deliver attachments via Orca/native file capability (not only
      path text in prompt); enforce vision provider_hint (FR-058).
      *(stage inbox + Orca --attach + Codex/Cursor image flags; vision fails
      closed when bytes_delivered=false)*
- [x] T088 HIGH: `aichestra handoff` performs one-action Orca
      `worktree create --agent cursor --prompt …` when Orca available; JSON
      packet alone is insufficient (FR-035/036).
      *(default execute; `--prepare-only` keeps packet-only)*
- [x] T089 HIGH: Bootstrap performs or clearly reports remaining install /
      Orca+provider integration / local-worker / smoke steps; do not mark
      download approval as download (FR-013/014; SC truthful status).
- [x] T090 HIGH: Doctor reports version/commit, Git, Orca+skills, Codex,
      Cursor, OpenCode, models, selected model+reason, Spec Kit detection,
      build/test commands per FR-040/061.
- [x] T091 HIGH: `smoke_mac.py` labels LIVE only after real execute_task (or
      rename probe-only to DISCOVERED); LIVE ≠ DISCOVERED (Constitution V).
      *(probe → DISCOVERED; `AICHESTRA_SMOKE_EXECUTE=1` → LIVE on ok execute;
      `AICHESTRA_SMOKE_ORCA_SUPERVISED=1` → Mode C-shaped supervised smoke)*

### P2 — follow-ups (non-blocking for architecture merge bar if P0+P1 done)

- [ ] T092 MEDIUM: LocalWorkerProvider selects via LocalRuntime abstraction
      (not hard-wired Ollama) (FR-047/051).
- [ ] T093 MEDIUM: Expand `run_cli_task` failure classification for NETWORK /
      CRASH / CANCEL (FR-062 / graceful degradation).
- [ ] T094 MEDIUM: Windows profiler: CIM/PowerShell fallback when WMIC absent.
- [ ] T095 LOW: Fold review-history regression tests into canonical suites
      (orca/workflow/bootstrap/security) per anti-bloat policy.

## Phase 19: Structural Mode C — one Orca Run controller

**Purpose**: Stop treating symptom patches as architecture. Make Mode C a thin
controller over **one Orca Run**; Aichestra owns policy + deterministic gates
only. Re-align artifacts already updated in this phase; complete code/tests.

### P0 — ownership model

- [x] T096 CRITICAL: Rename/document `OrchestratedWorkflow` as
      `ModeCRunController`; state that canonical lifecycle is `orca_run_id`
      (resume supported); local phases are policy/gate schedule mirrors, not a
      competing orchestrator (FR-001/043/045/054; Constitution VI).
- [x] T097 CRITICAL: Mode C agent steps (research, implement, writers,
      lead_review) dispatch **only** via Orca under the same Run; remove
      direct `local_worker` / `writer_fn`→lead / `_run_lead` Mode C paths
      (FR-045/054).
- [x] T098 CRITICAL: Lead review + maintenance gates receive adopted-worktree
      change signals (diff/paths/status) — never silent parent-checkout review
      when a child worktree was created (FR-055/057).
- [x] T099 CRITICAL: Rewrite integration/contract tests that encode
      dual-orchestrator fan-out (`lead.sent` / `worker.sent` as Mode C agents);
      assert one `run_id`, Orca-only agent dispatch, worktree adoption
      (FR-045/062).

### P1 — remaining fidelity

- [x] T100 HIGH: Spec Kit LARGE path invokes real skill/command flow or remains
      hard-blocked until artifacts exist — no `status=recorded` fake success
      (FR-028/063).
      *(pending + hard-block; clarify metadata; speckit_execution marker)*
- [x] T101 HIGH: Attachments delivered via Orca native attach when available
      (close T087) (FR-058).
- [x] T102 HIGH: Governance ADR/SPEC pending has an Orca task continuation path
      (not dead-end) (FR-055).
      *(one Orca governance task under same Run before hard-stop)*
