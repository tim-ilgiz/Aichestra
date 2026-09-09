<!--
Sync Impact Report
- Version change: 1.0.0 → 1.1.0
- Modified principles: II Graceful Degradation (Orca required for Mode C)
- Modified sections: Operating Constraints (single Orca Run; no dual orchestrator)
- Removed sections: none
- Follow-up TODOs: production Mode C realignment (tasks Phase 20 T108+)
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

Codex, Cursor, and local inference MUST remain optional. Useful Mode A native
usage and degraded Mode C (when Orca still has workers) MUST remain available
when those optional providers are missing. Provider discovery and adapters MUST
degrade gracefully rather than fail platform bootstrap.

Orca is required for Mode C. Missing Orca MUST fail Mode C closed and MUST NOT
authorize Aichestra to become a second orchestrator or to invoke Codex/Cursor
directly as a Mode C fallback. Mode A remains independently usable.

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

- Orca is the primary interactive UI and the only Mode C orchestration control
  plane. One Mode C invocation MUST use exactly one Orca Run.
- Aichestra MUST NOT implement a general-purpose workflow engine that duplicates
  Orca worker/task scheduling.
- Codex is the preferred lead provider; Cursor is the supported fallback lead
  (policy inputs to Orca).
- OpenCode + Ollama is an optional local worker (Mode C dispatch via Orca).
- Providers MUST be independently enable/disable-able.
- Repository clone location is arbitrary; no fixed home-directory paths in
  tracked production configuration.
- Bootstrap MUST be idempotent and preserve existing user configuration where
  practical.
- Machine-local model and provider settings remain untracked.
- Machine profiling is capability-based; production MUST NOT depend on a
  specific Mac identity.
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

**Version**: 1.1.0 | **Ratified**: 2026-09-09 | **Last Amended**: 2026-09-09

### Amendment 1.1.0

Clarify graceful degradation: Orca required for Mode C; Mode A remains when
Orca is absent. Forbid dual-orchestrator Mode C; one Orca Run; capability-based
machine profiling; independent provider enable flags.
