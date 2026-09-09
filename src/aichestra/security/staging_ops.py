"""Typed staging diagnostic builders — structured argv, not free-form shell.

OpenSSH reconstructs the remote command and the remote user's shell parses it.
Typed parameters therefore use a strict *allowlist* grammar (not a growing
blacklist) and remote argv is POSIX-quoted before SSH transport.
"""

from __future__ import annotations

import re
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


# Strict allowlist grammars for typed parameters (Constitution IV / FR-032/059).
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_PATH_RE = re.compile(r"^/?[A-Za-z0-9._+-]+(/[A-Za-z0-9._+-]+)*$")
_URL_RE = re.compile(
    r"^https?://[A-Za-z0-9][A-Za-z0-9.-]{0,253}"
    r"(:[0-9]{1,5})?"
    r"(/[A-Za-z0-9._~/-]{0,512})?$"
)
_LINES_RE = re.compile(r"^[1-9][0-9]{0,4}$")

# Diagnostic file reads only — never private keys / SSH material (FR-033/035).
_CAT_ALLOWED_EXACT = frozenset(
    {
        "/etc/os-release",
        "/etc/hostname",
        "/etc/issue",
        "/proc/uptime",
        "/proc/meminfo",
        "/proc/loadavg",
        "/proc/version",
        "/proc/cpuinfo",
    }
)
_CAT_ALLOWED_PREFIXES = (
    "/var/log/",
    "/var/log/journal/",
    "/run/log/",
)
_CAT_DENIED_NAME_RE = re.compile(
    r"(?i)(^|/)("
    r"\.ssh|"
    r"id_[a-z0-9]+|"
    r".*\.pem|"
    r".*\.key|"
    r"authorized_keys|"
    r"known_hosts|"
    r"private[_-]?key|"
    r".*_rsa|"
    r".*_ed25519|"
    r".*_ecdsa"
    r")(/|$)"
)


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
        StagingOpKind.TAIL_LOG: lambda: _tail_log(
            params.get("path", ""), params.get("lines", "50")
        ),
        StagingOpKind.CAT_FILE: lambda: _cat_file(params.get("path", "")),
        StagingOpKind.CURL_HEALTH: lambda: _curl_health(params.get("url", "")),
    }
    argv = builders[op_kind]()
    return StagingOp(
        kind=op_kind,
        argv=tuple(argv),
        description=f"staging diagnostic: {op_kind.value}",
    )


def posix_single_quote(value: str) -> str:
    """POSIX shell single-quote encoding for one remote argument."""
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def quote_remote_argv(argv: Sequence[str]) -> str:
    """Join argv into one remote command string safe for the remote shell."""
    return " ".join(posix_single_quote(a) for a in argv)


def _service_status(service: str) -> tuple[str, ...]:
    if not _allow_service(service):
        raise ValueError(
            "service name required and must match allowlist "
            "[A-Za-z0-9][A-Za-z0-9._@-]*"
        )
    return ("systemctl", "status", service, "--no-pager")


def _tail_log(path: str, lines: str) -> tuple[str, ...]:
    if not _allow_path(path):
        raise ValueError(
            "log path required and must match allowlist path grammar "
            "(no shell metacharacters, no '..')"
        )
    if not _allow_diagnostic_file(path):
        raise ValueError(
            "log path not on staging diagnostic allowlist "
            "(private keys / SSH material and non-diagnostic paths denied)"
        )
    if not _allow_lines(str(lines)):
        raise ValueError("lines must be a positive integer (1-99999)")
    script = (
        'p="$1"; n="$2"; '
        'if [ -L "$p" ] || [ -h "$p" ]; then '
        'echo "aichestra: symlink refused" >&2; exit 1; '
        "fi; "
        'exec tail -n "$n" -- "$p"'
    )
    return ("sh", "-c", script, "aichestra-tail", path, str(lines))


def _cat_file(path: str) -> tuple[str, ...]:
    if not _allow_path(path):
        raise ValueError(
            "file path required and must match allowlist path grammar "
            "(no shell metacharacters, no '..')"
        )
    if not _allow_diagnostic_file(path):
        raise ValueError(
            "file path not on staging diagnostic allowlist "
            "(private keys / SSH material and non-diagnostic paths denied)"
        )
    # Refuse symlink bypass: resolve only if not a symlink, then cat.
    # ``sh`` is allowlisted only for this fixed aichestra-cat wrapper.
    script = (
        'p="$1"; '
        'if [ -L "$p" ] || [ -h "$p" ]; then '
        'echo "aichestra: symlink refused" >&2; exit 1; '
        "fi; "
        'exec cat -- "$p"'
    )
    return ("sh", "-c", script, "aichestra-cat", path)


def _curl_health(url: str) -> tuple[str, ...]:
    if not _allow_url(url):
        raise ValueError(
            "health URL must be http(s) and match allowlist host/path grammar"
        )
    # ``-q`` / ``--disable`` MUST be first so user ``.curlrc`` cannot force POST
    # or other mutating defaults (FR-033/034).
    return ("curl", "-q", "-fsS", "--max-time", "10", "--get", url)


def _allow_service(value: str) -> bool:
    return bool(value) and bool(_SERVICE_RE.fullmatch(value))


def _allow_path(value: str) -> bool:
    if not value or not _PATH_RE.fullmatch(value):
        return False
    # Defense in depth: never allow parent traversal segments.
    return ".." not in value.split("/")


def _allow_diagnostic_file(value: str) -> bool:
    """Allow only known diagnostic paths; never private key / SSH material."""
    if not value or not _allow_path(value):
        return False
    if _CAT_DENIED_NAME_RE.search(value):
        return False
    if value in _CAT_ALLOWED_EXACT:
        return True
    return any(value.startswith(prefix) for prefix in _CAT_ALLOWED_PREFIXES)


def _allow_lines(value: str) -> bool:
    return bool(_LINES_RE.fullmatch(value))


def _allow_url(value: str) -> bool:
    return bool(value) and bool(_URL_RE.fullmatch(value))


def argv_from_sequence(argv: Sequence[str]) -> list[str]:
    return [str(a) for a in argv]
