# Implementation Plan: Project init + role routing

**Branch**: `002-project-init-role-routing` | **Date**: 2026-09-10

## Summary

Portable user config home + package defaults; `init`/`settings` CLI; normalize
role bindings (runtime + optional provider/model); ship in Mode C policy
package + coordinator MUST preamble; quota manual/auto text; fix Mode C
monitor/verification timeout mismatch.

## Technical approach

1. **Config homes** — `aichestra.config.paths.user_config_home()` portable;
   `resolve_aichestra_config_root` prefers explicit/`AICHESTRA_REPO_ROOT`/clone,
   else user config home. Defaults from `importlib.resources` package data.
2. **Roles module** — normalize/validate bindings; known runtimes include
   `codex`, `cursor`, `gemini`, `claude`, `opencode` (aliases `local`,
   `local-worker` → `opencode`).
3. **CLI** — `init`, `settings show`, `settings set KEY=VALUE`.
4. **Mode C** — `ModeCPolicyPackage.role_bindings` + `quota_policy`; preamble
   MUST; fail-closed when implement binding runtime disabled.
5. **Timeouts** — raise Mode C handoff wait; cap verification command timeout
   so gate reply fits ask window.

## Constitution check

I–VIII: portable paths; Orca-only Mode C; explicit policy not dual orchestrator;
no secrets in project.json; verify don't claim live OS smoke.
