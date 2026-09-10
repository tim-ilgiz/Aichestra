# Feature Specification: Project init + role routing

**Feature Branch**: `002-project-init-role-routing`

**Created**: 2026-09-10

**Status**: Implemented; live Orca receipt compatibility requires live validation

**Input**: Global pip-installable Aichestra; `aichestra init` in a target project;
Console settings CLI for role→runtime(+model) bindings; Mode C coordinator
honors bindings; quota manual/auto for implement role.

## Architecture constraints

- Orca remains the only Mode C orchestration control plane.
- Aichestra MUST NOT become a Python phase scheduler or call Codex/Cursor/
  OpenCode `execute_task` for Mode C agent work.
- Explicit project/user role bindings ARE allowed policy (not hard-coded
  product-phase routing in core). Values are AgentRuntime ids plus optional
  provider/model for local/OpenCode targets.

## User stories

### US1 — Global install + project init (P1)

A developer installs Aichestra as a Python package, enters **any** target
project directory, runs `aichestra init`, and gets `.aichestra/project.json`
with default role bindings and verify hints without needing the Aichestra git
clone as cwd.

**Acceptance**

1. `pip install` / editable install exposes `aichestra` CLI.
2. `aichestra init [--yes] [--project-root PATH]` creates `.aichestra/project.json`.
3. Machine-local config resolves to a portable user config home when not in the
   Aichestra clone.
4. Package-bundled defaults load without a clone checkout.

### US2 — Settings CLI for roles and models (P1)

Developer configures which runtime (and optional model/provider) handles
implement, research, tests, and docs — including Codex, Cursor, OpenCode, and
local models.

**Acceptance**

1. `aichestra settings show` prints project role/quota/verify config.
2. `aichestra settings set roles.implement=codex` works.
3. Object form works: `roles.tests={"runtime":"opencode","model":"qwen2.5-coder:14b"}`
   or dotted keys `roles.tests.runtime=opencode` + `roles.tests.model=...`.
4. Unknown runtime ids fail closed; execution.runtimes registrations are valid.
5. `quota.mode=manual|auto` and `quota.implement_fallback` are settable.

### US3 — Mode C honors role bindings (P1)

On `orchestrate`, POLICY_PACKAGE includes `role_bindings` and `quota_policy`.
Coordinator preamble MUST require Dispatch by declared role using the bound
runtime (and model when specified) from runnable `execution_targets`.

**Acceptance**

1. Package contains normalized `role_bindings` from project.json.
2. Preamble states MUST rules for role Dispatch and quota modes.
3. If implement runtime is disabled by CLI (`--no-codex` when bound to codex),
   Mode C fails closed before silent substitution.

### US4 — Quota notify vs auto fallback (P2)

When implement runtime hits quota/rate-limit:

- `manual`: coordinator escalates/asks operator to change settings via CLI.
- `auto`: coordinator same-Run handoff to `implement_fallback` via Orca (no
  direct Aichestra Cursor execute).

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

## PR #2 acceptance corrections

- Resolve every configured role to one exact ExecutionTarget before Run creation;
  role provider/model declarations participate in compatibility resolution.
  Disabled, missing, incompatible or unsupported targets fail closed. Registered
  `execution.runtimes` ids are accepted alongside built-ins.
- Canonical Orca task/worker receipts must prove role, same-Run task association,
  and effective runtime/provider/model/endpoint. Missing evidence or a mismatch
  fails the Run. A prompt or source-text assertion is not proof of dispatch.
- Auto quota recovery retries the coordinator once through Orca with the exact
  configured fallback and the original run_id. No direct provider execution.
  Inner-task fallback receipts require a prior structured quota outcome and
  chronological same-Run evidence; manual mode stops with operator guidance.
- An explicitly disabled implement binding must never be remapped, including
  when its runtime differs from legacy preferred_lead configuration.
- `orchestrate --prompt ...` finds the nearest ancestor `.aichestra/project.json`,
  otherwise uses resolved cwd. `--project-root` remains an explicit override.
- `settings` opens a console menu for Coding, Research, Tests, Documentation,
  Quota fallback and Verification; available choices use discovery. `init`
  without `--yes` opens this editor on a terminal. Noninteractive automation
  retains `init --yes` and `settings show|set`.
- Init keeps verification disabled without inventing project commands. Run
  preflight rejects missing/disabled verification before discovery or paid work.
- Every CI OS builds a wheel and installs it into a clean venv, then exercises
  version/init/settings from an unrelated temporary project without checkout
  imports. Fake-provider tests do not establish live Orca compatibility.
- Machine-local IDE workspace/account state must not be tracked.
