"""Cursor fallback-lead provider — discovery + opt-in Mode C execution.

Never intercepts native Cursor CLI/IDE usage; Mode C calls this adapter explicitly.
"""

from __future__ import annotations

from pathlib import Path

from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderSession,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
    probe_version,
    which_binary,
)
from aichestra.providers.execution import run_cli_task

# Cursor agent / CLI names vary; probe common ones without wrapping them.
_CURSOR_BINARIES = ("cursor-agent", "cursor")


class CursorProvider(ProviderAdapter):
    kind = ProviderKind.CURSOR

    def probe(self) -> ProviderStatus:
        binary = which_binary(_CURSOR_BINARIES)
        if not binary:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.FALLBACK_LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail="Cursor binary not found on PATH",
                intercepts_native_cli=False,
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.FALLBACK_LEAD,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="Cursor available as fallback lead (native use unintercepted)",
            intercepts_native_cli=False,
            metadata={"integration": "execution-v1"},
        )

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        status = self.probe()
        if not status.available or not status.binary_path:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=status.detail or "Cursor unavailable",
                session_id=session.session_id,
            )
        prompt = request.bounded_prompt()
        # Prefer cursor-agent print/non-interactive style when available.
        if Path(status.binary_path).name.lower() != "cursor-agent":
            # Avoid launching the GUI IDE as Mode C work.
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=(
                    "Mode C requires cursor-agent CLI for execution; "
                    "native Cursor IDE use remains unchanged"
                ),
                session_id=session.session_id,
            )
        argv = [status.binary_path, "-p", "--force", prompt]
        return run_cli_task(
            binary=status.binary_path,
            argv=argv,
            session=session,
            request=request,
            unavailable_detail="Cursor agent binary unavailable",
        )
