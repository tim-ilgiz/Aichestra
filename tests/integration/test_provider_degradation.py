"""Integration: provider degradation with fakes (no real quota)."""

from __future__ import annotations

from aichestra.orchestration.roles import select_lead
from aichestra.providers.base import FailureClass, ProviderKind
from tests.fakes.providers import SCENARIOS, fake_codex, fake_provider_set


def test_useful_without_codex() -> None:
    providers = fake_provider_set(codex="unavailable", cursor="success", local="unavailable")
    lead = select_lead(providers)
    assert lead.lead == ProviderKind.CURSOR
    assert lead.preferred_available is False
    assert lead.fallback_available is True


def test_useful_without_cursor() -> None:
    providers = fake_provider_set(codex="success", cursor="unavailable")
    lead = select_lead(providers)
    assert lead.lead == ProviderKind.CODEX


def test_useful_without_local_worker() -> None:
    providers = fake_provider_set(codex="success", cursor="success", local="unavailable")
    lead = select_lead(providers)
    assert lead.ok
    local = next(p for p in providers if p.kind == ProviderKind.LOCAL_WORKER)
    assert local.available is False


def test_degrade_when_all_leads_missing() -> None:
    providers = fake_provider_set(codex="unavailable", cursor="unavailable")
    lead = select_lead(providers)
    assert lead.lead is None
    assert not lead.ok


def test_fake_scenarios_cover_failure_classes() -> None:
    for scenario in SCENARIOS:
        status = fake_codex(scenario).probe()
        assert status.metadata.get("fake") is True
        assert status.intercepts_native_cli is False
        if scenario == "success":
            assert status.available and status.failure == FailureClass.NONE
        else:
            assert not status.available
            assert status.failure != FailureClass.NONE
