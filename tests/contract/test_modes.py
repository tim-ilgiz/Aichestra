"""Contract: modes and native CLI non-interception."""

from __future__ import annotations

import pytest

from aichestra.orchestration.modes import (
    Mode,
    intercepts_native_cli,
    parse_mode,
    starts_full_orchestration,
)
from aichestra.orchestration.workflow import ModeCRunController


def test_three_modes_parse() -> None:
    assert parse_mode("native") is Mode.NATIVE
    assert parse_mode("orca_interactive") is Mode.ORCA_INTERACTIVE
    assert parse_mode("orchestrated") is Mode.ORCHESTRATED
    assert parse_mode("A") is Mode.NATIVE
    assert parse_mode("B") is Mode.ORCA_INTERACTIVE
    assert parse_mode("C") is Mode.ORCHESTRATED


def test_only_orchestrated_starts_full_flow() -> None:
    assert starts_full_orchestration(Mode.NATIVE) is False
    assert starts_full_orchestration(Mode.ORCA_INTERACTIVE) is False
    assert starts_full_orchestration(Mode.ORCHESTRATED) is True


def test_never_intercepts_native_cli() -> None:
    for mode in Mode:
        assert intercepts_native_cli(mode) is False


def test_workflow_rejects_non_orchestrated_modes() -> None:
    with pytest.raises(ValueError):
        ModeCRunController(mode=Mode.NATIVE)
    with pytest.raises(ValueError):
        ModeCRunController(mode=Mode.ORCA_INTERACTIVE)
