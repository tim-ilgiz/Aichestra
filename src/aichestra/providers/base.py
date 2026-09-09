"""Provider adapter interfaces and structured availability statuses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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


class ProviderAdapter(ABC):
    kind: ProviderKind

    @abstractmethod
    def probe(self) -> ProviderStatus:
        """Return structured availability without consuming account quota."""


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
