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
- [x] T008 Fail closed without remapping when implement-bound runtime disabled by CLI

## Phase 4 — Reliability + tests

- [x] T009 Fix Mode C monitor / verification ask timeout mismatch
- [x] T010 Unit + contract tests; README daily commands


## PR #2 review corrections

- [x] T011 Resolve role runtime/provider/model into an exact preparable target
- [x] T012 Audit all canonical role dispatch receipts; fail closed on mismatch
- [x] T013 Same-Run configured auto quota retry; manual stop; bounded exhaustion
- [x] T014 Remove disabled implement remap; preserve exact provider/model binding
- [x] T015 Infer project root from nearest project config or resolved cwd
- [x] T016 Interactive settings and init editor using runtime/model discovery
- [x] T017 Accept registered execution runtime ids without roles.py edits
- [x] T018 Fail preflight before orchestration when verification is not configured
- [x] T019 Add wheel-build/clean-venv/foreign-cwd CLI smoke to the OS matrix
- [x] T020 Remove machine-local IDE workspace and account metadata; ignore .idea
- [x] T021 Complete regression tests and local installed-wheel smoke
- [ ] T022 BLOCKED: installed Orca workerShow returns dispatch/worker/startOptions
  but no durable effective launch binding, and Orca does not pin inner Dispatch
  to Aichestra `execution_target_id`. Do not substitute requested options for
  execution evidence. Exact-role and automatic quota live acceptance require
  that upstream contract. Fake CI is not live evidence. Do not mark this
  feature implemented while T022 is open.


## PR #2 REQUEST CHANGES (coordinator ≠ implement)

- [x] T023 Split `orchestration.coordinator` from `roles.implement`. Bootstrap
  uses the coordinator target only. `roles.*` remain worker roles.
  `roles.coordinator` is invalid.
- [x] T024 Quota auto/manual applies to `quota.roles.implement` (coding
  worker). Coordinator quota MUST NOT consume implement fallback or relaunch
  the coordinator on a different LLM. Accept legacy `quota.implement_fallback`
  on load.
- [x] T025 Emit typed `role_dispatch_contract` (role → exact
  `execution_target_id`). Coordinator owns which Tasks to create and when;
  Orca MUST Dispatch the bound target. Do not claim pre-dispatch enforcement
  while T022 is blocked.
- [x] T026 Settings/init UI and README: Coordinator vs Coding; honest blocker
  that live exact Dispatch pinning is not available on current Orca.
- [x] T027 Behavioural tests: distinct coordinator vs implement bootstrap;
  disabled coordinator vs disabled implement; auto quota does not replace
  coordinator; contract carries exact worker target ids.
