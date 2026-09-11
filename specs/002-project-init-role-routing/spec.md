# Feature Specification: Project init + role routing

**Feature Branch**: `002-project-init-role-routing`

**Created**: 2026-09-10

**Status**: Partial; live Orca exact Dispatch binding is blocked

**Input**: Global pip-installable Aichestra; `aichestra init` in a target project;
Console settings CLI for coordinator LLM vs worker role bindings; Mode C
honors a typed role→target contract; quota applies to coding workers, not
the coordinator.

## Architecture constraints

- Orca remains the only Mode C orchestration control plane.
- Aichestra MUST NOT become a Python phase scheduler or call Codex/Cursor/
  OpenCode `execute_task` for Mode C agent work.
- The coordinator LLM and worker LLMs are independent policy bindings.
  `roles.*` are exclusively worker roles. The Mode C coordinator LLM is
  `orchestration.coordinator`, never `roles.implement`.
- Explicit project/user role bindings ARE allowed policy (not hard-coded
  product-phase routing in core). Values are AgentRuntime ids plus optional
  provider/model for local/OpenCode targets.
- Coordinator under Orca still owns the DAG: which Tasks to create and when.
  Aichestra MUST emit a typed role-dispatch contract
  (`role=R → exact execution_target_id=X`, with `state` /
  `requires_launch_proof` when the bound target is still a candidate).
  Preferred enforcer is `aichestra dispatch-role` (policy-enforced
  worker-start on an existing Task). Direct Orca Dispatch of X remains
  allowed for runnable targets already in `execution_targets`. The
  coordinator LLM MUST NOT interpret or substitute another target.
  Durable `launch.effective` receipts for post-dispatch audit still depend
  on Orca; missing evidence fails closed and is not claimed as live proof.

## User stories

### US1 — Global install + project init (P1)

A developer installs Aichestra as a Python package, enters **any** target
project directory, runs `aichestra init`, and gets `.aichestra/project.json`
with default coordinator, worker role bindings, and verify hints without
needing the Aichestra git clone as cwd.

**Acceptance**

1. `pip install` / editable install exposes `aichestra` CLI.
2. `aichestra init [--yes] [--project-root PATH]` creates `.aichestra/project.json`.
3. Machine-local config resolves to a portable user config home when not in the
   Aichestra clone.
4. Package-bundled defaults load without a clone checkout.

### US2 — Settings CLI for coordinator, worker roles, and models (P1)

Developer configures the coordinator LLM independently from which runtime
(and optional model/provider) handles implement, research, tests, and docs —
including Codex, Cursor, OpenCode, and local models.

**Acceptance**

1. `aichestra settings show` prints coordinator, worker roles, quota, and verify.
2. `aichestra settings set roles.implement=codex` binds **coding workers only**.
3. `aichestra settings set orchestration.coordinator=cursor` binds the Mode C
   coordinator LLM without changing `roles.implement`.
4. Object form works: `roles.tests={"runtime":"opencode","model":"qwen2.5-coder:14b"}`
   or dotted keys `roles.tests.runtime=opencode` + `roles.tests.model=...`.
5. Unknown runtime ids fail closed; execution.runtimes registrations are valid.
6. `quota.mode=manual|auto` and `quota.roles.implement` (coding-worker fallback)
   are settable. Legacy `quota.implement_fallback` is accepted as an alias for
   `quota.roles.implement` when loading.

### US3 — Mode C honors coordinator vs worker bindings (P1)

On `orchestrate`, POLICY_PACKAGE includes `orchestration.coordinator`,
`role_bindings` (workers only), `role_dispatch_contract`, and `quota_policy`.
Bootstrap uses the coordinator target. Worker Dispatch MUST use the exact
bound ExecutionTarget id from the contract.

**Acceptance**

1. Package contains normalized worker `role_bindings` from project.json.
2. Package contains `orchestration.coordinator` and a typed
   `role_dispatch_contract` mapping each worker role to one
   `execution_target_id`.
3. Bootstrap ExecutionTarget is `orchestration.coordinator`, never
   `roles.implement`. Setting `roles.implement=codex` MUST NOT make Codex the
   coordinator.
4. If the coordinator runtime or a worker-bound runtime is disabled by CLI
   (`--no-codex` when that binding is codex), Mode C fails closed before silent
   substitution. Disabling implement does not remap the coordinator, and
   disabling the coordinator does not remap implement.

### US4 — Quota notify vs auto fallback for coding workers (P2)

When the **implement** worker hits quota/rate-limit:

- `manual`: stop and ask the operator to change `roles.implement` /
  `quota.roles.implement` via CLI. Do not replace the coordinator.
- `auto`: remaining implement work in the same Run MUST use the exact
  `quota.roles.implement` target. Coordinator LLM stays
  `orchestration.coordinator`. No direct Aichestra Cursor/Codex execute.

