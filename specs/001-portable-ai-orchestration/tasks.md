---
description: "Task list for portable Aichestra platform implementation"
---

# Tasks: Portable Aichestra Platform

**Input**: Design documents from `/specs/001-portable-ai-orchestration/`

**Prerequisites**: plan.md (required), spec.md (required)

**Tests**: Included where required by success criteria (SC-001–SC-010) and
security/isolation invariants. Prefer minimal high-value coverage.

**Organization**: Phases follow foundation → user stories → CI/smoke/docs.
Story labels map to spec user stories (US1–US5).

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US5 traceability to spec.md

## Phase 1: Repository / Python foundation

**Purpose**: Create the installable Python package skeleton without provider
runtime implementation.

- [ ] T001 Create package layout `src/aichestra/` and `tests/` per plan.md
- [ ] T002 Add `pyproject.toml` for Python 3.11+ package metadata and pytest
- [ ] T003 [P] Add root `.gitignore` excluding secrets, `.env`, model weights,
      sessions, caches, machine-local config, and large logs (FR-016/019/020)
- [ ] T004 [P] Add minimal `tests/conftest.py` with shared path fixtures only

**Checkpoint**: Package imports; no Orca/provider installs yet beyond stubs

---

## Phase 2: Cross-platform configuration and platform detection

**Purpose**: Arbitrary clone path + OS detection + config layering (US1)

- [ ] T005 [US1] Implement repo-root resolution in `src/aichestra/repo.py`
      for arbitrary clone paths (FR-010/011; SC-001)
- [ ] T006 [P] [US1] Implement OS detection helpers in
      `src/aichestra/platform_detect.py` (FR-009/012)
- [ ] T007 [US1] Implement tracked vs machine-local config layering in
      `src/aichestra/config/layering.py` (FR-016/041; SC-010)
- [ ] T008 [P] [US1] Implement hardware profiles in
      `src/aichestra/config/hardware_profiles.py` including M4 Pro 24 GB
      conservative ~14B / one-worker default (FR-017/018)
- [ ] T009 [US1] Unit tests for repo root, OS detect, and config layering in
      `tests/unit/test_repo_config.py` (SC-001/SC-003 path invariants)

**Checkpoint**: Path/config foundation ready; no fixed home paths in tracked
defaults

---

## Phase 3: Provider discovery / graceful degradation

**Purpose**: Discover providers and degrade usefully (US2)

- [ ] T010 [US2] Define provider adapter interfaces in
      `src/aichestra/providers/base.py`
- [ ] T011 [P] [US2] Implement discovery in
      `src/aichestra/providers/discovery.py` (FR-003–008)
- [ ] T012 [P] [US2] Add fake/absent provider test doubles under
      `tests/fakes/` for Codex, Cursor, and local worker (FR-039; SC-004)
- [ ] T013 [US2] Integration tests proving useful operation with no local
      worker, no Codex, and no Cursor in
      `tests/integration/test_provider_degradation.py` (SC-004)

**Checkpoint**: Discovery returns structured availability; missing providers do
not hard-fail core workflows

---

## Phase 4: Orca integration

**Purpose**: Orca as opt-in control plane; native tools untouched (US2)

- [ ] T014 [US2] Implement Orca adapter/integration hooks in
      `src/aichestra/providers/orca.py` (FR-001)
- [ ] T015 [US2] Ensure native Codex/Cursor invocation paths remain unwrapped in
      `src/aichestra/orchestration/roles.py` (FR-002; Principle VI)
- [ ] T016 [US2] Implement Codex preferred / Cursor fallback lead selection in
      `src/aichestra/orchestration/roles.py` (FR-003/004)
- [ ] T017 [US2] Implement Codex→Cursor handoff with bounded context and
      documented manual fallback policy in
      `src/aichestra/orchestration/handoff.py` (FR-035/036)
- [ ] T018 [US2] Contract/unit tests for lead selection and handoff in
      `tests/contract/test_lead_handoff.py`

**Checkpoint**: Orchestrated mode is opt-in; native mode remains available

---

## Phase 5: Local OpenCode/Ollama worker

**Purpose**: Optional local worker (US2)

- [ ] T019 [US2] Implement optional local worker adapter in
      `src/aichestra/providers/local_worker.py` (FR-005/006)
