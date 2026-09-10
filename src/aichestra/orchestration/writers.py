"""Test/doc writer preference helpers — update existing before creating new."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WriterPlan:
    action: str  # skip | update | create
    targets: tuple[str, ...]
    rationale: str


def plan_test_writes(
    *,
    test_decision: str,
    test_scope: Iterable[str] | None = None,
    existing_test_files: Iterable[str] | Path | None = None,
) -> WriterPlan:
    """Prefer updating/parameterizing existing tests (FR-026)."""
    if test_decision in {"none", "", None}:
        return WriterPlan(
            action="skip",
            targets=(),
            rationale="maintenance-reviewer decided no tests needed",
        )
    existing = _as_paths(existing_test_files)
    scope = [s for s in (test_scope or []) if s]
    if existing and test_decision in {"update_existing", "required", "add_minimal"}:
        return WriterPlan(
            action="update",
            targets=tuple(existing[:5]),
            rationale="prefer updating/parameterizing existing tests before new files",
        )
    return WriterPlan(
        action="create",
        targets=tuple(scope) or ("tests/test_new_behavior.py",),
        rationale="no suitable existing tests found; create minimal coverage",
    )


def plan_doc_writes(
    *,
    doc_decision: str,
    doc_targets: Iterable[str] | None = None,
    canonical_docs: Iterable[str] | None = None,
) -> WriterPlan:
    """Prefer updating an existing canonical document (FR-027)."""
    if doc_decision in {"none", "", None}:
        return WriterPlan(
            action="skip",
            targets=(),
            rationale="maintenance-reviewer decided no documentation update needed",
        )
    targets = [t for t in (doc_targets or []) if t]
    canonical = [c for c in (canonical_docs or []) if c]
    if doc_decision == "update_canonical" or (targets and canonical):
        chosen = targets or canonical[:1]
        return WriterPlan(
            action="update",
            targets=tuple(chosen),
            rationale="prefer updating existing canonical document",
        )
    if canonical:
        return WriterPlan(
            action="update",
            targets=tuple(canonical[:1]),
            rationale="prefer updating existing canonical document",
        )
    return WriterPlan(
        action="create",
        targets=tuple(targets) or ("docs/note.md",),
        rationale="no canonical doc identified; create minimal note only if required",
    )


def _as_paths(value: Iterable[str] | Path | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Path):
        if value.is_dir():
            return [str(p) for p in sorted(value.rglob("test_*.py"))][:20]
        return [str(value)]
    return [str(v) for v in value]
