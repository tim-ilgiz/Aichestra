# Aichestra

Portable, project-agnostic AI development orchestration environment centered on
Orca, with Codex as preferred lead, Cursor as fallback lead, and an optional
OpenCode + Ollama local worker.

## Install

From this repository (runtime only):

```bash
python3 -m pip install -e .
```

For contributors who also run tests:

```bash
python3 -m pip install -e ".[dev]"
pytest
```

Or run without install by setting `PYTHONPATH=src`.

After install, `aichestra …` and `python -m aichestra …` are equivalent.

## Config locations

| Mode | Machine-local settings |
|------|------------------------|
| Contributor clone (cwd is this repo) | `.local/machine.local.json` (gitignored) |
| Installed package / foreign project | Portable user config home (see below) |

User config home (override with `AICHESTRA_CONFIG_HOME`):

- macOS: `~/Library/Application Support/aichestra/`
- Linux: `$XDG_CONFIG_HOME/aichestra/` or `~/.config/aichestra/`
- Windows: `%APPDATA%\aichestra\`

`local.enabled` defaults to `false` and remains valid on any hardware.
Large local model downloads require explicit approval
(`--approve-model-download` records approval; it does not download weights).

Target-project settings live in `.aichestra/project.json` (via `aichestra init`).

## Bootstrap (idempotent config / doctor)

Pick the entrypoint for your OS (thin wrappers around the shared Python core):

- macOS: `bootstrap/macos/setup.sh`
- Linux: `bootstrap/linux/setup.sh`
- Windows (native PowerShell): `bootstrap/windows/setup.ps1`

Equivalent Python:

```bash
python -m aichestra bootstrap
python -m aichestra update
```

Bootstrap writes/merges config, runs discovery + doctor, and can set
`bootstrap_complete=true` when doctor is ok and no blocking remaining steps
remain. It does **not** install Orca/Codex/Cursor/OpenCode/Ollama — install
those tools separately; doctor reports what is missing.

## Daily commands

```bash
# Platform
python -m aichestra doctor            # PASS/WARN/FAIL health check
python -m aichestra profile           # deterministic machine profile (JSON)
python -m aichestra bootstrap         # idempotent setup
python -m aichestra update            # after git pull; preserves machine-local

# Target project
aichestra init --yes                  # create .aichestra/project.json
aichestra settings                    # interactive role/model/quota editor
aichestra settings show               # print roles / quota / verification
aichestra settings set KEY=VALUE …    # mutate project.json (see below)

# Mode C / research / handoff (require Orca where noted)
python -m aichestra orchestrate --prompt "…" --project-root /path/to/target
python -m aichestra handoff --prompt "…" --run-id <run_id>
python -m aichestra research /path/to/target-project --query "auth"
python -m aichestra prove-launch --project-root /path/to/target --candidate-id <id>
python -m aichestra abort-launch --token <cleanup_lease>
python -m aichestra dispatch-role --role tests --run <run_id> --task <task_id> \
  --project-root /path/to/target

# Staging diagnostics (typed, allowlist-gated)
python -m aichestra staging STAGE_ALIAS --op uptime --dry-run
python -m aichestra staging STAGE_ALIAS --op disk_usage
```

### Target project init + role settings

From any target project:

```bash
cd /path/to/your-project
aichestra init --yes
aichestra init --yes --force                          # overwrite existing project.json
aichestra init --yes --set roles.implement=cursor     # init + one-shot sets
aichestra settings show
```

All `aichestra settings set` keys:

```bash
# Coordinator LLM (Mode C bootstrap) — independent of coding workers
aichestra settings set orchestration.coordinator=cursor

# Worker roles (implement | research | tests | docs)
aichestra settings set roles.implement=codex
aichestra settings set roles.research=cursor
aichestra settings set roles.tests=cursor
aichestra settings set roles.docs=cursor
aichestra settings set roles.tests.runtime=opencode
aichestra settings set roles.tests.model=qwen2.5-coder:14b
aichestra settings set roles.tests.provider=ollama
aichestra settings set 'roles.tests={"runtime":"opencode","model":"qwen2.5-coder:14b"}'

# Quota (manual | auto) — coding-worker fallback only
aichestra settings set quota.mode=manual
aichestra settings set quota.mode=auto
aichestra settings set quota.roles.implement=cursor
aichestra settings set quota.implement_fallback=cursor   # alias for quota.roles.implement

