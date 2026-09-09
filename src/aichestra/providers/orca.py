"""Orca control-plane adapter — discovery + Mode C session/task execution.

Inspect installed Orca docs / `orca skills get` on the developer machine before
extending session/worktree primitives. Aichestra must not duplicate a
general-purpose orchestrator (no CAO layer). Native Codex/Cursor remain
unintercepted.
"""

from __future__ import annotations

from pathlib import Path

from aichestra.platform_detect import OperatingSystem, detect_os
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

# Common CLI names; discovery only for PATH — never wraps/intercepts user invocations.
_ORCA_BINARIES = ("orca", "orca-cli")


def resolve_orca_binary() -> str | None:
    """Resolve Orca CLI from PATH or well-known install locations (no user hard-code)."""
    found = which_binary(_ORCA_BINARIES)
    if found:
        return found
    for candidate in _platform_cli_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def _platform_cli_candidates() -> list[Path]:
    os_name = detect_os()
    home = Path.home()
    if os_name == OperatingSystem.MACOS:
        return [
            Path("/Applications/Orca.app/Contents/Resources/bin/orca"),
            home / "Applications/Orca.app/Contents/Resources/bin/orca",
        ]
    if os_name == OperatingSystem.WINDOWS:
        local = Path.home() / "AppData" / "Local"
        return [
            local / "Programs" / "Orca" / "orca.exe",
            local / "Orca" / "orca.exe",
        ]
    # Linux: common user/system prefixes; PATH remains primary.
    return [
        home / ".local" / "bin" / "orca",
        Path("/usr/local/bin/orca"),
        Path("/usr/bin/orca"),
    ]


class OrcaProvider(ProviderAdapter):
    kind = ProviderKind.ORCA

    def probe(self) -> ProviderStatus:
        binary = resolve_orca_binary()
        if not binary:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.CONTROL_PLANE,
                failure=FailureClass.UNAVAILABLE,
                detail="Orca binary not found on PATH or known install locations",
                intercepts_native_cli=False,
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.CONTROL_PLANE,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="Orca available as opt-in control plane",
            intercepts_native_cli=False,
            metadata={"integration": "execution-v1"},
        )

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        """Dispatch a bounded Mode C prompt through Orca (opt-in control plane)."""
        status = self.probe()
        if not status.available or not status.binary_path:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=status.detail or "Orca unavailable",
                session_id=session.session_id,
            )
        prompt = request.bounded_prompt()
        # Smallest reliable wrapper: non-interactive run with session id metadata.
        # Does not replace native `orca` interactive use.
        argv = [
            status.binary_path,
            "run",
            "--session",
            session.session_id,
            prompt,
        ]
        result = run_cli_task(
            binary=status.binary_path,
            argv=argv,
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        meta = dict(result.metadata)
        meta["control_plane"] = True
        meta["integration"] = "execution-v1"
        return ProviderTaskResult(
            ok=result.ok,
            output=result.output,
            failure=result.failure,
            detail=result.detail,
            session_id=result.session_id,
            metadata=meta,
        )
