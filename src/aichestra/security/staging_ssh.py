"""Staging SSH diagnostics — alias resolution + allowlist-gated remote runner.

Never:
- accept unrestricted remote shell strings from the LLM
- read or copy private key contents
- support production SSH
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from aichestra.config.layering import load_json, machine_local_path, resolve_config
from aichestra.security.sanitize import sanitize_text
from aichestra.security.staging_allowlist import (
    PRODUCTION_SSH_SUPPORTED,
    StagingDecision,
    evaluate_command,
)
from aichestra.security.staging_ops import StagingOp, build_op


@dataclass(frozen=True)
class StagingAlias:
    name: str
    host: str
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None  # path only; never read key contents
    environment: str = "staging"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "identity_file": self.identity_file,
            "environment": self.environment,
        }


@dataclass(frozen=True)
class StagingRunResult:
    ok: bool
    alias: str
    decision: StagingDecision
    argv_remote: tuple[str, ...]
    ssh_argv: tuple[str, ...]
    stdout: str
    stderr: str
    sanitized_for_cloud: str
    exit_code: int | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "alias": self.alias,
            "decision": self.decision.to_dict(),
            "argv_remote": list(self.argv_remote),
            "ssh_argv": list(self.ssh_argv),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "sanitized_for_cloud": self.sanitized_for_cloud,
            "exit_code": self.exit_code,
            "error": self.error,
            "production_ssh_supported": PRODUCTION_SSH_SUPPORTED,
        }


class UnknownStagingAliasError(LookupError):
    """Fail-closed when a staging alias is not configured."""


def load_staging_aliases(
    *,
    repo_root: Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, StagingAlias]:
    """Load aliases from machine-local / resolved config (never from secrets files)."""
    cfg = config if config is not None else resolve_config(repo_root=repo_root)
    raw = cfg.get("staging", {}).get("aliases", {})
    if not isinstance(raw, dict):
        return {}
    aliases: dict[str, StagingAlias] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        host = str(value.get("host") or "").strip()
        if not host:
            continue
        env = str(value.get("environment") or "staging").strip().lower()
        if env in {"production", "prod", "prd"}:
            # Structurally refuse production aliases.
            continue
        aliases[str(name)] = StagingAlias(
            name=str(name),
            host=host,
            user=(str(value["user"]) if value.get("user") else None),
            port=int(value["port"]) if value.get("port") else None,
            identity_file=(
                str(value["identity_file"]) if value.get("identity_file") else None
            ),
            environment="staging",
        )
    return aliases


def resolve_staging_alias(
    alias_name: str,
    *,
    repo_root: Path | None = None,
    config: dict[str, Any] | None = None,
) -> StagingAlias:
    name = alias_name.strip()
    aliases = load_staging_aliases(repo_root=repo_root, config=config)
    if name not in aliases:
        configured = ", ".join(sorted(aliases)) or "(none)"
        raise UnknownStagingAliasError(
            f"Unknown staging SSH alias {name!r}. Fail closed. "
            f"Configured aliases: {configured}. "
            f"Add machine-local staging.aliases in {machine_local_path(repo_root)} "
            "(do not commit). Ask the operator for the correct STAGING alias."
        )
    return aliases[name]


def build_ssh_argv(alias: StagingAlias, remote_argv: Sequence[str]) -> list[str]:
    """Build OpenSSH argv array. Never embeds a free-form remote shell string."""
    ssh = shutil.which("ssh") or "ssh"
    argv: list[str] = [ssh, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    if alias.port:
        argv.extend(["-p", str(alias.port)])
    if alias.identity_file:
        # Pass path only — never open/read the private key file contents.
        path = Path(alias.identity_file)
        if path.is_file():
            argv.extend(["-i", str(path)])
    target = f"{alias.user}@{alias.host}" if alias.user else alias.host
    argv.append(target)
    # Pass remote command as discrete argv tokens to ssh (no shell joining).
    argv.extend(str(a) for a in remote_argv)
    return argv


def run_staging_diagnostic(
    *,
    alias_name: str,
    op: StagingOp | None = None,
    op_kind: str | None = None,
    op_params: dict[str, str] | None = None,
    remote_argv: Sequence[str] | None = None,
    allow_raw_argv: bool = False,
    repo_root: Path | None = None,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
    executor: Any | None = None,
) -> StagingRunResult:
    """Resolve alias → allowlist → optional SSH exec → sanitize for cloud handoff.

    Operator/public path is typed ops only (``op`` / ``op_kind``).
    ``remote_argv`` is test-only and requires ``allow_raw_argv=True``.

    ``executor`` is an optional callable(ssh_argv) -> CompletedProcess-like for tests.
    """
    alias = resolve_staging_alias(alias_name, repo_root=repo_root, config=config)

    if op is None:
        if op_kind:
            op = build_op(op_kind, **(op_params or {}))
            cmd = op.to_argv()
        elif remote_argv is not None:
            if not allow_raw_argv:
                raise ValueError(
                    "Free-form remote_argv is test-only; use typed op/op_kind "
                    "(FR-059). Pass allow_raw_argv=True only from fixtures."
                )
            cmd = [str(a) for a in remote_argv]
        else:
            raise ValueError("Provide op or op_kind")
    else:
        cmd = op.to_argv()

    decision = evaluate_command(cmd, environment=alias.environment)
    ssh_argv = build_ssh_argv(alias, decision.argv) if decision.allowed else ()

    if not decision.allowed:
        return StagingRunResult(
            ok=False,
            alias=alias.name,
            decision=decision,
            argv_remote=tuple(cmd),
            ssh_argv=tuple(ssh_argv),
            stdout="",
            stderr="",
            sanitized_for_cloud="",
            exit_code=None,
            error=decision.reason,
        )

    if dry_run:
        return StagingRunResult(
            ok=True,
            alias=alias.name,
            decision=decision,
            argv_remote=tuple(decision.argv),
            ssh_argv=tuple(ssh_argv),
            stdout="",
            stderr="",
            sanitized_for_cloud="",
            exit_code=None,
            error=None,
        )

    run = executor or _default_executor
    try:
        completed = run(list(ssh_argv))
    except FileNotFoundError as exc:
        return StagingRunResult(
            ok=False,
            alias=alias.name,
            decision=decision,
            argv_remote=tuple(decision.argv),
            ssh_argv=tuple(ssh_argv),
            stdout="",
            stderr="",
            sanitized_for_cloud="",
            exit_code=None,
            error=f"ssh client missing: {exc}",
        )
    except OSError as exc:
        return StagingRunResult(
            ok=False,
            alias=alias.name,
            decision=decision,
            argv_remote=tuple(decision.argv),
            ssh_argv=tuple(ssh_argv),
            stdout="",
            stderr="",
            sanitized_for_cloud="",
            exit_code=None,
            error=str(exc),
        )

    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    code = getattr(completed, "returncode", None)
    combined = stdout + ("\n" + stderr if stderr else "")
    sanitized = sanitize_text(combined)
    return StagingRunResult(
        ok=code == 0,
        alias=alias.name,
        decision=decision,
        argv_remote=tuple(decision.argv),
        ssh_argv=tuple(ssh_argv),
        stdout=stdout,
        stderr=stderr,
        sanitized_for_cloud=sanitized,
        exit_code=code,
        error=None if code == 0 else "remote diagnostic failed",
    )


def _default_executor(ssh_argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ssh_argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def staging_configured(repo_root: Path | None = None) -> bool:
    return bool(load_staging_aliases(repo_root=repo_root))


# Re-export for discovery without importing private key loaders.
__all__ = [
    "UnknownStagingAliasError",
    "StagingAlias",
    "StagingRunResult",
    "build_ssh_argv",
    "load_staging_aliases",
    "load_json",
    "resolve_staging_alias",
    "run_staging_diagnostic",
    "staging_configured",
    "PRODUCTION_SSH_SUPPORTED",
]
