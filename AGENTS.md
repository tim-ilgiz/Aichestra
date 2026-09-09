# Aichestra — Agent Instructions

## Source of truth

Read in this order:

1. `AGENTS.md`
2. current Spec Kit constitution (`.specify/memory/constitution.md`)
3. active feature spec
4. active feature plan
5. active feature tasks
6. existing implementation and tests

The specification describes intended behavior.
The codebase describes current implementation.

## Mission

Build a portable, project-agnostic AI development orchestration environment
based primarily on Orca.

The platform must support:

- macOS
- Windows
- Linux

## Core architecture

- Orca = UI and orchestration control plane
- Codex = preferred lead
- Cursor = fallback lead
- OpenCode + Ollama = optional local worker
- maintenance-reviewer gates tests and documentation
- GitHub Spec Kit is proportional to task size/risk
- target-project AI/Factory tooling must not be broken

## Native mode

Direct use of Codex and Cursor must remain possible.

Do not globally replace or intercept their normal commands.

Orca orchestration is opt-in.

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

## Graceful degradation

Codex, Cursor and the local worker are optional providers.

The platform must retain useful functionality when one or more are absent.

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
- contract tests where important
- security/money/concurrency/idempotency coverage

Avoid:

- trivial getter/setter tests
- duplicate arbitrary-value cases
- mock-call-count-only tests
- tests coupled to harmless internal refactoring

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
