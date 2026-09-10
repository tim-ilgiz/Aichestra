"""Preserve target-project AI Factory / Factory tooling (FR-029)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_FACTORY_MARKERS = (
    ".factory",
    "factory.json",
    "FACTORY.md",
    "ai-factory",
    ".ai-factory",
    "factory",
)


@dataclass(frozen=True)
class FactoryDetection:
    present: bool
    markers: tuple[str, ...]
    may_delete: bool
    may_migrate: bool
    reason: str


def detect_factory_tooling(project_root: Path | str) -> FactoryDetection:
    """Detect Factory tooling; never delete or migrate without approval."""
    root = Path(project_root)
    found: list[str] = []
    for marker in _FACTORY_MARKERS:
        path = root / marker
        if path.exists():
            found.append(marker)
    # Also scan shallow children for factory-ish dirs
    try:
        for child in root.iterdir():
            name = child.name.lower()
            if "factory" in name and child.is_dir():
                if child.name not in found:
                    found.append(child.name)
    except OSError:
        pass

    if found:
        return FactoryDetection(
            present=True,
            markers=tuple(found),
            may_delete=False,
            may_migrate=False,
            reason=(
                "Factory tooling detected — preserve unless migration is "
                "explicitly approved"
            ),
        )
    return FactoryDetection(
        present=False,
        markers=(),
        may_delete=False,
        may_migrate=False,
        reason="no Factory tooling detected",
    )


def approve_factory_migration(*, explicit_approval: bool) -> bool:
    """Migration is blocked unless the operator gives explicit approval."""
    return bool(explicit_approval)
