"""Discover optional providers with graceful degradation."""

from __future__ import annotations

from typing import Any

from aichestra.providers.base import ProviderStatus
from aichestra.providers.codex import CodexProvider
from aichestra.providers.cursor import CursorProvider
from aichestra.providers.local_worker import LocalWorkerProvider
from aichestra.providers.orca import OrcaProvider
from aichestra.providers.quota_guard import real_provider_execution_blocked


def _fake_providers_forced() -> bool:
    """CI / tests inject fake providers so real Codex/Cursor quota is never used."""
    return real_provider_execution_blocked()


def discover_providers(
    *,
    local_enabled: bool = False,
    ollama_host: str | None = None,
    force_real: bool = False,
) -> list[ProviderStatus]:
    """Probe Orca/Codex/Cursor/local-worker without intercepting native CLIs.

    When ``AICHESTRA_FAKE_PROVIDERS`` or ``AICHESTRA_NO_REAL_QUOTA`` is set,
    return fake success statuses instead of probing real binaries (FR-039).
    """
    if _fake_providers_forced() and not force_real:
        return _inline_fake_statuses(local_enabled=local_enabled)

    adapters = [
        OrcaProvider(),
        CodexProvider(),
        CursorProvider(),
        LocalWorkerProvider(local_enabled=local_enabled, ollama_host=ollama_host),
    ]
    return [adapter.probe() for adapter in adapters]


def _inline_fake_statuses(*, local_enabled: bool) -> list[ProviderStatus]:
    """Stdlib-safe fakes when tests.fakes is not importable (installed package)."""
    from aichestra.providers.base import (
        FailureClass,
        ProviderKind,
        ProviderRole,
        ProviderStatus,
    )

    def fake(kind: ProviderKind, role: ProviderRole, available: bool) -> ProviderStatus:
        return ProviderStatus(
            kind=kind,
            available=available,
            role=role,
            binary_path=f"/fake/{kind.value}" if available else None,
            version="fake-0.0.0" if available else None,
            failure=FailureClass.NONE if available else FailureClass.UNAVAILABLE,
            detail="fake provider (AICHESTRA_FAKE_PROVIDERS)",
            intercepts_native_cli=False,
            metadata={"fake": True},
        )

    return [
        fake(ProviderKind.ORCA, ProviderRole.CONTROL_PLANE, True),
        fake(ProviderKind.CODEX, ProviderRole.LEAD, True),
        fake(ProviderKind.CURSOR, ProviderRole.FALLBACK_LEAD, True),
        fake(ProviderKind.LOCAL_WORKER, ProviderRole.WORKER, bool(local_enabled)),
    ]


def discover_providers_report(
    *,
    local_enabled: bool = False,
    ollama_host: str | None = None,
    force_real: bool = False,
) -> dict[str, Any]:
    statuses = discover_providers(
        local_enabled=local_enabled,
        ollama_host=ollama_host,
        force_real=force_real,
    )
    by_kind = {s.kind.value: s.to_dict() for s in statuses}
    return {
        "providers": by_kind,
        "any_lead": any(
            s.available and s.kind.value in {"codex", "cursor"} for s in statuses
        ),
        "orca_available": any(
            s.available and s.kind.value == "orca" for s in statuses
        ),
        "local_worker_available": any(
            s.available and s.kind.value == "local-worker" for s in statuses
        ),
        "native_cli_intercepted": any(s.intercepts_native_cli for s in statuses),
        "fake_providers": _fake_providers_forced() and not force_real,
    }
