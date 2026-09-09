<!--
Sync Impact Report
- Version change: (none) → 1.0.0
- Modified principles: template placeholders → I–VIII project principles
- Added sections: Operating Constraints; Development Workflow
- Removed sections: none
- Follow-up TODOs: none
-->

# Aichestra Constitution

## Core Principles

### I. PORTABILITY FIRST

Every tracked implementation MUST support macOS, Windows, and Linux unless
explicitly isolated as platform-specific. No machine-specific path or
environment assumption may leak into portable configuration. Prefer
cross-platform Python for shared helper logic. Windows and Linux native
operation are first-class; WSL MUST NOT be required.

### II. GRACEFUL DEGRADATION

No optional AI provider MAY become a hard dependency. Useful workflows MUST
remain available when Codex, Cursor, or local inference is unavailable.
Provider discovery and adapters MUST degrade gracefully rather than fail the
platform bootstrap.

### III. MINIMUM MAINTENANCE COST

Tests and documentation are created according to regression and domain value,
not quantity. The maintenance-reviewer gates both. Prefer updating existing
canonical tests and documents before creating new files. Do not create
permanent implementation-summary or generated report documents unless
explicitly required.

### IV. SECURITY IS STRUCTURAL

Security boundaries such as staging SSH restrictions MUST be enforced using
code, allowlists, permissions, and process boundaries—not only LLM prompts.
Only STAGING SSH diagnostics are permitted. No production SSH integration.
Stage access MUST be read-only by structural enforcement. Secrets, tokens,
private keys, model weights, runtime sessions, and machine-local configuration
MUST NOT be committed.

### V. VERIFY, DON'T CLAIM

Generating files is not evidence that a feature works. Real commands,
deterministic tests, CI, and truthful smoke-test reporting define validation
status. Never claim Windows or Linux live validation from macOS-only
execution. Never claim live testing that was not performed.

### VI. NATIVE TOOLS REMAIN NATIVE

Normal direct Cursor and Codex usage MUST remain independent from Orca
orchestration. Do not globally replace or intercept native commands. Orca
orchestration is opt-in.

### VII. PROJECT AGNOSTICISM

The orchestration platform itself MUST NOT contain application-specific
assumptions (for example KateRentBot-specific paths, workflows, or
configuration). Target-project AI Factory / Factory tooling MUST be preserved
unless migration is explicitly approved. At least two independent fixture
repositories MUST prove project isolation.

### VIII. ONE CANONICAL SOURCE

Durable knowledge SHOULD have one canonical source. Avoid duplicate
specifications, docs, and rules that create synchronization debt. Spec Kit
usage MUST be proportional to task size and risk; do not force every tiny
change through full SDD.

## Operating Constraints

- Orca is the primary interactive UI and orchestration control plane.
- Codex is the preferred lead provider; Cursor is the supported fallback lead.
- OpenCode + Ollama is an optional local worker.
- Repository clone location is arbitrary; no fixed home-directory paths in
  tracked production configuration.
- Bootstrap MUST be idempotent and preserve existing user configuration where
  practical.
- Machine-local model and provider settings remain untracked.
- GitHub Actions MUST validate core code on macOS, Windows, and Linux without
  consuming real Codex/Cursor account quota.

## Development Workflow

1. Inspect the repository and active Spec Kit artifacts before changing code.
2. Implement in dependency order from the active tasks list.
3. Run, test, debug, and retest until acceptance criteria are met or a genuine
   external blocker exists.
4. After production implementation and before generating tests or docs, run the
   maintenance-reviewer gate.
5. Mark any unexecuted validation explicitly as NOT VALIDATED with the concrete
   blocker.

## Governance

This constitution supersedes conflicting informal guidance in prompts or ad-hoc
notes. Amendments require an explicit constitution update with semantic version
bump, dated change notes, and alignment of AGENTS.md / Cursor rules only when
those files would otherwise drift. Spec Kit feature specs define intended
behavior; the codebase defines current implementation. Compliance is reviewed
during Spec Kit analyze/converge and maintenance-reviewer gates.

**Version**: 1.0.0 | **Ratified**: 2026-09-09 | **Last Amended**: 2026-09-09
