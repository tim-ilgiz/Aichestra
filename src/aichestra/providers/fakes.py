"""In-tree fake providers for Mode C when real quota is blocked (FR-039).

These adapters never spawn Codex/Cursor/Orca binaries. Used by the CLI when
``AICHESTRA_FAKE_PROVIDERS`` / ``AICHESTRA_NO_REAL_QUOTA`` is set so orchestration
can still advance without consuming account quota.
"""

from __future__ import annotations

from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderSession,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
)


class FakeModeCProvider(ProviderAdapter):
    """Deterministic Mode C stand-in — no subprocess, no quota."""

    def __init__(
        self,
        kind: ProviderKind,
        role: ProviderRole,
        *,
        available: bool = True,
        output: str = "fake mode-c execution ok",
    ) -> None:
        self.kind = kind
        self._role = role
        self._available = available
        self._output = output
        self.sent: list[ProviderTaskRequest] = []

    def probe(self) -> ProviderStatus:
        return ProviderStatus(
            kind=self.kind,
            available=self._available,
            role=self._role,
            binary_path=f"/fake/{self.kind.value}" if self._available else None,
            version="fake-0.0.0" if self._available else None,
            failure=FailureClass.NONE if self._available else FailureClass.UNAVAILABLE,
            detail="fake provider (AICHESTRA_FAKE_PROVIDERS)",
            intercepts_native_cli=False,
            metadata={"fake": True},
        )

    def supports_execution(self) -> bool:
        return True

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        self.sent.append(request)
        if not self._available:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail="fake provider unavailable",
                session_id=session.session_id,
                metadata={"fake": True},
            )
        return ProviderTaskResult(
            ok=True,
            output=self._output,
            failure=FailureClass.NONE,
            detail="fake execution ok",
            session_id=session.session_id,
            metadata={"fake": True},
        )


def fake_orca() -> FakeModeCProvider:
    return FakeModeCProvider(ProviderKind.ORCA, ProviderRole.CONTROL_PLANE)


def fake_codex_lead() -> FakeModeCProvider:
    return FakeModeCProvider(ProviderKind.CODEX, ProviderRole.LEAD)


def fake_cursor_lead() -> FakeModeCProvider:
    return FakeModeCProvider(ProviderKind.CURSOR, ProviderRole.FALLBACK_LEAD)


def fake_local_worker(*, enabled: bool) -> FakeModeCProvider:
    return FakeModeCProvider(
        ProviderKind.LOCAL_WORKER,
        ProviderRole.WORKER,
        available=enabled,
        output="fake local-worker research summary",
    )
