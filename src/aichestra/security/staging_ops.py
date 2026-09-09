"""Typed staging diagnostic builders — structured argv, not free-form shell."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class StagingOpKind(str, Enum):
    UPTIME = "uptime"
    DISK_USAGE = "disk_usage"
    FREE_MEMORY = "free_memory"
    PROCESS_LIST = "process_list"
    SERVICE_STATUS = "service_status"
    TAIL_LOG = "tail_log"
    CAT_FILE = "cat_file"
    CURL_HEALTH = "curl_health"


@dataclass(frozen=True)
class StagingOp:
    kind: StagingOpKind
    argv: tuple[str, ...]
    description: str

    def to_argv(self) -> list[str]:
        return list(self.argv)


def build_op(kind: StagingOpKind | str, **params: str) -> StagingOp:
    """Build a typed read-only diagnostic operation (FR-059)."""
    op_kind = StagingOpKind(kind) if isinstance(kind, str) else kind
    builders = {
        StagingOpKind.UPTIME: lambda: ("uptime",),
        StagingOpKind.DISK_USAGE: lambda: ("df", "-h"),
        StagingOpKind.FREE_MEMORY: lambda: ("free", "-m"),
        StagingOpKind.PROCESS_LIST: lambda: ("ps", "aux"),
        StagingOpKind.SERVICE_STATUS: lambda: _service_status(params.get("service", "")),
        StagingOpKind.TAIL_LOG: lambda: _tail_log(params.get("path", ""), params.get("lines", "50")),
        StagingOpKind.CAT_FILE: lambda: _cat_file(params.get("path", "")),
        StagingOpKind.CURL_HEALTH: lambda: _curl_health(params.get("url", "")),
    }
    argv = builders[op_kind]()
    return StagingOp(
        kind=op_kind,
        argv=tuple(argv),
        description=f"staging diagnostic: {op_kind.value}",
    )


def _service_status(service: str) -> tuple[str, ...]:
    if not service or not _safe_token(service):
        raise ValueError("service name required and must be a safe token")
    return ("systemctl", "status", service, "--no-pager")


def _tail_log(path: str, lines: str) -> tuple[str, ...]:
    if not path or not _safe_path(path):
        raise ValueError("log path required and must be a safe relative/absolute path")
    n = lines if str(lines).isdigit() else "50"
    return ("tail", "-n", n, path)


def _cat_file(path: str) -> tuple[str, ...]:
    if not path or not _safe_path(path):
        raise ValueError("file path required and must be a safe path")
    return ("cat", path)


def _curl_health(url: str) -> tuple[str, ...]:
    if not url.startswith(("http://", "https://")):
        raise ValueError("health URL must be http(s)")
    if any(c in url for c in (" ", ";", "|", "&", "`", "$", "\n")):
        raise ValueError("unsafe characters in URL")
    return ("curl", "-fsS", "--max-time", "10", url)


def _safe_token(value: str) -> bool:
    return bool(value) and value.replace("-", "").replace("_", "").replace(".", "").isalnum()


def _safe_path(value: str) -> bool:
    if not value or any(c in value for c in (";", "|", "&", "`", "$", "\n", "\r")):
        return False
    # Disallow parent traversal tricks in staging builders.
    if ".." in value.split("/"):
        return False
    return True


def argv_from_sequence(argv: Sequence[str]) -> list[str]:
    return [str(a) for a in argv]
