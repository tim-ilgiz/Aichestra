# Implementation Plan: Project init + role routing

**Branch**: `002-project-init-role-routing` | **Date**: 2026-09-10

## Summary

Portable user config home + package defaults; `init`/`settings` CLI; normalize
role bindings into exact execution targets; audit canonical Orca receipts;
apply configured manual/auto quota behavior in one Run; preflight verification;
validate the installed wheel outside the checkout.

## Technical approach

1. **Config homes** — `aichestra.config.paths.user_config_home()` portable;
   `resolve_aichestra_config_root` prefers explicit/`AICHESTRA_REPO_ROOT`/clone,
   else user config home. Defaults from `importlib.resources` package data.
2. **Roles module** — normalize/validate bindings; known runtimes include
   `codex`, `cursor`, `gemini`, `claude`, `opencode` (aliases `local`,
   `local-worker` → `opencode`), extended by `execution.runtimes` registrations.
3. **CLI** — interactive `init`/`settings`, plus noninteractive `init --yes`,
   `settings show`, `settings set KEY=VALUE`; infer project root from ancestors.
4. **Mode C** — resolve exact targets before bootstrap; canonical receipt audit
   enforces role bindings; bounded quota retry through Orca; disabled bindings
   always fail closed.
5. **Timeouts** — raise Mode C handoff wait; cap verification command timeout
   so gate reply fits ask window.

## Constitution check

I–VIII: portable paths; Orca-only Mode C; explicit policy not dual orchestrator;
no secrets in project.json; verify don't claim live OS smoke.


## PR #2 implementation correction / architecture decision

**Decision:** retain the Orca-owned DAG and add deterministic exact-role
resolution plus a canonical receipt audit at Run finalization. The audit reads
all tasks and workers in the Run, then each worker-show receipt. It rejects
unknown roles, cross-Run associations, missing evidence and effective target
mismatches. This is post-dispatch verification, so it detects violations after
work may have occurred; it does not claim an Orca pre-dispatch sandbox.
Orca installations without the required canonical receipt fields fail closed.

Role compatibility derives from roles as well as execution bindings; existing
compatibility restrictions remain authoritative. CLI and controller resolve
roles before creating a Run. The implement target alone selects bootstrap.
A quota result permits one configured coordinator replacement in the same Run;
fresh launch proof replaces the exhausted runtime's handles. Inner tasks remain
Orca-owned and fallback receipts must prove a prior quota outcome. Repeated
quota fails without an unbounded retry loop.

The console editor uses runtime/model discovery and saves only on explicit
Save. Project discovery searches ancestors; verification readiness is checked
before execution discovery. CI builds and installs a wheel outside the checkout.
Update existing canonical tests/README, with focused role receipt and quota
behavior tests rather than source-string assertions.
