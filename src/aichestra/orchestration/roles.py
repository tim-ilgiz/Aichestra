"""Lead and role selection — Codex preferred, Cursor fallback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from aichestra.providers.base import ProviderKind, ProviderStatus


class Role(str, Enum):
    LEAD = "lead"
    RESEARCHER = "repository-researcher"
    MAINTENANCE_REVIEWER = "maintenance-reviewer"
    TEST_WRITER = "test-writer"
    DOC_WRITER = "doc-writer"
    VERIFICATION_RUNNER = "verification-runner"
    LOCAL_WORKER = "local-worker"


@dataclass(frozen=True)
class LeadSelection:
    lead: ProviderKind | None
    reason: str
    preferred_available: bool
    fallback_available: bool

    @property
    def ok(self) -> bool:
        return self.lead is not None


def select_lead(
    providers: Iterable[ProviderStatus],
    *,
    preferred: ProviderKind | str = ProviderKind.CODEX,
    fallback: ProviderKind | str = ProviderKind.CURSOR,
) -> LeadSelection:
    """Select Codex as preferred lead, Cursor as fallback (FR-003/004)."""
    preferred_kind = ProviderKind(preferred) if isinstance(preferred, str) else preferred
    fallback_kind = ProviderKind(fallback) if isinstance(fallback, str) else fallback
    by_kind = {p.kind: p for p in providers}

    pref = by_kind.get(preferred_kind)
    fb = by_kind.get(fallback_kind)
    preferred_ok = bool(pref and pref.available)
    fallback_ok = bool(fb and fb.available)

    if preferred_ok:
        return LeadSelection(
            lead=preferred_kind,
            reason=f"{preferred_kind.value} preferred lead available",
            preferred_available=True,
            fallback_available=fallback_ok,
        )
    if fallback_ok:
        return LeadSelection(
            lead=fallback_kind,
            reason=(
                f"{preferred_kind.value} unavailable; "
                f"using {fallback_kind.value} fallback"
            ),
            preferred_available=False,
            fallback_available=True,
        )
    return LeadSelection(
        lead=None,
        reason="no lead provider available; degrade gracefully",
        preferred_available=False,
        fallback_available=False,
    )


def researcher_prefers_local(local_worker_available: bool) -> bool:
    """repository-researcher prefers local-worker when available (FR-053)."""
    return local_worker_available
