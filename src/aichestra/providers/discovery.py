"""Discover optional providers with graceful degradation."""

from __future__ import annotations

from typing import Any, Mapping

from aichestra.providers.base import (
    FailureClass,
    ProviderKind,
    ProviderRole,
    ProviderStatus,
)
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
    enabled: Mapping[str, bool] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[ProviderStatus]:
    """Probe Orca/Codex/Cursor/local-worker without intercepting native CLIs.

    When ``AICHESTRA_FAKE_PROVIDERS`` or ``AICHESTRA_NO_REAL_QUOTA`` is set,
    return fake success statuses instead of probing real binaries (FR-039).

    ``enabled`` maps kind names (``orca``, ``codex``, ``cursor``, ``local-worker``)
    to booleans; disabled kinds are returned as unavailable without probing.

    ``config`` is forwarded to ``LocalWorkerProvider`` so machine-local
    preferred/allowed models and size caps are honored during discovery.
    """
    if _fake_providers_forced() and not force_real:
        statuses = _inline_fake_statuses(local_enabled=local_enabled)
    else:
        cfg = config
        if cfg is None:
            try:
                from aichestra.config.layering import resolve_config

                cfg = resolve_config()
            except Exception:
                cfg = None
        adapters = [
            OrcaProvider(),
            CodexProvider(),
            CursorProvider(),
            LocalWorkerProvider(
                local_enabled=local_enabled,
                ollama_host=ollama_host,
                config=cfg,
            ),
        ]
        statuses = [adapter.probe() for adapter in adapters]
    return [_apply_enable(s, enabled) for s in statuses]


def _apply_enable(
    status: ProviderStatus,
    enabled: Mapping[str, bool] | None,
) -> ProviderStatus:
    if not enabled:
        return status
    key = status.kind.value
    # Accept both local-worker and local aliases.
    if key == ProviderKind.LOCAL_WORKER.value:
        flag = enabled.get(key)
        if flag is None:
            flag = enabled.get("local")
    else:
        flag = enabled.get(key)
    if flag is None or flag:
        return status
    return ProviderStatus(
        kind=status.kind,
        available=False,
        role=status.role,
        binary_path=status.binary_path,
        version=status.version,
        failure=FailureClass.UNAVAILABLE,
        detail=f"{key} disabled by configuration / CLI override",
        intercepts_native_cli=False,
        metadata={**dict(status.metadata), "disabled_by_config": True},
    )


def _inline_fake_statuses(*, local_enabled: bool) -> list[ProviderStatus]:
    """Stdlib-safe fakes when tests.fakes is not importable (installed package)."""

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
    enabled: Mapping[str, bool] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    statuses = discover_providers(
        local_enabled=local_enabled,
        ollama_host=ollama_host,
        force_real=force_real,
        enabled=enabled,
        config=config,
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


def enabled_map_from_config(config: Mapping[str, Any] | None) -> dict[str, bool]:
    """Build discover_providers ``enabled`` map from layered config."""
    from aichestra.config.layering import provider_enabled

    return {
        "orca": provider_enabled(config, "orca"),
        "codex": provider_enabled(config, "codex"),
        "cursor": provider_enabled(config, "cursor"),
        "local-worker": provider_enabled(config, "local-worker"),
    }
