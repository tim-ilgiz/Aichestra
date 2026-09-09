"""Worktree / serial edit policy — refuse shared concurrent edits (FR-057).

Uses a project-local lock file so two ``orchestrate`` processes cannot edit the
same checkout concurrently. In-process registry remains as a fast path for
same-process detection.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class EditPolicy(str, Enum):
    SERIAL = "serial"
    WORKTREE = "worktree"
    REFUSED_SHARED = "refused_shared"


@dataclass(frozen=True)
class EditLease:
    policy: EditPolicy
    path: str
    agent_id: str
    allowed: bool
    reason: str


# In-process registry for concurrent-edit detection (tests / single process).
_ACTIVE_EDIT_ROOTS: dict[str, str] = {}


def _lock_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".aichestra" / "edit.lock"


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* appears to exist without signaling/terminating it.

    On Windows, ``os.kill(pid, 0)`` maps to ``TerminateProcess`` and must not be
    used for liveness checks (Python docs). Use ``OpenProcess`` instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Query Windows process existence without terminating the process."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # ERROR_ACCESS_DENIED (5) still means the PID exists.
    if kernel32.GetLastError() == 5:
        return True
    return False


def _read_lock(lock_file: Path) -> dict | None:
    try:
        raw = lock_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _try_acquire_file_lock(root: str, agent_id: str) -> EditLease | None:
    """Acquire cross-process lock. Returns lease, or None to fall through to refuse."""
    lock_file = _lock_path(root)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_id": agent_id,
        "pid": os.getpid(),
        "ts": time.time(),
    }

    for _ in range(2):
        existing = _read_lock(lock_file) if lock_file.exists() else None
        if existing:
            holder = str(existing.get("agent_id") or "")
            holder_pid = int(existing.get("pid") or 0)
            if holder == agent_id and holder_pid == os.getpid():
                return EditLease(
                    policy=EditPolicy.SERIAL,
                    path=root,
                    agent_id=agent_id,
                    allowed=True,
                    reason="serial edit lease re-entrant",
                )
            if holder_pid and not _pid_alive(holder_pid):
                try:
                    lock_file.unlink()
                except OSError:
                    pass
                existing = None
            elif holder and holder != agent_id:
                return EditLease(
                    policy=EditPolicy.REFUSED_SHARED,
                    path=root,
                    agent_id=agent_id,
                    allowed=False,
                    reason=(
                        f"shared concurrent edit refused; checkout held by {holder}. "
                        "Use an Orca worktree or wait for serial turn."
                    ),
                )

        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        except OSError as exc:
            return EditLease(
                policy=EditPolicy.REFUSED_SHARED,
                path=root,
                agent_id=agent_id,
                allowed=False,
                reason=f"edit lease lock failed: {exc}",
            )
        try:
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)
        return EditLease(
            policy=EditPolicy.SERIAL,
            path=root,
            agent_id=agent_id,
            allowed=True,
            reason="serial edit lease granted",
        )

    holder = "unknown"
    existing = _read_lock(lock_file)
    if existing and existing.get("agent_id"):
        holder = str(existing["agent_id"])
    return EditLease(
        policy=EditPolicy.REFUSED_SHARED,
        path=root,
        agent_id=agent_id,
        allowed=False,
        reason=(
            f"shared concurrent edit refused; checkout held by {holder}. "
            "Use an Orca worktree or wait for serial turn."
        ),
    )


def request_edit_lease(
    project_root: Path | str,
    agent_id: str,
    *,
    use_worktree: bool = False,
    worktree_path: Path | str | None = None,
) -> EditLease:
    """Grant serial or worktree edit; refuse silent shared checkout edits."""
    root = str(Path(project_root).resolve())
    if use_worktree:
        wt = str(Path(worktree_path or f"{root}.worktree-{agent_id}").resolve())
        if wt == root:
            return EditLease(
                policy=EditPolicy.REFUSED_SHARED,
                path=root,
                agent_id=agent_id,
                allowed=False,
                reason="worktree path must differ from primary checkout",
            )
        return EditLease(
            policy=EditPolicy.WORKTREE,
            path=wt,
            agent_id=agent_id,
            allowed=True,
            reason="isolated worktree edit allowed",
        )

    holder = _ACTIVE_EDIT_ROOTS.get(root)
    if holder and holder != agent_id:
        return EditLease(
            policy=EditPolicy.REFUSED_SHARED,
            path=root,
            agent_id=agent_id,
            allowed=False,
            reason=(
                f"shared concurrent edit refused; checkout held by {holder}. "
                "Use an Orca worktree or wait for serial turn."
            ),
        )

    lease = _try_acquire_file_lock(root, agent_id)
    if lease is None or not lease.allowed:
        return lease or EditLease(
            policy=EditPolicy.REFUSED_SHARED,
            path=root,
            agent_id=agent_id,
            allowed=False,
            reason="shared concurrent edit refused",
        )

    _ACTIVE_EDIT_ROOTS[root] = agent_id
    return lease


def release_edit_lease(project_root: Path | str, agent_id: str) -> None:
    root = str(Path(project_root).resolve())
    if _ACTIVE_EDIT_ROOTS.get(root) == agent_id:
        del _ACTIVE_EDIT_ROOTS[root]
    lock_file = _lock_path(root)
    existing = _read_lock(lock_file) if lock_file.exists() else None
    if existing and str(existing.get("agent_id") or "") == agent_id:
        try:
            lock_file.unlink()
        except OSError:
            pass


def reset_hard_helper_allowed() -> bool:
    """Never provide git reset --hard against the user's real working copy."""
    return False


def forbidden_recovery_commands() -> tuple[str, ...]:
    return ("git reset --hard",)
