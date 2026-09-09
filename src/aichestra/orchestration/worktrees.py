"""Worktree / serial edit policy — refuse shared concurrent edits (FR-057)."""

from __future__ import annotations

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
    _ACTIVE_EDIT_ROOTS[root] = agent_id
    return EditLease(
        policy=EditPolicy.SERIAL,
        path=root,
        agent_id=agent_id,
        allowed=True,
        reason="serial edit lease granted",
    )


def release_edit_lease(project_root: Path | str, agent_id: str) -> None:
    root = str(Path(project_root).resolve())
    if _ACTIVE_EDIT_ROOTS.get(root) == agent_id:
        del _ACTIVE_EDIT_ROOTS[root]


def reset_hard_helper_allowed() -> bool:
    """Never provide git reset --hard against the user's real working copy."""
    return False


def forbidden_recovery_commands() -> tuple[str, ...]:
    return ("git reset --hard",)
