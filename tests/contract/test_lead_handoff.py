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
    )
    data = packet.to_dict()
    assert "full_transcript" not in data
    assert len(data["git_diff"]) < 60_000
    assert "truncated for handoff bound" in data["git_diff"]
    payload = prepare_manual_handoff(packet)
    assert payload["mode"] == "manual_one_action"
    assert payload["automatic_quota_fallback_reliable"] is False
    assert "packet" in payload
