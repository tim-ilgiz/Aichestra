"""Locate the Aichestra config root (clone or portable user home)."""

from __future__ import annotations

import os
from pathlib import Path

_MARKERS = ("pyproject.toml", "AGENTS.md", ".specify")
AICHESTRA_REPO_ROOT_ENV = "AICHESTRA_REPO_ROOT"


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


def looks_like_aichestra_root(path: Path | str) -> bool:
    """True when *path* is the Aichestra platform clone, not an arbitrary project.

    Marker-only discovery (pyproject/AGENTS/.specify) can match a target
    project. Machine-local config lives in the Aichestra clone, so prove-launch
    must not treat a foreign project as the config root.
    """
    root = Path(path)
    return _looks_like_root(root) and (root / "src" / "aichestra").is_dir()


def resolve_aichestra_config_root(
    *,
    repo_root: Path | str | None = None,
    project_root: Path | str | None = None,
    start: Path | str | None = None,
) -> Path:
    """Resolve Aichestra config home for layered defaults / machine-local.

    Order:
    1. explicit ``repo_root``
    2. ``AICHESTRA_REPO_ROOT``
    3. cwd when it is the Aichestra clone
    4. portable user config home (global pip install — no clone required)

    Never silently adopt ``project_root`` (foreign target project).
    """
    if repo_root is not None:
        return Path(repo_root).resolve()
    env = (os.environ.get(AICHESTRA_REPO_ROOT_ENV) or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        discovered = find_repo_root(start)
    except FileNotFoundError:
        from aichestra.config.paths import user_config_home

        return user_config_home()
    if looks_like_aichestra_root(discovered):
        return discovered
    # Foreign target project — use user config home, not project root.
    from aichestra.config.paths import user_config_home

    _ = project_root  # documented: intentionally unused for config home
    return user_config_home()


def _looks_like_root(path: Path) -> bool:
    hits = sum(1 for marker in _MARKERS if (path / marker).exists())
    return hits >= 2


def trusted_config_root(path: Path | str) -> bool:
    """Accept contributor config trees and the installed-package user home."""
    from aichestra.config.paths import user_config_home

    root = Path(path).resolve()
    return (looks_like_aichestra_root(root)
            or (root / "policies" / "defaults.json").is_file()
            or root == user_config_home())
