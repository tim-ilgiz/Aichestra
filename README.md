# Aichestra

Portable, project-agnostic AI development orchestration environment centered on
Orca, with Codex as preferred lead, Cursor as fallback lead, and an optional
OpenCode + Ollama local worker.

## Install

```bash
python3 -m pip install -e ".[dev]"
```

Or run without install by setting `PYTHONPATH=src`.

## Bootstrap (idempotent)

Pick the entrypoint for your OS (thin wrappers around the shared Python core):

- macOS: `bootstrap/macos/setup.sh`
- Linux: `bootstrap/linux/setup.sh`
- Windows (native PowerShell): `bootstrap/windows/setup.ps1`

Equivalent Python:

```bash
python -m aichestra bootstrap
python -m aichestra update
```

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
3. **Mode C** — start explicitly (requires Orca **and** `--project-root`):
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