- [ ] T020 [US2] Wire hardware-profile defaults without committing weights or
      host paths (FR-017–020)
- [ ] T021 [US2] Tests that platform operates with local inference disabled in
      `tests/integration/test_local_worker_optional.py` (SC-004)

**Checkpoint**: Local worker optional; disabled mode remains useful

---

## Phase 6: Agent roles and maintenance-reviewer

**Purpose**: Research compaction + anti-bloat gates (US3)

- [ ] T022 [US3] Implement local repo research support in
      `src/aichestra/orchestration/research_compact.py` (FR-021)
- [ ] T023 [US3] Implement research compaction before cloud lead handoff
      (FR-022)
- [ ] T024 [US3] Implement maintenance-reviewer gate in
      `src/aichestra/orchestration/maintenance_reviewer.py`
      (FR-023/024/025)
- [ ] T025 [US3] Implement test/doc writer preference helpers that update
      existing canonical artifacts first (FR-026/027)
- [ ] T026 [P] [US3] Encode Spec Kit proportionality policy helper
      (FR-028)
- [ ] T027 [P] [US3] Implement Factory-preservation guard in
      `src/aichestra/orchestration/factory_preserve.py` (FR-029)
- [ ] T028 [US3] Fixture tests for maintenance-reviewer decisions in
      `tests/integration/test_maintenance_reviewer.py` (SC-005)
- [ ] T029 [US3] Add reusable maintenance-audit workflow stub/docs entry for
      later target-project bloat reduction (FR-042)

**Checkpoint**: Reviewer can block unnecessary tests/docs; research is compacted

---

## Phase 7: Stage security and sanitization

**Purpose**: Structural staging-only diagnostics (US4)

- [ ] T030 [US4] Implement staging-only diagnostic boundary and deny production
      SSH paths in `src/aichestra/security/staging_allowlist.py`
      (FR-030/031)
- [ ] T031 [US4] Enforce read-only command allowlist outside LLM prompts
      (FR-032)
- [ ] T032 [US4] Reject dangerous commands before remote execution (FR-033;
      SC-006)
- [ ] T033 [US4] Implement diagnostic sanitization in
      `src/aichestra/security/sanitize.py` (FR-034)
- [ ] T034 [US4] Security fixture tests in
      `tests/security/test_staging_commands.py` and
      `tests/security/test_sanitize.py` (SC-006)

**Checkpoint**: Destructive commands never reach remote execution

---

## Phase 8: macOS / Windows / Linux bootstrap

**Purpose**: Idempotent OS entrypoints (US1)

- [ ] T035 [US1] Implement shared idempotent bootstrap core in
      `src/aichestra/bootstrap/core.py` (FR-014/015/041)
- [ ] T036 [P] [US1] Add `scripts/bootstrap_macos.py` (FR-013)
- [ ] T037 [P] [US1] Add `scripts/bootstrap_windows.py` (FR-013; Windows native)
- [ ] T038 [P] [US1] Add `scripts/bootstrap_linux.py` (FR-013; Linux native)
- [ ] T039 [US1] Tests for idempotent bootstrap and machine-local preservation
      in `tests/integration/test_bootstrap.py` (SC-010)

**Checkpoint**: Fresh clone can bootstrap on each OS entrypoint path

---

## Phase 9: Doctor

**Purpose**: Cross-platform health check (US1/US5)

- [ ] T040 [US1] Implement doctor/health check in
      `src/aichestra/doctor.py` and `scripts/doctor.py` (FR-040)
- [ ] T041 [US1] Doctor reports repo root, OS, provider availability, and
      degraded-mode guidance without claiming unrun live validation
- [ ] T042 [US1] Unit/integration tests in `tests/unit/test_doctor.py`

**Checkpoint**: Doctor is the canonical local health entrypoint

---

## Phase 10: Fixture repositories and integration tests

**Purpose**: Project isolation proof (US5)

- [ ] T043 [P] [US5] Create fixture repository A under `fixtures/project_a/`
      (FR-037)
- [ ] T044 [P] [US5] Create fixture repository B under `fixtures/project_b/`
      (FR-037)
- [ ] T045 [US5] Isolation tests proving no task/config/runtime leakage in
      `tests/integration/test_fixture_isolation.py` (SC-007)
