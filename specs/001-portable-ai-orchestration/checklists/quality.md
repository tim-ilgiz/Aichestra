# Quality Checklist: Portable Aichestra Platform

**Purpose**: Requirements-quality review before implementation
**Created**: 2026-09-09
**Feature**: [spec.md](../spec.md) · [plan.md](../plan.md)

## Cross-platform portability

- [x] macOS, Windows, and Linux are first-class native targets
- [x] Arbitrary clone path is required and testable
- [x] No fixed developer home paths in tracked production config
- [x] Portable helpers are cross-platform Python; OS entrypoints are thin
- [x] WSL is explicitly non-required

## Security boundary

- [x] Staging-only SSH diagnostics
- [x] No production SSH integration path
- [x] Read-only allowlist enforced outside LLM prompts
- [x] Dangerous commands rejected before remote execution
- [x] Diagnostic sanitization before cloud handoff
- [x] Secrets/models/sessions/machine-local config stay untracked

## Provider graceful degradation

- [x] Codex preferred, Cursor fallback, local worker optional
- [x] Useful operation with missing local worker / Codex / Cursor
- [x] Native Codex/Cursor usage remains independent of Orca
- [x] Quota fallback policy is explicit (auto only if reliable; else manual)

## Documentation/test anti-bloat

- [x] maintenance-reviewer can decide no tests / no docs
- [x] Writers prefer updating existing canonical artifacts
- [x] Spec Kit usage is proportional to risk
- [x] No permanent generated summary/report docs required by this feature

## Notes

- Reviewer-owned checklist: items marked complete reflect specification and plan
  quality, not implementation completion.
