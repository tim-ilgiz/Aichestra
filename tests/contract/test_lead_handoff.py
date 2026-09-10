"""Contract: lead selection and handoff policy."""

from __future__ import annotations

from aichestra.orchestration.handoff import (
    automatic_quota_fallback_reliable,
    build_handoff_packet,
    prepare_manual_handoff,
    should_auto_fallback_on_quota,
)
from aichestra.orchestration.roles import select_lead
from aichestra.providers.base import ProviderKind
from tests.fakes.providers import fake_provider_set


def test_codex_preferred_over_cursor() -> None:
    lead = select_lead(fake_provider_set(codex="success", cursor="success"))
    assert lead.lead == ProviderKind.CODEX


def test_cursor_fallback_when_codex_missing() -> None:
    lead = select_lead(fake_provider_set(codex="unavailable", cursor="success"))
    assert lead.lead == ProviderKind.CURSOR


def test_manual_handoff_flag_and_bounded_packet() -> None:
    assert automatic_quota_fallback_reliable is False
    assert should_auto_fallback_on_quota() is False
    packet = build_handoff_packet(
        original_request="implement feature",
        git_diff="x" * 60_000,
        full_transcript="SHOULD_NOT_APPEAR",
        next_action="continue in Cursor",
        orca_run_id="run-abc",
    )
    data = packet.to_dict()
    assert "full_transcript" not in data
    assert len(data["git_diff"]) < 60_000
    assert "truncated for handoff bound" in data["git_diff"]
    payload = prepare_manual_handoff(packet)
    assert payload["mode"] == "manual_one_action"
    assert payload["automatic_quota_fallback_reliable"] is False
    assert payload["preserves_orca_run"] is True
    assert "run-use" in payload["suggested_orca_command"]
    assert "--run" in payload["suggested_orca_command"]
    assert "--no-parent" not in payload["suggested_orca_command"]
    assert "packet" in payload


def test_handoff_without_run_id_does_not_claim_preserve() -> None:
    packet = build_handoff_packet(original_request="x")
    payload = prepare_manual_handoff(packet)
    assert payload["preserves_orca_run"] is False
    assert "--no-parent" not in payload["suggested_orca_command"]
    executed = prepare_manual_handoff(packet, execute=True)
    assert executed["executed"] is False
    assert "run_id" in executed["execute_error"]
