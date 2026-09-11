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
- [ ] T013 BLOCKED by T022: Same-Run configured auto quota retry is described by
  `role_dispatch_contract.quota` / `aichestra dispatch-role --reason quota-fallback`; Aichestra no longer
  auto-retries coding workers itself. Live automatic implement fallback still
  requires coordinator (or operator) to call dispatch-role for the fallback
  target, plus durable launch receipts where Orca provides them.
- [x] T014 Remove disabled implement remap; preserve exact provider/model binding
- [x] T015 Infer project root from nearest project config or resolved cwd
- [x] T016 Interactive settings and init editor using runtime/model discovery
- [x] T017 Accept registered execution runtime ids without roles.py edits
- [x] T018 Fail preflight before orchestration when verification is not configured
- [x] T019 Add wheel-build/clean-venv/foreign-cwd CLI smoke to the OS matrix
- [x] T020 Remove machine-local IDE workspace and account metadata; ignore .idea
- [x] T021 Complete regression tests and local installed-wheel smoke
- [ ] T022 PARTIAL: `aichestra dispatch-role` provides policy-enforced
  worker-start for the immutable Run role binding (pre-dispatch pin without making
  Aichestra a scheduler). Remaining: durable Orca `launch.effective` receipts
  for post-dispatch audit, and live coordinator adoption of dispatch-role.
  Fake CI is not live evidence. Do not mark this feature fully implemented
  while T022 is open.


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

## PR #2 REQUEST CHANGES (custom runtime / provisionable / dispatch-role)

- [x] T028 Pass `execution.runtimes` allowlist into every ModeCRunController
  re-parse of coordinator binding and quota policy; e2e custom coordinator
  through `run_all()` and custom quota through policy package.
- [x] T029 Provisionable worker bindings emit `state` /
  `requires_launch_proof` / `candidate_id` in `role_dispatch_contract`;
  package consistency asserts runnable↔execution_targets or
  provisionable↔candidates.
- [x] T030 `aichestra dispatch-role --run --task --role`: resolve project
  Run contract → prove if needed → Orca worker-start exact target. Coordinator
  still owns the DAG; this is the policy-enforced Dispatch adapter for T022.
## Run contract and installed-package review corrections

- [x] T031 Accept installed user config homes at prove-launch/dispatch-role boundaries.
- [x] T032 Persist immutable Run contracts before handoff; enforce saved target
  and canonical Task role/Run association before worker launch.
- [x] T033 Add quota-fallback reason with same-Run primary quota receipt validation.
- [x] T034 Extend isolated wheel smoke to orchestrate/prove-launch/dispatch-role
  config boundaries and add contract drift / negative quota regressions.

T013/T022 still require live coordinator adoption and durable Orca launch receipts;
these deterministic corrections do not claim live integration acceptance.
