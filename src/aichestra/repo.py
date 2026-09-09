"""Locate the Aichestra repository root from an arbitrary clone path."""

from __future__ import annotations

from pathlib import Path

_MARKERS = ("pyproject.toml", "AGENTS.md", ".specify")


def find_repo_root(start: Path | str | None = None) -> Path:
    """Walk upward from *start* until Aichestra markers are found."""
    current = Path(start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if _looks_like_root(candidate):
            return candidate
    raise FileNotFoundError(
        f"Could not locate Aichestra repository root from {current}"
    )


def _looks_like_root(path: Path) -> bool:
    hits = sum(1 for marker in _MARKERS if (path / marker).exists())
    return hits >= 2
