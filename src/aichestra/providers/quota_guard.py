"""Structural guard against real Codex/Cursor quota use in CI/tests (FR-039)."""

from __future__ import annotations

import os


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def real_provider_execution_blocked() -> bool:
    """True when env forbids probing/executing real paid provider CLIs."""
    for key in ("AICHESTRA_FAKE_PROVIDERS", "AICHESTRA_NO_REAL_QUOTA"):
        value = os.environ.get(key, "").strip().lower()
        if value in _TRUTHY:
            return True
    return False


def blocked_execution_detail() -> str:
    return (
        "Real provider execution blocked by AICHESTRA_FAKE_PROVIDERS / "
        "AICHESTRA_NO_REAL_QUOTA (FR-039). Bind fake adapters for Mode C."
    )
