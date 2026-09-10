"""Opaque cleanup leases for Aichestra-owned prove-launch bridge terminals.

Coordinator abort-launch must not accept a raw Orca terminal handle. After a
successful owned bridge proof, Aichestra stores ``lease → terminal_handle``
under the untracked machine-local tree and returns only the lease. Abort
atomically consumes that lease, then closes the stored handle. Successful
Dispatch consumes the same lease without closing, so a late abort cannot
kill a running worker. Existing-terminal launches never receive a lease.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Mapping

LEASE_DIRNAME = "launch-cleanup"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def lease_dir(repo_root: Path | str) -> Path:
    return Path(repo_root).resolve() / ".local" / LEASE_DIRNAME


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
        path = directory / token
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        return token
    raise RuntimeError("could not allocate cleanup lease")


def consume_cleanup_lease(token: str, *, repo_root: Path | str) -> str | None:
    """Atomically take a lease. Return the owned handle, or None if unknown."""
    lease = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(lease):
        return None
    directory = lease_dir(repo_root)
    src = directory / lease
    dest = directory / f".{lease}.{secrets.token_hex(8)}.taking"
    try:
        os.rename(src, dest)
    except OSError:
        return None
    try:
        raw = dest.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        data = None
    try:
        dest.unlink()
    except OSError:
        pass
    if not isinstance(data, dict) or data.get("owns_terminal") is not True:
        return None
    handle = data.get("terminal_handle")
    if not isinstance(handle, str) or not handle.strip():
        return None
    return handle.strip()


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
