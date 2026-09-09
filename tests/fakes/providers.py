"""Fake provider test doubles — never consume real Codex/Cursor quota."""

from __future__ import annotations

from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderStatus,
)

SCENARIOS = ("success", "unavailable", "quota", "auth", "timeout", "error")


def _status(
    kind: ProviderKind,
    *,
    available: bool,
    role: ProviderRole,
    failure: FailureClass = FailureClass.NONE,
    detail: str = "",
) -> ProviderStatus:
    return ProviderStatus(
        kind=kind,
        available=available,
        role=role,
        binary_path=f"/fake/{kind.value}" if available else None,
        version="fake-0.0.0" if available else None,
        failure=failure,
        detail=detail or failure.value,
        intercepts_native_cli=False,
        metadata={"fake": True},
    )


class FakeProvider(ProviderAdapter):
    def __init__(self, status: ProviderStatus) -> None:
        self.kind = status.kind
        self._status = status

    def probe(self) -> ProviderStatus:
        return self._status


def fake_codex(scenario: str = "success") -> FakeProvider:
    return FakeProvider(_scenario(ProviderKind.CODEX, ProviderRole.LEAD, scenario))


def fake_cursor(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.CURSOR, ProviderRole.FALLBACK_LEAD, scenario)
    )


def fake_local_worker(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.LOCAL_WORKER, ProviderRole.WORKER, scenario)
    )


def fake_orca(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.ORCA, ProviderRole.CONTROL_PLANE, scenario)
    )


def _scenario(
    kind: ProviderKind, role: ProviderRole, scenario: str
) -> ProviderStatus:
    scenario = scenario.lower().strip()
    if scenario in {"success", "ok", "available"}:
        return _status(kind, available=True, role=role, detail="fake success")
    if scenario in {"unavailable", "absent", "missing"}:
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.UNAVAILABLE,
            detail="fake unavailable",
        )
    if scenario == "quota":
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.QUOTA,
            detail="fake quota exhausted",
        )
    if scenario == "auth":
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.AUTH,
            detail="fake auth failure",
        )
    if scenario == "timeout":
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.TIMEOUT,
            detail="fake timeout",
        )
    if scenario in {"error", "generic"}:
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.ERROR,
            detail="fake generic error",
        )
    raise ValueError(f"unknown fake scenario: {scenario}")


def fake_provider_set(
    *,
    codex: str = "unavailable",
    cursor: str = "unavailable",
    local: str = "unavailable",
    orca: str = "unavailable",
) -> list[ProviderStatus]:
    """Return ProviderStatus list for lead-selection / degradation tests."""
    return [
        fake_orca(orca).probe(),
        fake_codex(codex).probe(),
        fake_cursor(cursor).probe(),
        fake_local_worker(local).probe(),
    ]
