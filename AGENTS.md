# Aichestra — Agent Instructions

## Source of truth

Read in this order:

1. `AGENTS.md`
2. current Spec Kit constitution (`.specify/memory/constitution.md`)
3. active feature spec
4. active feature plan
5. active feature tasks
6. existing implementation and tests

The specification describes **intended** behavior.
The codebase describes **current** implementation.

When they disagree, do not weaken the spec to match the code.
Record the gap and fix the code (or reopen tasks).

## Mission

Build a portable, project-agnostic AI development orchestration environment
based primarily on Orca.

The platform must support:

- macOS
- Windows
- Linux

## Core architecture

### Three modes

- **Mode A — Native**: direct Codex / Cursor IDE / `cursor-agent`. Aichestra
  and Orca MUST NOT intercept these invocations.
- **Mode B — Orca Interactive**: user works in Orca UI/CLI manually; Aichestra
  does not auto-start full Mode C.
- **Mode C — Aichestra Orchestrated**: always via Orca. One Mode C invocation
  creates or resumes **exactly one Orca Run**. All agent/worker/worktree work
  belongs to that Run.

### Roles

- Orca = UI and **only** Mode C orchestration control plane
- Codex = preferred lead (policy for Orca)
- Cursor = fallback lead (policy for Orca)
- OpenCode + Ollama = optional local worker (via Orca in Mode C)
- Aichestra = bootstrap, discovery, policy, config, deterministic gates,
  verification, Orca adapter
- Aichestra is **not** a second general-purpose workflow engine, worker
  scheduler, session manager, or worktree manager
- maintenance-reviewer gates tests and documentation (deterministic)
- GitHub Spec Kit is proportional policy input to Orca — not a Python
  orchestrator
- target-project AI/Factory tooling must not be broken

### Mode C invariants (MUST)

- Orca required; if Orca unavailable → Mode C FAIL (use Mode A for direct tools)
- Resolved target project root required
- Exactly one Orca `run-create` / one `run_id` per Mode C invocation
- No direct Codex/Cursor/local-worker execution from Aichestra for Mode C
  implement/research/writers/review
- Attachments forwarded through real Orca/provider mechanisms when claimed

## Native mode

Direct use of Codex and Cursor must remain possible.

Do not globally replace or intercept their normal commands.

Orca orchestration (Mode C) is opt-in.

## Portability

Never hard-code:

- a username
- `/Users/...`
- `C:\...`
- one shell
- one package manager
- one path separator

Prefer cross-platform Python for portable helper logic.

Windows native operation is first-class.
WSL is optional and must not be required.

Linux native operation is first-class.

Machine profiling is capability-based (OS/CPU/RAM/disk/GPU/runtimes/models).
Do not bind production architecture to a specific Mac identity.

## Graceful degradation

Codex, Cursor, and the local worker are optional providers.

Precise meaning:

- Codex missing → Orca may use Cursor
- Cursor missing → Orca may use Codex
- local-worker missing → cloud-oriented Mode C (still requires Orca)
- both Codex and Cursor unavailable → fail or degraded Orca flow per remaining
  configured workers
- **Orca missing → Mode C FAIL; Mode A remains usable**

Do **not** interpret missing Orca as permission for Aichestra to become the
orchestrator or to call lead adapters directly.

Providers MUST be independently enable/disable-able without breaking the
platform.

## Security

Never commit:

- tokens
- passwords
- private SSH keys
- `.env` secrets
- model weights
- machine-local configuration
- runtime sessions
- large logs

Only STAGING SSH diagnostics are supported.

No production SSH integration.

Stage access must be read-only by structural enforcement, not merely prompt
instructions.

## Testing philosophy

Do not maximize test count.

Use the minimum useful regression safety net.

Prefer:

- business-invariant unit tests
- integration/behavior tests
- architecture contract tests (Mode C / Orca invariants)
- security/money/concurrency/idempotency coverage

Avoid:

- trivial getter/setter tests
- duplicate arbitrary-value cases
- mock-call-count-only tests
- tests coupled to harmless internal refactoring
- tests that lock **obsolete** dual-orchestrator behavior

## Documentation philosophy

Do not create documentation merely because code changed.

Prefer updating an existing canonical document.

Each durable fact should have one canonical home.

Git contains implementation history.
Documentation describes the current system.

Do not create permanent `implementation-summary.md`, `final-report.md` or similar
generated reports unless explicitly required.

## Spec Kit policy

SMALL:
direct implementation → maintenance review → verification

MEDIUM:
short brief → research → plan → implementation → maintenance review

LARGE/HIGH-RISK:
Spec Kit may use full specify/clarify/plan/tasks workflow

Do not force every tiny change through full SDD.

Do not build a Python workflow engine “because Spec Kit.”

## Execution

When implementing:

inspect → implement → run → test → debug → retest

Do not stop after scaffolding or documentation.

Ask the user only for genuinely external blockers such as:

- interactive authentication
- OS security approval
- unknown staging SSH alias
- irreversible external action

## Verification

Never claim a platform was live-tested when it was not.

Current development may validate macOS live.

Windows/Linux behavior may initially be proven through cross-platform code,
tests and CI until real-machine smoke tests are executed.

Deterministic verification (build/tests/lint/configured commands) stays in
Aichestra; non-zero exit codes cannot be overridden by LLM opinion. Mode C
verification results feed the same Orca Run for final review.
