"""Staging-only allowlist — deny dangerous commands BEFORE execution (FR-030–033)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from aichestra.security.staging_ops import StagingOp, StagingOpKind

# No production SSH integration path exists in this module or package.
PRODUCTION_SSH_SUPPORTED = False

_DENIED_TOKENS = frozenset(
    {
        "sudo",
        "rm",
        "kill",
        "reboot",
        "shutdown",
        "halt",
        "poweroff",
        "chmod",
        "chown",
        "mkfs",
        "dd",
        "passwd",
        "useradd",
        "userdel",
        "visudo",
    }
)

_DENIED_PHRASES = (
    "docker stop",
    "docker rm",
    "docker kill",
    "kubectl apply",
    "kubectl delete",
    "systemctl restart",
    "systemctl stop",
    "service restart",
    "service stop",
    "apt install",
    "apt-get install",
    "yum install",
    "dnf install",
    "brew install",
    "choco install",
    "winget install",
    "npm install -g",
    "pip install",
)

_ALLOWED_BINARIES = frozenset(
    {
        "uptime",
        "df",
        "free",
        "ps",
        "systemctl",
        "tail",
        "cat",
        "curl",
        "head",
        "wc",
        "uname",
        "hostname",
        "id",
        "whoami",
        "ls",
    }
)

_ALLOWED_SYSTEMCTL_SUBCOMMANDS = frozenset({"status", "is-active", "is-enabled", "show"})

# curl may only be used for GET-style health checks (typed CURL_HEALTH).
_DENIED_CURL_FLAGS = frozenset(
    {
        "-o",
        "-O",
        "--output",
        "--remote-name",
        "-d",
        "--data",
        "--data-raw",
        "--data-binary",
        "--data-urlencode",
        "-F",
        "--form",
        "-T",
        "--upload-file",
        "-X",
        "--request",
        "--json",
    }
)
_DENIED_CURL_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE", "CONNECT", "TRACE"})

_DENIED_REDIRECT_TOKENS = frozenset({">", ">>", ">&", "&>", "<", "<<", "<<<"})


@dataclass(frozen=True)
class StagingDecision:
    allowed: bool
    reason: str
    argv: tuple[str, ...]
    environment: str  # staging | production | unknown

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "argv": list(self.argv),
            "environment": self.environment,
        }


def assert_staging_environment(environment: str) -> None:
    env = environment.strip().lower()
    if env in {"production", "prod", "prd"}:
        raise PermissionError(
            "Production SSH integration is not supported (FR-031). "
            "Only STAGING diagnostics are available."
        )
    if env not in {"staging", "stage", "stg"}:
        raise PermissionError(
            f"Unknown or unsupported environment {environment!r}; fail closed. "
            "Only STAGING diagnostics are integrated."
        )


def evaluate_command(
    argv: Sequence[str] | StagingOp,
    *,
    environment: str = "staging",
) -> StagingDecision:
    """Allowlist/deny before any remote execution."""
    if isinstance(argv, StagingOp):
        cmd = argv.to_argv()
    else:
        cmd = [str(a) for a in argv]

    try:
        assert_staging_environment(environment)
    except PermissionError as exc:
        return StagingDecision(
            allowed=False,
            reason=str(exc),
            argv=tuple(cmd),
            environment=environment.strip().lower() or "unknown",
        )

    if not cmd:
        return StagingDecision(
            allowed=False,
            reason="empty command denied",
            argv=(),
            environment="staging",
        )

    joined = " ".join(cmd).lower()
    for phrase in _DENIED_PHRASES:
        if phrase in joined:
            return StagingDecision(
                allowed=False,
                reason=f"denied dangerous pattern before execution: {phrase}",
                argv=tuple(cmd),
                environment="staging",
            )

    for token in cmd:
        base = token.split("/")[-1].lower()
        if base in _DENIED_TOKENS or token.lower() in _DENIED_TOKENS:
            return StagingDecision(
                allowed=False,
                reason=f"denied dangerous token before execution: {base}",
                argv=tuple(cmd),
                environment="staging",
            )

    binary = cmd[0].split("/")[-1].lower()
    if binary not in _ALLOWED_BINARIES:
        return StagingDecision(
            allowed=False,
            reason=f"binary not on staging allowlist: {binary}",
            argv=tuple(cmd),
            environment="staging",
        )

    if binary == "systemctl":
        sub = cmd[1].lower() if len(cmd) > 1 else ""
        if sub not in _ALLOWED_SYSTEMCTL_SUBCOMMANDS:
            return StagingDecision(
                allowed=False,
                reason=f"systemctl subcommand not read-only allowlisted: {sub}",
                argv=tuple(cmd),
                environment="staging",
            )

    # Reject shell metacharacters / redirects even inside args.
    for token in cmd:
        if token in _DENIED_REDIRECT_TOKENS or token.startswith(">"):
            return StagingDecision(
                allowed=False,
                reason="shell redirection denied before execution",
                argv=tuple(cmd),
                environment="staging",
            )
        if any(c in token for c in (";", "|", "&", "`", "\n", "$(", "${", ">")):
            return StagingDecision(
                allowed=False,
                reason="shell metacharacters denied before execution",
                argv=tuple(cmd),
                environment="staging",
            )

    if binary == "curl":
        curl_deny = _evaluate_curl_readonly(cmd)
        if curl_deny is not None:
            return curl_deny

    return StagingDecision(
        allowed=True,
        reason="allowlisted staging diagnostic",
        argv=tuple(cmd),
        environment="staging",
    )


def _evaluate_curl_readonly(cmd: list[str]) -> StagingDecision | None:
    """Deny curl write / mutating forms before any remote execution."""
    i = 1
    while i < len(cmd):
        token = cmd[i]
        flag = token.split("=", 1)[0]
        if flag in _DENIED_CURL_FLAGS or token in _DENIED_CURL_FLAGS:
            return StagingDecision(
                allowed=False,
                reason=f"curl write/mutate flag denied before execution: {flag}",
                argv=tuple(cmd),
                environment="staging",
            )
        if flag in {"-X", "--request"}:
            method = ""
            if "=" in token:
                method = token.split("=", 1)[1].upper()
            elif i + 1 < len(cmd):
                method = cmd[i + 1].upper()
            if method in _DENIED_CURL_METHODS:
                return StagingDecision(
                    allowed=False,
                    reason=f"curl method denied before execution: {method}",
                    argv=tuple(cmd),
                    environment="staging",
                )
        # Combined short flags like -fsSO
        if token.startswith("-") and not token.startswith("--"):
            body = token[1:]
            if "o" in body or "O" in body or "d" in body or "F" in body or "T" in body:
                return StagingDecision(
                    allowed=False,
                    reason=f"curl write flag denied before execution: {token}",
                    argv=tuple(cmd),
                    environment="staging",
                )
        i += 1
    return None


def evaluate_staging_op(op: StagingOp, *, environment: str = "staging") -> StagingDecision:
    if op.kind not in StagingOpKind:
        return StagingDecision(
            allowed=False,
            reason="unknown staging op kind",
            argv=tuple(op.argv),
            environment=environment,
        )
    return evaluate_command(op, environment=environment)
