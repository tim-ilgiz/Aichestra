# Implementation Plan: Project init + role routing

**Branch**: `002-project-init-role-routing` | **Date**: 2026-09-10

## Summary

Portable user config home + package defaults; `init`/`settings` CLI; split
coordinator LLM from worker roles; normalize worker bindings into a typed
role-dispatch contract; audit canonical Orca receipts after the fact;
apply configured manual/auto quota to **implement workers only**; preflight
verification; validate the installed wheel outside the checkout.

Live Orca exact Dispatch pinning is out of Aichestra's control and remains
blocked. This plan does not mark the feature fully implemented.

## Technical approach

1. **Config homes** — `aichestra.config.paths.user_config_home()` portable;
   `resolve_aichestra_config_root` prefers explicit/`AICHESTRA_REPO_ROOT`/clone,
   else user config home. Defaults from `importlib.resources` package data.
2. **Roles module** — worker keys are `implement`, `research`, `tests`,
   `docs` only. Coordinator is `orchestration.coordinator`. Quota fallback is
   `quota.roles.implement` (legacy `quota.implement_fallback` on load).
3. **CLI** — interactive `init`/`settings` includes Coordinator vs Coding;
   noninteractive `settings set orchestration.coordinator=...` and
   `roles.implement=...`; infer project root from ancestors.
4. **Mode C** — bootstrap from the coordinator target; worker
   `role_dispatch_contract` maps each role to one `execution_target_id`;
   receipt audit enforces worker bindings after Dispatch; coordinator quota
   does not consume implement fallback; disabled bindings always fail closed.
5. **Timeouts** — raise Mode C handoff wait; cap verification command timeout
   so gate reply fits ask window.

## Constitution check

I–VIII: portable paths; Orca-only Mode C; explicit policy not dual orchestrator;
no secrets in project.json; verify don't claim live OS smoke or live Orca
Dispatch pinning that the installed Orca cannot provide.

## PR #2 implementation correction / architecture decision

**Decision 1 — coordinator ≠ implement.** `roles.*` are worker roles only.
The Mode C bootstrap LLM is `orchestration.coordinator`. The implement
target never selects bootstrap. Default coordinator may still be Codex
(constitution preferred lead) while a user sets `roles.implement=codex`
to mean **coding workers only**.

**Decision 2 — typed dispatch contract, DAG stays in Orca.** Aichestra
does not schedule Tasks. Coordinator under Orca still decides which Tasks
to create and when. Aichestra emits:

```text
role=tests → execution_target_id=X → Dispatch X
  (runnable) or prove-launch then Dispatch (provisionable)
```

Preferred enforcer: `aichestra dispatch-role --run --task --role` resolves
the immutable Run contract and worker-starts only that exact target. That mapping
also lives in POLICY_PACKAGE `role_dispatch_contract`. Durable
`launch.effective` receipts remain required for post-dispatch audit; missing
evidence fails closed. Fake CI is not live proof.

**Decision 3 — quota targets coding workers.** `quota.mode=auto` binds
`quota.roles.implement` as the exact fallback ExecutionTarget for
**implement** work in the same Run. It MUST NOT relaunch the coordinator
on a different LLM. Coordinator quota is operator-facing
(`orchestration.coordinator`). Inner implement fallback receipts still
require a prior structured implement quota outcome.

Role compatibility derives from coordinator, worker roles, and quota
fallbacks as well as execution bindings; existing compatibility
restrictions remain authoritative. CLI and controller resolve coordinator
and worker roles before creating a Run. The console editor uses
runtime/model discovery and saves only on explicit Save. Project discovery
searches ancestors; verification readiness is checked before execution
discovery. CI builds and installs a wheel outside the checkout. Update
existing canonical tests/README, with focused coordinator-vs-implement
and role-receipt tests rather than source-string assertions.

Persist the role contract in the resolved config home before handoff. Dispatch
reads that contract, validates canonical Task membership, and resolves only its
exact target against currently enabled discovery. Quota fallback is an explicit
reason guarded by primary-worker quota evidence; Orca still owns scheduling.
