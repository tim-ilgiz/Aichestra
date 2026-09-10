# Tasks: Project init + role routing

**Input**: `specs/002-project-init-role-routing/`

## Phase 1 — Spec & config foundation

- [x] T001 Write spec.md / plan.md / tasks.md for 002
- [x] T002 Portable user config home + package-bundled defaults
- [x] T003 Role binding normalize/validate (runtime + provider/model)

## Phase 2 — CLI

- [x] T004 `aichestra init` creates `.aichestra/project.json`
- [x] T005 `aichestra settings show|set` (roles, quota, verify)

## Phase 3 — Mode C policy

- [x] T006 Serialize `role_bindings` + `quota_policy` into POLICY_PACKAGE
- [x] T007 Coordinator preamble MUST for roles + quota manual/auto
- [x] T008 Fail closed / remap when implement-bound runtime disabled by CLI

## Phase 4 — Reliability + tests

- [x] T009 Fix Mode C monitor / verification ask timeout mismatch
- [x] T010 Unit + contract tests; README daily commands
