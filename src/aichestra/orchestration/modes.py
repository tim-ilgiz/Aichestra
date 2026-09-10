"""Three distinct user modes: Native / Orca Interactive / Orchestrated."""

from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    """Aichestra interaction modes (FR-043).

    A Native — Codex/Cursor used directly; Aichestra does not intercept.
    B Orca Interactive — single-agent session; no automatic full orchestration.
    C Orchestrated — explicit Aichestra workflow start coordinates roles.
    """

    NATIVE = "native"
    ORCA_INTERACTIVE = "orca_interactive"
    ORCHESTRATED = "orchestrated"


_ALIASES = {
    "a": Mode.NATIVE,
    "native": Mode.NATIVE,
    "b": Mode.ORCA_INTERACTIVE,
    "interactive": Mode.ORCA_INTERACTIVE,
    "orca": Mode.ORCA_INTERACTIVE,
    "orca_interactive": Mode.ORCA_INTERACTIVE,
    "c": Mode.ORCHESTRATED,
    "orchestrated": Mode.ORCHESTRATED,
    "orchestration": Mode.ORCHESTRATED,
}


def parse_mode(value: str | Mode | None, *, default: Mode = Mode.NATIVE) -> Mode:
    if value is None:
        return default
    if isinstance(value, Mode):
        return value
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if key not in _ALIASES:
        raise ValueError(f"Unknown mode: {value!r}")
    return _ALIASES[key]


def starts_full_orchestration(mode: Mode) -> bool:
    """Only Mode C starts multi-role orchestration."""
    return mode is Mode.ORCHESTRATED


def intercepts_native_cli(mode: Mode) -> bool:
    """Native Codex/Cursor CLIs are never intercepted in any mode."""
    return False