# Verification gate (JSON boolean only; fail-closed when disabled)
aichestra settings set verification.enabled=false
aichestra settings set verification.enabled=true
aichestra settings set verify='["pytest","-q"]'
aichestra settings set verify='[["npm","test"],["npm","run","lint"]]'
aichestra settings set verify=null
```

Works in **any** target project directory (not tied to a specific app). Role
values are AgentRuntime ids (`codex`, `cursor`, `gemini`, `claude`, `opencode`)
with optional `provider` / `model` (OpenCode + local/Ollama). Aliases `local` /
`local-worker` map to `opencode`. `roles.*` are worker roles only.
`orchestration.coordinator` is the Mode C coordinator LLM and is independent
of `roles.implement`. Config lives in `.aichestra/project.json`.
`verify` is separate from the toggle. Init sets `verification.enabled=false`;
until enabled with a real JSON boolean `true` and non-empty verify commands,
orchestration stops at preflight before creating a Run. The verification gate
also remains fail-closed.

Mode C resolves the coordinator and each worker role into exact supported
ExecutionTargets before creating a Run. Bootstrap uses
`orchestration.coordinator`, never `roles.implement`. `execution.runtimes`
can register additional runtime ids (including from machine/global layered
config — bindings are stored in the project; runtimes need not be copied into
`project.json`).
Disabled or unresolved bindings fail with a settings error. Orca owns task
creation; Aichestra emits a typed `role_dispatch_contract`
(`role → execution_target_id`, with `requires_launch_proof` for provisionable
candidates). Prefer `aichestra dispatch-role --run --task --role` to
worker-start the exact bound target. Before handoff, Aichestra persists an
immutable contract by Run id in the config home. Later settings changes do not
retarget that Run; unavailable or disabled targets fail closed. Missing contracts
require a new Run, including with `--resume-run-id`: resume never creates a
contract and rejects changed policy. Restore the original settings to resume,
or start a new Run to apply new settings. Dispatch of an existing Task resolves
the saved runtime/provider/model independently of current role settings, while
current availability and explicit compatibility restrictions remain authoritative.
Endpoint fingerprints distinguish tenant/query differences without storing the
full URL in the coordinator contract. Task role and same-Run association are
checked before launch. Canonical task/worker receipts are audited for role, Run and
effective runtime/provider/model/endpoint. Missing or mismatched evidence
fails the Run. An Orca version that omits durable `launch.effective` cannot
fully close live post-dispatch audit. Full live role/quota acceptance remains
partial until coordinators adopt dispatch-role and receipts are proven live.

`quota.mode=manual` stops and asks the operator to change settings and start a
new Run with the remaining objective/context; it cannot retarget the old Run.
`quota.roles.implement` is the coding-worker fallback (precedence:
`quota.roles.implement` → `quota.implement_fallback` → default). Auto mode does
**not** replace the coordinator LLM; coordinator quota is a separate failure
(`orchestration.coordinator`). Inner-task implement fallback must have a prior
structured primary-worker quota receipt. Use `dispatch-role --role implement
--reason quota-fallback --run <run_id> --task <task_id> --project-root <root>`;
this selects the Run contract fallback only in auto mode and checks canonical
quota evidence before starting a worker. Evidence must be typed
(`failure=quota`, durable `lastFailure.failure=quota`, or the settling
`worker_done` message `payload.failure=quota` — Orca 1.4.200 stores the class
on the message even when `lastFailure` omits it). Body/subject prose is not
enough. Live quota recovery via `dispatch-role --reason quota-fallback` was
validated on Orca 1.4.200 (run `run_b3f15bcba245`).

From a project or subdirectory, use `aichestra orchestrate --prompt "..."`.
The nearest ancestor `.aichestra/project.json` selects the project root, with
cwd as the fallback; `--project-root` overrides this. `aichestra init` opens
the settings editor on a terminal; `--yes` accepts defaults for automation.
The editor includes Coordinator separately from Coding.

### Modes in practice

1. **Native** — run `codex` / Cursor IDE / `cursor` as usual (unchanged).
2. **Orca interactive** — open Orca app; work with one agent (`orca open` / UI).
3. **Mode C** — start explicitly from a live Orca terminal (requires Orca,
   its runtime-issued `ORCA_TERMINAL_HANDLE`, **and** `--project-root`):
   `python -m aichestra orchestrate --prompt "…" --project-root /path/to/target`
   Creates **one Orca Run**; agent work goes through Orca tasks/workers/worktrees;
   Aichestra applies policy + deterministic gates on the adopted worktree.
   No direct Codex/Cursor fallback in Mode C (use Mode A for native tools).
   Optional provider toggles: `--no-codex`, `--no-cursor`, `--no-local`
   (and `--no-orca`, which fails Mode C closed).
   Attachments: `--attach screenshot.png` forwards native file bytes when the
   installed Orca `worker-start` still advertises `--attach` (staged outside the
   parent checkout). If Orca lacks that primitive, Mode C fails closed honestly
   rather than metadata-only path lists. Codex Mode A uses `--image` / `-i`.
4. **Codex→Cursor handoff** — one-action inside the same Orca Run:
   `python -m aichestra handoff --prompt "…" --run-id <run_id>`
   (or `--repo /path/to/project` to auto-resolve a recent Mode C run from
   `.aichestra/last_mode_c_run.json`). Execute binds `task-create --run <id>`
   after successful `run-use`. Use `--prepare-only` for packet-only output.
   Without `--run-id` / resolvable recent run, execute fails closed.
5. **Disable local inference** — keep `local.enabled: false` in
   machine-local config (default).
6. **Local repository research** —
   `python -m aichestra research /path/to/target-project --query "auth"`
7. **Machine/local AI** — `python -m aichestra profile` and doctor local-AI section.
8. **Staging diagnostics** (typed, allowlist-gated; alias in machine-local config):

   ```bash
   python -m aichestra staging STAGE_ALIAS --op uptime --dry-run
   python -m aichestra staging STAGE_ALIAS --op disk_usage
   ```

   Unknown aliases fail closed. Production SSH is not supported.
9. **Worktree / diff** — prefer Orca:
   `orca worktree list`, `orca worktree show`, and the Orca UI diff for the
   active worktree (do not `git reset --hard` the user's real checkout).
10. **Update** — `git pull` then `python -m aichestra update` (clone) or
    reinstall / `pip install -e .` after pulling (contributor).

Orca CLI may live on PATH or at the macOS app bundle
`/Applications/Orca.app/Contents/Resources/bin/orca` (discovered automatically).

Convenience scripts:

```bash
python scripts/doctor.py
python scripts/machine_profile.py
python scripts/smoke_mac.py         # Mac-oriented live smoke report
```

## Three modes

| Mode | Name | Behavior |
|------|------|----------|
| A | `native` | Use Codex/Cursor directly. Aichestra does **not** intercept native CLIs. |
| B | `orca_interactive` | Single-agent Orca session; no automatic full multi-role orchestration. |
| C | `orchestrated` | One Orca Run + Aichestra policy/gates (agent steps via Orca; local maintenance-reviewer + verification on adopted worktree). |

Orca is opt-in and **required for Mode C**. Codex is the preferred lead; Cursor is the fallback.
Quota behavior uses project `quota.mode` and its exact configured fallback.
Legacy handoff helpers retain their manual semantics; Mode C recovery is owned
by the Run controller and executed only through Orca.

## Staging diagnostics

Only **STAGING** SSH diagnostics are integrated. Dangerous commands
(`sudo`, `rm`, restart/stop, `docker rm/stop`, `kubectl apply/delete`, …)
are rejected **before** execution. Production SSH has **no** integration path.

## Validation status (truthful)

| Surface | Status |
|---------|--------|
| Automated CI (macOS / Windows / Linux matrix) | Validates core code with **fake providers** (no real Codex/Cursor quota) |
| macOS live smoke | May be live-validated separately via `scripts/smoke_mac.py` for components actually present |
| Windows live smoke | **NOT VALIDATED** until a real Windows smoke run |
| Linux live smoke | **NOT VALIDATED** until a real Linux smoke run |
| Live auto quota fallback via `dispatch-role` | **VALIDATED** on Orca 1.4.200 via durable `worker_done` `payload.failure=quota` (2026-09-11) |

Do not treat CI green as live OS smoke for Windows/Linux.

## Architecture (high level)

- **Orca** — opt-in UI / **orchestration engine** for Mode C (one Run per task)
- **Aichestra** — policy, bootstrap, discovery, deterministic gates (not a second orchestrator)
- **Codex** — preferred lead (via Orca in Mode C; native in Mode A)
- **Cursor** — fallback lead
- **OpenCode + Ollama** — optional `local-worker` (via Orca in Mode C)
- **maintenance-reviewer** — structured TEST/DOC/ADR/SPEC gate (may choose none/none)
- **verification-runner** — real subprocess commands; exit codes are authoritative
- Native Codex/Cursor usage remains independent of Orca

## Spec Kit

- Spec: [`specs/001-portable-ai-orchestration/spec.md`](specs/001-portable-ai-orchestration/spec.md)
- Plan: [`specs/001-portable-ai-orchestration/plan.md`](specs/001-portable-ai-orchestration/plan.md)
- Tasks: [`specs/001-portable-ai-orchestration/tasks.md`](specs/001-portable-ai-orchestration/tasks.md)
- Project init / role routing: [`specs/002-project-init-role-routing/spec.md`](specs/002-project-init-role-routing/spec.md)
- Constitution: [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
- Agent contract: [`AGENTS.md`](AGENTS.md)

## Tests

```bash
python3 -m pip install -e ".[dev]"
pytest
```

Fixture projects under `fixtures/project_a` (Python) and `fixtures/project_b`
(Node) prove isolation. CI sets `AICHESTRA_FAKE_PROVIDERS=1` and
`AICHESTRA_NO_REAL_QUOTA=1`.

Mode C bootstraps one explicit coordinator from `orchestration.coordinator`
(independent of `roles.implement`) using one runnable ExecutionTarget. Legacy
provider fields do not select the coordinator. The coordinator reads project
context, including scoped nested AGENTS.md, owns which Tasks to create and
when, and must Dispatch each worker role through
`role_dispatch_contract` exact `execution_target_id`s (prefer
`aichestra dispatch-role`). It waits for outcomes
and converges results before completing. At the implementation boundary it calls
`orchestration ask --question AICHESTRA_GATE:maintenance`; Aichestra replies with
TEST/DOC/ADR/SPEC decisions before Orca dispatches required writers. The
coordinator releases settled child workers; Aichestra releases the coordinator
and checks reclaimable resources. Success requires this handshake, explicit
successful completion, passing verification, and a confirmed canonical Run with
all Tasks completed. Missing or failed state is a non-success result.

The production resolver probes the installed Orca `agent-context --json` launch
contract. Native adapters support runtime-managed inference for Codex, Cursor,
Claude Code and Gemini CLI; explicit `execution.bindings` may select supported
runtimes. Only runnable targets enter the coordinator package. Every native
coordinator launch must return the matching `launch.effective.agent`; missing
or mismatched binding receipts fail closed. Runtime-specific launch adapters
can be registered without adding workflow product branches.

Explicit provider/model/endpoint combinations become dispatchable through a
launch adapter. Native ``--agent`` alone does not prove a backend binding;
native ``--model`` covers opaque runtime model preferences when Orca returns
matching ``launch.effective.model``. Backend bindings use
``orca-terminal-bridge`` (create → wait → **live** ``terminal read`` process
proof → ``worker-start --terminal``) or an attested ``orca-existing-terminal``
handle (``ORCA_WORKER_TERMINAL_HANDLE`` / context). Schema support for a bridge
marks a target **provisionable**, not **runnable**; ``runnable`` requires a
proven process/contract binding. Create-receipt ``startupCommand`` echoes are
not proof. Prompt text, discovery alone, and Aichestra-process environment
variables are not proof. Bridge-owned terminals are closed on prepare/dispatch
failure before attach; foreign existing terminals are never closed. Abort-launch
closes a bridge only after claiming an Aichestra-issued opaque cleanup lease
and proving via Orca ``worker-list`` that the stored handle is not bound to a
Dispatch — callers cannot pass a raw terminal handle. A structured Dispatch
binding consumes the lease and leaves the terminal running. Failed close
restores the lease. After an exact ``dispatch_id`` exists, failure paths
attempt bounded worker-release
cleanup. Local-only live acceptance remains NOT VALIDATED (no proven local
ExecutionTarget on the current machine). Cloud/native Mode C live smoke passed via T168 (`run_3d12b2f31039`) for the
pre-T180 handshake. In-Run `AICHESTRA_GATE:verification` live smoke passed via
T180 (`run_27377a46c199`). Fresh GitHub CI (T167) accepted on final
implementation HEAD `8c159f85` (GitHub Actions run #78: Ubuntu + macOS +
Windows).

Worker release distinguishes `released`, `already_released`, `release_pending`
and `release_unknown`. Recovery executes only exact-dispatch allowlisted
commands from Orca's receipt, bounded to three recovery attempts, and inspects
the exact worker again. Automatic recovery can settle a pending release without
a retry. Unresolved Run resources prevent success; broad never close is
never used. Failed starts release an exact ``dispatch_id`` when present, or
close only a bridge-owned terminal handle created by this prepare() when
worker-start never produced a dispatch.

An ordinary headless shell without Orca terminal authority is unsupported and
fails before Run creation. Do not invent or reuse a stale terminal handle.
Adapter contract tests validate launch construction and receipts, not a live DAG.
See the active tasks' T167 entry for CI matrix close-out evidence (run #78 on
`8c159f85`) and T168 for live Mode C acceptance evidence.