- [ ] T046 [US5] Scan/assert tracked production config has no developer home
      paths or credentials in `tests/contract/test_no_machine_paths.py`
      (SC-003; FR-011)

**Checkpoint**: Two fixtures prove project-agnostic isolation

---

## Phase 11: GitHub Actions matrix

**Purpose**: CI on macOS/Windows/Linux without real quota (US5)

- [ ] T047 [US5] Add `.github/workflows/ci.yml` matrix for macOS, Windows,
      Linux (FR-038; SC-002)
- [ ] T048 [US5] Wire fake providers so CI never consumes real Codex/Cursor
      quota (FR-039)
- [ ] T049 [US5] Ensure CI runs core unit/integration/security suites and fails
      on regressions

**Checkpoint**: Automated matrix is the portability proof until live smoke exists

---

## Phase 12: Current Mac live smoke validation

**Purpose**: Truthful live reporting (US5)

- [ ] T050 [US5] Add Mac live smoke script/checklist runner that records only
      actually available components (SC-008)
- [ ] T051 [US5] Ensure Windows/Linux live status remains NOT VALIDATED until
      real smoke runs (SC-009)
- [ ] T052 [US5] Document smoke report format in README or operator section
      without claiming unrun platforms

**Checkpoint**: Validation claims match executed evidence

---

## Phase 13: Operator documentation / final convergence

**Purpose**: Canonical docs only; converge to Definition of Done

- [ ] T053 Update root `README.md` with implemented bootstrap/doctor usage only
      after those entrypoints exist (no premature install fiction)
- [ ] T054 Verify Spec Kit proportionality note and AGENTS.md remain the
      process entrypoints (FR-028)
- [ ] T055 Run full local pytest suite; fix failures; mark any blocked live
      validation explicitly
- [ ] T056 Cross-check FR-001–FR-042 and SC-001–SC-010 against implemented
      tasks; close gaps or record NOT VALIDATED blockers
- [ ] T057 Final repo hygiene: no secrets, no model weights, no machine-local
      paths in tracked files

**Checkpoint**: Feature ready for Spec Kit converge / PR

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 → Phase 2 → Phase 3
- Phase 4 and Phase 5 depend on Phase 3
- Phase 6 depends on Phase 3–4 (roles/handoff)
- Phase 7 can proceed after Phase 1–2 (security mostly independent)
- Phase 8 depends on Phase 2–3
- Phase 9 depends on Phase 3 and Phase 8
- Phase 10 depends on Phases 2–7
- Phase 11 depends on Phases 1–10 tests existing
- Phase 12 depends on Phase 8–9
- Phase 13 depends on all prior desired phases

### User Story Mapping

- **US1** (bootstrap/portability): Phases 2, 8, 9
- **US2** (providers/Orca): Phases 3, 4, 5
- **US3** (research/reviewer): Phase 6
- **US4** (staging security): Phase 7
- **US5** (isolation/CI/smoke): Phases 10–12

### Parallel Opportunities

- T003/T004; T006/T008; T011/T012; T036–T038; T043/T044
- After Phase 3: Phase 4 and Phase 7 can proceed in parallel
- Phase 5 after local-worker interface from Phase 3

---

## Requirement Traceability (high-signal)

| Requirement cluster | Tasks |
|---------------------|-------|
| Windows/Linux native bootstrap | T006, T036–T038 |
| Arbitrary clone path / no fixed home | T005, T009, T046 |
| Graceful provider degradation | T010–T013, T019–T021 |
| maintenance-reviewer + conditional tests/docs | T024–T028 |
| Staging read-only + sanitization | T030–T034 |
| GitHub Actions matrix / no quota | T047–T049 |
| Machine-local config separation | T007, T008, T035, T039 |
| Isolation fixtures | T043–T045 |
| Truthful validation | T050–T052, T055 |

---

## Implementation Strategy

1. Complete Phases 1–2 (foundation)
2. Complete Phase 3 (discovery) → MVP degraded-mode platform core
3. Add Phases 4–5 (Orca + optional local worker)
4. Add Phases 6–7 (reviewer + security)
5. Add Phases 8–9 (bootstrap + doctor)
6. Add Phases 10–12 (fixtures, CI, Mac smoke)
7. Phase 13 convergence

Do not stop at scaffolding. Run tests after each meaningful batch. Do not claim
Windows/Linux live validation from macOS-only execution.
