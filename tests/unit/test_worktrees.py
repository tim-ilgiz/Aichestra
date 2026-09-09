"""Worktree / serial edit policy tests (FR-057)."""

from __future__ import annotations

from aichestra.orchestration.worktrees import (
    forbidden_recovery_commands,
    release_edit_lease,
    request_edit_lease,
    reset_hard_helper_allowed,
)


def test_reset_hard_helper_forbidden() -> None:
    assert reset_hard_helper_allowed() is False
    assert "git reset --hard" in forbidden_recovery_commands()


def test_shared_checkout_refused(tmp_path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    first = request_edit_lease(root, "agent-a")
    assert first.allowed is True
    second = request_edit_lease(root, "agent-b")
    assert second.allowed is False
    assert "shared concurrent" in second.reason.lower()
    release_edit_lease(root, "agent-a")
    third = request_edit_lease(root, "agent-b")
    assert third.allowed is True
    release_edit_lease(root, "agent-b")


def test_worktree_isolation_allowed(tmp_path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    wt = tmp_path / "proj-wt-b"
    lease = request_edit_lease(
        root, "agent-b", use_worktree=True, worktree_path=wt
    )
    assert lease.allowed is True
    assert lease.path == str(wt.resolve())