Coordinator quota is a different failure: fail closed and tell the operator
to change `orchestration.coordinator`. `quota.roles.implement` MUST NOT
replace the coordinator.

Live inner-Dispatch quota switching uses `aichestra dispatch-role` (or
direct Orca Dispatch of the exact `quota.roles.implement` target). Until
coordinators adopt that entrypoint and Orca provides durable
`launch.effective` receipts, Aichestra audits receipts after the fact and
fails closed on mismatch; it does not claim soft-pass when evidence is
missing.

### US5 — Verification deferred by default (P2)

`aichestra init` MUST NOT invent language-specific `verify` commands.
When `verification.enabled=false`, the Mode C verification gate MUST NOT run
checks and MUST return a non-zero / `ok=false` result to Orca (fail closed —
no soft-pass). Explicit `verification.enabled=true` plus `verify` commands
still run when present. Language-aware verification is a follow-up.

## Out of scope

- Full graphical installer TUI (a small console settings editor is in scope)
- Per-model pins inside Codex/Cursor cloud product UIs beyond Dispatch args
  when Orca supports them
- OpenAI billing API polling
- Aichestra-owned research→implement→tests→docs phase graph
- Claiming live exact-role Dispatch enforcement on an Orca that cannot persist
  `launch.effective` or pin Dispatch to `execution_target_id`

## PR #2 acceptance corrections

- Resolve every configured worker role to one exact ExecutionTarget before Run
  creation; role provider/model declarations participate in compatibility
  resolution. Disabled, missing, incompatible or unsupported targets fail
  closed. Registered `execution.runtimes` ids are accepted alongside built-ins.
- Resolve `orchestration.coordinator` to one exact bootstrap ExecutionTarget
  independently of `roles.implement`.
- Canonical Orca task/worker receipts must prove role, same-Run task
  association, and effective runtime/provider/model/endpoint. Missing evidence
  or a mismatch fails the Run. A prompt or source-text assertion is not proof
  of dispatch. This audit is post-dispatch; it is not a substitute for Orca
  pinning inner Dispatch to the contract target.
- Auto quota recovery applies to `quota.roles.implement` (coding worker) in
  the same Run. It MUST NOT relaunch or replace the coordinator. Inner-task
  fallback receipts require a prior structured **implement** quota outcome and
  chronological same-Run evidence; manual mode stops with operator guidance.
- An explicitly disabled coordinator or implement binding must never be
  remapped, including when its runtime differs from legacy preferred_lead.
- `orchestrate --prompt ...` finds the nearest ancestor `.aichestra/project.json`,
  otherwise uses resolved cwd. `--project-root` remains an explicit override.
- `settings` opens a console menu for Coordinator, Coding, Research, Tests,
  Documentation, Quota fallback and Verification; available choices use
  discovery. `init` without `--yes` opens this editor on a terminal.
  Noninteractive automation retains `init --yes` and `settings show|set`.
- Init keeps verification disabled without inventing project commands. Run
  preflight rejects missing/disabled verification before discovery or paid work.
- Every CI OS builds a wheel and installs it into a clean venv, then exercises
  version/init/settings from an unrelated temporary project without checkout
  imports. Fake-provider tests do not establish live Orca compatibility.
- Machine-local IDE workspace/account state must not be tracked.

## Known external blocker

`aichestra dispatch-role` provides a policy-enforced Dispatch path that pins
the immutable Run role binding to an exact ExecutionTarget without making Aichestra
a second orchestrator. Remaining live gaps: coordinator adoption of that
entrypoint, and durable Orca `launch.effective` receipts for post-dispatch
audit. Do not mark this feature fully implemented until those are proven live.
Fake CI providers are not live evidence.

## Run contract enforcement corrections

- Persist the typed contract by canonical run_id in the config home before
  coordinator handoff. Refuse replacement by later settings, missing contracts,
  cross-project usage, unavailable targets and mismatched Task role/Run identity.
- `dispatch-role --reason quota-fallback` selects only the saved auto-mode
  implement fallback, after a completed canonical primary implement quota receipt
  in the same Run is verified. No Aichestra task scheduling is introduced.
- Installed user config homes are trusted with package defaults, including when
  passed explicitly as --repo-root. Wheel smoke exercises all three command
  config boundaries, with no checkout imports or AICHESTRA_REPO_ROOT.

- Resume MUST only load an existing contract, never create one. Missing or
  changed policy fails closed; restore original settings or start a new Run.
- Saved worker bindings participate as transient compatibility requirements,
  before mutable role declarations, without overriding explicit restrictions.
- Bootstrap resources remain Aichestra's cleanup responsibility until handed
  to the Orca adapter. Persistence failures MUST release owned terminals.
- Endpoint identity includes query/tenant via a fingerprint of the full
  normalized URL. Sanitized coordinator URLs are not identity evidence.
- Manual quota changes apply to a new Run with the remaining objective/context;
  changing settings MUST NOT mutate the old Run's role contract.
