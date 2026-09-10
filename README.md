# Aichestra

Portable, project-agnostic AI development orchestration environment centered on
Orca, with Codex as preferred lead, Cursor as fallback lead, and an optional
OpenCode + Ollama local worker.

## Install

```bash
python3 -m pip install -e ".[dev]"
```

Or run without install by setting `PYTHONPATH=src`.

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

Machine-local settings are written to `.local/machine.local.json` (gitignored).
`local.enabled` defaults to `false` and remains valid on any hardware.

Large local model downloads require explicit approval
(`--approve-model-download` records approval; it does not download weights).

## Daily commands

```bash
python -m aichestra doctor          # PASS/WARN/FAIL health check
python -m aichestra profile         # deterministic machine profile (JSON)
python -m aichestra bootstrap       # idempotent setup
python -m aichestra update          # after git pull; preserves machine-local
```

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
   Attachments: `--attach screenshot.png` delivers native file bytes
   (Orca `--attach` / staged inbox; Codex `--image` in Mode A).
4. **Codex→Cursor handoff** — one-action inside the same Orca Run:
   `python -m aichestra handoff --prompt "…" --run-id <run_id>`
   (or `--repo /path/to/project` to auto-resolve a recent Mode C run from
   `.aichestra/last_mode_c_run.json`). Execute binds `task-create --run <id>`
   after successful `run-use`. Use `--prepare-only` for packet-only output.
   Without `--run-id` / resolvable recent run, execute fails closed.
5. **Disable local inference** — keep `local.enabled: false` in
   `.local/machine.local.json` (default).
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
10. **Update** — `git pull` then `python -m aichestra update`.

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
Quota handoff Codex→Cursor is **manual one-action** in v1
(`automatic_quota_fallback_reliable = false`).

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
- Constitution: [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
- Agent contract: [`AGENTS.md`](AGENTS.md)

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Fixture projects under `fixtures/project_a` (Python) and `fixtures/project_b`
(Node) prove isolation. CI sets `AICHESTRA_FAKE_PROVIDERS=1` and
`AICHESTRA_NO_REAL_QUOTA=1`.

Mode C bootstraps one explicit coordinator in the current Orca checkout using
one runnable ExecutionTarget. Legacy provider fields do not select the coordinator. The coordinator reads project context,
including scoped nested AGENTS.md, owns child Tasks/Dispatches and worktree
placement in the same Run, waits for their outcomes and converges their results
before completing. At the implementation boundary it calls
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
closes a bridge only after consuming an Aichestra-issued opaque cleanup lease —
callers cannot pass a raw terminal handle. After an
exact ``dispatch_id`` exists, failure paths attempt bounded worker-release
cleanup. Local-only live acceptance remains NOT VALIDATED (no proven local
ExecutionTarget on the current machine). Cloud/native Mode C live smoke passed via T168 (`run_3d12b2f31039`) for the
pre-T180 handshake. In-Run `AICHESTRA_GATE:verification` live smoke passed via
T180 (`run_27377a46c199`). Fresh GitHub CI on current HEAD (T167) is required
before merge.

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
See the active tasks' T167 entry for fresh CI matrix evidence and T168 for live
Mode C acceptance evidence.
