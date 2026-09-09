"""Cursor fallback-lead provider — discovery + opt-in Mode C execution.

Never intercepts native Cursor CLI/IDE usage; Mode C calls this adapter explicitly.
Only ``cursor-agent`` (and Windows ``.exe``/``.cmd`` variants) are Mode-C-executable.
The plain ``cursor`` IDE launcher is never treated as available for Mode C.
"""

from __future__ import annotations

from pathlib import Path

from aichestra.providers.attachments import cursor_image_flags
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

# Mode C requires cursor-agent; do not discover the plain IDE launcher.
_CURSOR_AGENT_BINARIES = ("cursor-agent", "cursor-agent.exe", "cursor-agent.cmd")
_AGENT_EXTENSIONS = (".exe", ".cmd", ".bat")


def _strip_windows_ext(name: str) -> str:
    lower = name.lower()
    for ext in _AGENT_EXTENSIONS:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _is_cursor_agent_basename(path: str) -> bool:
    """True when basename is cursor-agent* after stripping Windows extensions."""
    base = _strip_windows_ext(Path(path).name).lower()
    return base == "cursor-agent" or base.startswith("cursor-agent")


class CursorProvider(ProviderAdapter):
    kind = ProviderKind.CURSOR

    def probe(self) -> ProviderStatus:
        binary = which_binary(_CURSOR_AGENT_BINARIES)
        if not binary or not _is_cursor_agent_basename(binary):
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.FALLBACK_LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail=(
                    "cursor-agent not found on PATH "
                    "(plain cursor IDE is not Mode-C-executable)"
                ),
                intercepts_native_cli=False,
                metadata={"executable": False},
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.FALLBACK_LEAD,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="cursor-agent available as fallback lead (native IDE unintercepted)",
            intercepts_native_cli=False,
            metadata={"integration": "execution-v1", "executable": True},
        )

    def supports_execution(self) -> bool:
        status = self.probe()
        return bool(status.available and status.metadata.get("executable", False))

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
        if not _is_cursor_agent_basename(status.binary_path):
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
        prompt = request.bounded_prompt()
        image_flags = cursor_image_flags(request.attachments)
        # Print/non-interactive; omit --force when read_only.
        if request.read_only:
            argv = [status.binary_path, "-p", *image_flags, prompt]
        else:
            argv = [status.binary_path, "-p", "--force", *image_flags, prompt]
        result = run_cli_task(
            binary=status.binary_path,
            argv=argv,
            session=session,
            request=request,
            unavailable_detail="Cursor agent binary unavailable",
        )
        if image_flags:
            meta = dict(result.metadata)
            meta["bytes_delivered"] = True
            meta["attachment_delivery"] = "cursor_image_flags"
            return ProviderTaskResult(
                ok=result.ok,
                output=result.output,
                failure=result.failure,
                detail=result.detail,
                session_id=result.session_id,
                metadata=meta,
            )
        return result
