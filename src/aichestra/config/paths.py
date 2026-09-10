"""Portable config homes for global pip installs (no Aichestra clone required)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def user_config_home() -> Path:
    """Return the per-user Aichestra config directory (cross-platform)."""
    override = (os.environ.get("AICHESTRA_CONFIG_HOME") or "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        return (base / "aichestra").resolve()

    if os.name == "nt":
        appdata = (os.environ.get("APPDATA") or "").strip()
        if appdata:
            return (Path(appdata) / "aichestra").resolve()
        return (Path.home() / "AppData" / "Roaming" / "aichestra").resolve()

    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return (Path(xdg) / "aichestra").resolve()
    return (Path.home() / ".config" / "aichestra").resolve()


def ensure_user_config_home() -> Path:
    home = user_config_home()
    home.mkdir(parents=True, exist_ok=True)
    return home
