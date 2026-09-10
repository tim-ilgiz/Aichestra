"""Opaque cleanup leases for Aichestra-owned prove-launch bridge terminals.

Coordinator abort-launch must not accept a raw Orca terminal handle. After a
successful owned bridge proof, Aichestra stores ``lease → terminal_handle``
under the untracked machine-local tree and returns only the lease.

Lease files are a three-state machine:

- available: ``{token}``
- claimed: ``{token}.claimed`` (abort in progress; retriable)
- consumed: file gone

Abort claims, then either restores (probe/close failed) or consumes (terminal
closed, or Orca proved the handle is already bound to a Dispatch). Successful
Dispatch may absorb an *available* lease without closing. Existing-terminal
launches never receive a lease.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Mapping

LEASE_DIRNAME = "launch-cleanup"
CLAIMED_SUFFIX = ".claimed"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def lease_dir(repo_root: Path | str) -> Path:
    return Path(repo_root).resolve() / ".local" / LEASE_DIRNAME


def _available_path(directory: Path, token: str) -> Path:
    return directory / token


def _claimed_path(directory: Path, token: str) -> Path:
    return directory / f"{token}{CLAIMED_SUFFIX}"


def _read_owned_handle(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("owns_terminal") is not True:
        return None
    handle = data.get("terminal_handle")
    if not isinstance(handle, str) or not handle.strip():
        return None
    return handle.strip()


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def issue_cleanup_lease(handle: str, *, repo_root: Path | str) -> str:
    """Record an owned bridge handle and return an opaque cleanup lease."""
    owned = str(handle or "").strip()
    if not owned:
        raise ValueError("cleanup lease requires an owned terminal handle")
    directory = lease_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    payload = json.dumps(
        {"terminal_handle": owned, "owns_terminal": True},
        separators=(",", ":"),
    )
    for _ in range(8):
        token = secrets.token_hex(32)
        path = _available_path(directory, token)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        return token
    raise RuntimeError("could not allocate cleanup lease")


def claim_cleanup_lease(token: str, *, repo_root: Path | str) -> str | None:
    """Move available → claimed (or resume an existing claim). Return handle."""
    lease = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(lease):
        return None
    directory = lease_dir(repo_root)
    available = _available_path(directory, lease)
    claimed = _claimed_path(directory, lease)
    try:
        os.rename(available, claimed)
    except OSError:
        if not claimed.is_file():
            return None
    handle = _read_owned_handle(claimed)
    if handle is None:
        _discard(claimed)
        return None
    return handle


def restore_cleanup_lease(token: str, *, repo_root: Path | str) -> bool:
    """Move claimed → available so abort can be retried."""
    lease = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(lease):
        return False
    directory = lease_dir(repo_root)
    available = _available_path(directory, lease)
    claimed = _claimed_path(directory, lease)
    try:
        os.rename(claimed, available)
    except OSError:
        return False
    return True


def consume_claimed_lease(token: str, *, repo_root: Path | str) -> bool:
    """Drop a claimed lease (terminal closed or already Dispatched)."""
    lease = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(lease):
        return False
    claimed = _claimed_path(lease_dir(repo_root), lease)
    if not claimed.is_file():
        return False
    _discard(claimed)
    return True


def consume_cleanup_lease(token: str, *, repo_root: Path | str) -> str | None:
    """Absorb an *available* lease. Return the owned handle, or None.

    Does not steal a claimed lease: abort-launch owns that state until it
    restores or consumes the claim. Dispatch success therefore cannot yank a
    lease out from under an in-flight abort.
    """
    lease = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(lease):
        return None
    directory = lease_dir(repo_root)
    src = _available_path(directory, lease)
    dest = directory / f".{lease}.{secrets.token_hex(8)}.taking"
    try:
        os.rename(src, dest)
    except OSError:
        return None
    handle = _read_owned_handle(dest)
    _discard(dest)
    return handle


def cleanup_lease_from_prepared(payload: Mapping[str, Any] | None) -> str | None:
    """Extract the opaque lease from a coordinator prepared_launch payload."""
    if not isinstance(payload, Mapping):
        return None
    cleanup = payload.get("cleanup")
    if not isinstance(cleanup, Mapping):
        return None
    lease = cleanup.get("lease")
    if isinstance(lease, str) and lease.strip():
        return lease.strip()
    invocation = cleanup.get("invocation")
    if not isinstance(invocation, Mapping):
        return None
    args = [str(item) for item in list(invocation.get("args") or ())]
    if "--token" not in args:
        return None
    index = args.index("--token")
    if index + 1 >= len(args):
        return None
    token = args[index + 1].strip()
    return token or None


def cleanup_repo_root_from_prepared(payload: Mapping[str, Any] | None) -> Path | None:
    """Repo root encoded in prove-launch cleanup invocation, if present."""
    if not isinstance(payload, Mapping):
        return None
    cleanup = payload.get("cleanup")
    if not isinstance(cleanup, Mapping):
        return None
    invocation = cleanup.get("invocation")
    if not isinstance(invocation, Mapping):
        return None
    args = [str(item) for item in list(invocation.get("args") or ())]
    if "--repo-root" not in args:
        return None
    index = args.index("--repo-root")
    if index + 1 >= len(args):
        return None
    raw = args[index + 1].strip()
    return Path(raw) if raw else None
