"""Codex preferred-lead provider — discovery + opt-in Mode C execution.

Never intercepts native `codex` CLI usage; Mode C calls this adapter explicitly.
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
    probe_version,
    which_binary,
)
from aichestra.providers.execution import run_cli_task

_CODEX_BINARIES = ("codex",)


class CodexProvider(ProviderAdapter):
    kind = ProviderKind.CODEX

    def probe(self) -> ProviderStatus:
        binary = which_binary(_CODEX_BINARIES)
        if not binary:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail="Codex binary not found on PATH",
                intercepts_native_cli=False,
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.LEAD,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="Codex available as preferred lead (native use unintercepted)",
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
                detail=status.detail or "Codex unavailable",
                session_id=session.session_id,
            )
        prompt = request.bounded_prompt()
        # Non-interactive exec path — does not shadow or wrap the user's `codex`.
        argv = [status.binary_path, "exec", "--skip-git-repo-check", prompt]
        return run_cli_task(
            binary=status.binary_path,
            argv=argv,
            session=session,
            request=request,
            unavailable_detail="Codex binary unavailable",
        )
