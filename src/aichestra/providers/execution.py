"""Shared Mode C CLI execution helper — opt-in; never wraps native entrypoints."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Sequence

from aichestra.providers.base import (
    FailureClass,
    ProviderSession,
    ProviderTaskRequest,
    ProviderTaskResult,
)


def run_cli_task(
    *,
    binary: str | None,
    argv: Sequence[str],
    session: ProviderSession,
    request: ProviderTaskRequest,
    unavailable_detail: str = "provider binary unavailable",
) -> ProviderTaskResult:
    """Run a one-shot provider CLI argv and map exit/timeout to FailureClass."""
    if not binary:
        return ProviderTaskResult(
            ok=False,
            failure=FailureClass.UNAVAILABLE,
            detail=unavailable_detail,
            session_id=session.session_id,
        )

    cwd = request.cwd
    if cwd is not None and not Path(cwd).is_dir():
        return ProviderTaskResult(
            ok=False,
            failure=FailureClass.ERROR,
            detail=f"cwd does not exist: {cwd}",
            session_id=session.session_id,
        )

    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(request.timeout_seconds)),
            cwd=cwd,
        )
    except FileNotFoundError:
        return ProviderTaskResult(
            ok=False,
            failure=FailureClass.UNAVAILABLE,
            detail=f"binary not found: {binary}",
            session_id=session.session_id,
        )
    except subprocess.TimeoutExpired:
        return ProviderTaskResult(
            ok=False,
            failure=FailureClass.TIMEOUT,
            detail=f"provider timed out after {request.timeout_seconds}s",
            session_id=session.session_id,
        )
    except OSError as exc:
        return ProviderTaskResult(
            ok=False,
            failure=FailureClass.ERROR,
            detail=str(exc),
            session_id=session.session_id,
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    output = (stdout + ("\n" + stderr if stderr else "")).strip()
    if completed.returncode == 0:
        return ProviderTaskResult(
            ok=True,
            output=output,
            failure=FailureClass.NONE,
            detail="ok",
            session_id=session.session_id,
            metadata={"exit_code": 0, "argv0": argv[0] if argv else binary},
        )

    failure = FailureClass.ERROR
    lower = (stderr + stdout).lower()
    if "quota" in lower or "rate limit" in lower:
        failure = FailureClass.QUOTA
    elif "auth" in lower or "login" in lower or "unauthorized" in lower:
        failure = FailureClass.AUTH
    return ProviderTaskResult(
        ok=False,
        output=output,
        failure=failure,
        detail=f"exit code {completed.returncode}",
        session_id=session.session_id,
        metadata={"exit_code": completed.returncode},
    )


ArgvBuilder = Callable[[str, ProviderTaskRequest], list[str]]
