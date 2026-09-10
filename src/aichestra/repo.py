"""Locate the Aichestra repository root from an arbitrary clone path."""

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
    """Resolve the Aichestra config/home root without using a foreign cwd.

    Order: explicit ``repo_root`` → ``AICHESTRA_REPO_ROOT`` → cwd only when
    that tree is actually the Aichestra clone. Never silently adopt
    ``project_root`` or another repo that merely has AGENTS.md.
    """
    if repo_root is not None:
        return Path(repo_root).resolve()
    env = (os.environ.get(AICHESTRA_REPO_ROOT_ENV) or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        discovered = find_repo_root(start)
    except FileNotFoundError as exc:
        raise ValueError(
            "Aichestra config root required: pass --repo-root or set "
            f"{AICHESTRA_REPO_ROOT_ENV}; refusing cwd-based discovery that "
            "can pick a foreign target project"
        ) from exc
    if looks_like_aichestra_root(discovered):
        return discovered
    project_hint = ""
    if project_root is not None:
        project_hint = f" Target project is {Path(project_root).resolve()}."
    raise ValueError(
        "Aichestra config root required: cwd is not the Aichestra clone "
        f"(found {discovered}). Pass --repo-root or set {AICHESTRA_REPO_ROOT_ENV}."
        f"{project_hint}"
    )


def _looks_like_root(path: Path) -> bool:
    hits = sum(1 for marker in _MARKERS if (path / marker).exists())
    return hits >= 2
