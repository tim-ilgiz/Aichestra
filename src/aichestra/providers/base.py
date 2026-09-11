"""Provider adapter interfaces and structured availability statuses."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from aichestra.execution.domain import ExecutionTarget


class ProviderKind(str, Enum):
    ORCA = "orca"
    CODEX = "codex"
    CURSOR = "cursor"
    LOCAL_WORKER = "local-worker"
    OPENCODE = "opencode"
    OLLAMA = "ollama"


class ProviderRole(str, Enum):
    CONTROL_PLANE = "control_plane"
    LEAD = "lead"
    FALLBACK_LEAD = "fallback_lead"
    WORKER = "worker"
    RUNTIME = "runtime"


class FailureClass(str, Enum):
    NONE = "none"
    UNAVAILABLE = "unavailable"
    QUOTA = "quota"
    AUTH = "auth"
    NETWORK = "network"
    TIMEOUT = "timeout"
    CRASH = "crash"
    CANCEL = "cancel"
    ERROR = "error"


@dataclass(frozen=True)
class ProviderStatus:
    kind: ProviderKind
    available: bool
    role: ProviderRole | None = None
    binary_path: str | None = None
    version: str | None = None
    failure: FailureClass = FailureClass.NONE
    detail: str = ""
    intercepts_native_cli: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "available": self.available,
            "role": self.role.value if self.role else None,
            "binary_path": self.binary_path,
            "version": self.version,
            "failure": self.failure.value,
            "detail": self.detail,
            "intercepts_native_cli": self.intercepts_native_cli,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ProviderSession:
    """Opt-in orchestration session — does not wrap native CLI entrypoints."""

    session_id: str
    kind: ProviderKind
    role: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind.value,
            "role": self.role,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ProviderTaskRequest:
    """Bounded Mode C task: prompt + compact context for an adapter."""

    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    role: str = ""
    timeout_seconds: float = 120.0
    read_only: bool = False
    max_prompt_chars: int = 8_000
    cwd: str | None = None
    attachments: tuple[str, ...] = ()

    execution_target: "ExecutionTarget | None" = field(default=None, repr=False, compare=False)
    role_targets: dict[str, "ExecutionTarget"] = field(default_factory=dict, repr=False, compare=False)
    quota_target: "ExecutionTarget | None" = field(default=None, repr=False, compare=False)
    gate_handler: Callable[[str], dict[str, Any]] | None = field(default=None, repr=False, compare=False)

    def bounded_prompt(self) -> str:
        """Build a bounded prompt; preserve trailing CONSTRAINT when truncating."""
        prompt = self.prompt.strip()
        mid_parts: list[str] = []
        if self.context:
            # Keep context compact — callers should pre-compact research.
            ctx = str(self.context)
            if len(ctx) > 4_000:
                ctx = ctx[:3_999] + "…"
            mid_parts.append(f"CONTEXT: {ctx}")
        if self.attachments:
            mid_parts.append("ATTACHMENTS: " + ", ".join(self.attachments[:40]))

        constraint = ""
        if self.read_only:
            constraint = "CONSTRAINT: read-only; do not modify production files."

        suffix_block = f"\n\n{constraint}" if constraint else ""
        body = "\n\n".join(p for p in (prompt, *mid_parts) if p)
        text = body + suffix_block
        if len(text) <= self.max_prompt_chars:
            return text

        # Always keep full CONSTRAINT when it fits; truncate body first.
        if constraint and len(constraint) <= self.max_prompt_chars:
            if len(constraint) >= self.max_prompt_chars:
                return constraint
            # Prefer ending with the constraint block.
            room = self.max_prompt_chars - len(suffix_block)
            if room <= 1:
                return constraint
            truncated_body = (body[: room - 1] + "…") if body else ""
            if truncated_body:
                return truncated_body + suffix_block
            return constraint

        if constraint:
            return constraint[: self.max_prompt_chars - 1] + "…"
        return text[: self.max_prompt_chars - 1] + "…"


@dataclass(frozen=True)
class ProviderTaskResult:
    ok: bool
    output: str = ""
    failure: FailureClass = FailureClass.NONE
    detail: str = ""
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "failure": self.failure.value,
            "detail": self.detail,
            "session_id": self.session_id,
            "metadata": dict(self.metadata),
        }


class ProviderAdapter(ABC):
    """Discovery + smallest reliable Mode C execution surface (FR-001/043/045).

    Native Codex/Cursor CLIs remain unintercepted: adapters are opt-in callers
    used only when Aichestra Mode C explicitly starts work.
    """

    kind: ProviderKind

    @abstractmethod
    def probe(self) -> ProviderStatus:
        """Return structured availability without consuming account quota."""

    def supports_execution(self) -> bool:
        """Whether this adapter can start/send Mode C tasks."""
        return True

    def start_session(
        self,
        *,
        role: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> ProviderSession:
        """Start an orchestration session (local bookkeeping + provider hook)."""
        meta = dict(context or {})
        meta.setdefault("integration", "execution-v1")
        return ProviderSession(
            session_id=str(uuid.uuid4()),
            kind=self.kind,
            role=role,
            metadata=meta,
        )

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        """Send a bounded prompt/context and observe success/failure.

        Default is unavailable; concrete adapters override with CLI or fakes.
        """
        return ProviderTaskResult(
            ok=False,
            failure=FailureClass.UNAVAILABLE,
            detail=f"{self.kind.value} execution not implemented",
            session_id=session.session_id,
        )

    def execute_task(self, request: ProviderTaskRequest) -> ProviderTaskResult:
        """start_session → send convenience for single-shot Mode C work."""
        session = self.start_session(role=request.role, context=request.context)
        return self.send(session, request)


def which_binary(names: tuple[str, ...] | list[str]) -> str | None:
    import shutil

    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def probe_version(binary: str, args: list[str] | None = None) -> str | None:
    import subprocess

    cmd = [binary, *(args or ["--version"])]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if not text:
        return None
    return text.splitlines()[0].strip()[:200]
