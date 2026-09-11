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
- [x] T013 CLOSED via T049 (2026-09-11): Same-Run auto quota retry shares
  `resolve_worker_failure(..., durable_messages=)` between `authorize_task`
  and settle `validate_role_receipts`. `_audit_role_dispatches` loads the same
  `check --all` worker_done mail. Contract regression covers Orca 1.4.200
  (lastFailure without typed failure + payload.failure=quota → final audit
  OK). Prior live authorize: run `run_b3f15bcba245`, primary
  `ctx_4fb091aa3333`, fallback `ctx_ba96c22a6ea4`. Live end-to-end settle on a
  fresh Mode C Run after T049 remains optional smoke (not a merge blocker
  once PR CI is green).
- [x] T014 Remove disabled implement remap; preserve exact provider/model binding
- [x] T015 Infer project root from nearest project config or resolved cwd
- [x] T016 Interactive settings and init editor using runtime/model discovery
- [x] T017 Accept registered execution runtime ids without roles.py edits
- [x] T018 Fail preflight before orchestration when verification is not configured
- [x] T019 Add wheel-build/clean-venv/foreign-cwd CLI smoke to the OS matrix
- [x] T020 Remove machine-local IDE workspace and account metadata; ignore .idea
- [x] T021 Complete regression tests and local installed-wheel smoke
- [x] T022 LIVE VALIDATED (2026-09-11, Orca 1.4.200): Inner worker Dispatch
  MUST use `aichestra dispatch-role`. Direct Orca worker-start does not settle.
  Aichestra persists dispatch-role adoption receipts and audits them with
  durable `startOptions.launch.effective`. Live coordinator (Cursor under Orca)
  created tests Task `task_a04c1c7f110b` on run `run_46902f941734` and invoked
  dispatch-role → `ctx_fc0f8a58b7b5` (cursor); worker-show
  `launch.effective.agent=cursor`; production `validate_role_receipts` ok
  (2 dispatches: coordinator bootstrap + inner). Fake CI is not this evidence.


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

T013 closed via T049 settle-audit / authorize durable-quota parity;
T022 live validated via coordinator-driven dispatch-role adoption receipts plus
durable `launch.effective` (run `run_46902f941734`). These deterministic
corrections remain in force.

## Resume / immutable target review corrections

- [x] T035 Resume never creates or replaces a contract; cover missing, changed,
  and unchanged contracts through run_all.
- [x] T036 Resolve saved provider/model requirements through the production
  compatibility pipeline after settings drift; preserve disabled restrictions.
- [x] T037 Release prepared bootstrap resources on contract persistence failure.
- [x] T038 Fingerprint full endpoint identity, preserving query in attestation.
- [x] T039 Refuse unpinned agent fallback after terminal/model adapter failure;
  pass existing terminal launch references to the adapter.

## Research artifact policy

- [x] T040 Emit deterministic `research_artifact` (`none`|`file`|`compacted`)
  in POLICY_PACKAGE with coordinator MUST instructions; not a phase scheduler.

## PR #2 REQUEST CHANGES (installed package / layered runtimes / verification / dispatch ownership)

- [x] T041 `profile` / `doctor` / `bootstrap` use `resolve_aichestra_config_root`
  (never bare `find_repo_root` from a foreign project); bootstrap writes
  machine-local under user config home without mutating foreign `.gitignore`.
  Extend `smoke_wheel.py` with profile/doctor/bootstrap from unrelated cwd.
- [x] T042 Settings validation accepts runtimes registered in layered
  machine/global config without requiring a duplicate in project.json.
- [x] T043 `verification.enabled` is strict boolean (`is True` / settings
  reject non-bool with exit 2); string typos must not fail-open.
- [x] T044 `dispatch-role` must not call `run-use` (avoids transferring Run
  ownership away from the coordinator); authorize via task/run association
  then `worker-start --task`.
- [x] T045 Legacy `quota.implement_fallback` applies when `quota.roles` is
  missing the implement key (including empty `roles: {}`); explicit
  `quota.roles.implement` wins.

T013 closed via T049 settle-audit durable worker_done `payload.failure`
parity with authorize. T022 live validated via coordinator-driven
dispatch-role and `launch.effective` (run `run_46902f941734`).

## Live Orca typed-quota readiness (2026-09-11)

- [x] T046 Accept Orca camelCase `completedAt` and typed `lastFailure.failure`
  (also `rate_limit` / `quota_exhausted` → `quota`) in authorize + receipt
  audit; refuse prose-only subject/body. Contract tests cover nested
  `startOptions.launch.effective` and lastFailure JSON.
- [x] T047 `dispatch-role --from` + `--run` on worker-start; never treat Orca
  envelope id as Dispatch id; never call `run-use` from dispatch-role.
- [x] T048 Read durable `worker_done` message `payload.failure` via
  `check --all` when `lastFailure` omits the typed class (Orca 1.4.200);
  live quota-fallback validated on run_b3f15bcba245.

## Master post-merge review corrections (2026-09-11)

- [x] T049 Unify `resolve_worker_failure(worker_receipt, durable_messages=...)`
  for `authorize_task` and settle `validate_role_receipts`; `_audit_role_dispatches`
  loads the same `check --all` worker_done mail. Contract regression covers
  Orca 1.4.200 shape (lastFailure without failure + payload.failure=quota →
  final audit OK).
- [x] T050 `dispatch-role` returns `ok: false` when adoption receipt persistence
  fails after worker-start; attempt controlled `worker-release` and report
  `worker_started` / release status.
- [x] T051 Persist immutable per-dispatch adoption receipts under
  `<run-hash>.dispatch-role/<dispatch-hash>.json` with exclusive create;
  `load_dispatch_role_receipts` gathers the directory (legacy shared JSON
  array still readable). Partial-write `OSError` cleans up the empty file.
- [x] T052 Confirm real worker release after failed adoption persist: exit 0
  alone is insufficient; mirror `_release_worker` bounded recovery
  (`release_pending`/`release_unknown` → allowed recovery + `worker-show`)
  before setting `worker_release_ok=true`. Contract: exit=0 +
  `state=release_pending` → `worker_release_ok=false`.
