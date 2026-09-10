"""Contract: Mode C uses one Orca Run + worktree adoption; no lead bypass."""

from __future__ import annotations

from tests.fakes.providers import fake_execution_targets

import sys
from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    GateKind,
    ModeCRunController,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import ProviderTaskResult
from aichestra.providers.orca import build_orca_argv, extract_worktree_locator
from aichestra.providers.base import ProviderTaskRequest
from tests.fakes.providers import fake_codex, fake_orca


def test_supervised_argv_is_worker_start_not_worktree_create() -> None:
    req = ProviderTaskRequest(
        prompt="implement feature",
        role="lead_implement",
        context={"task_id": "task_1", "run_id": "run_1"},
    )
    argv = build_orca_argv("/bin/orca", req, session_id="abcd1234-ffff")
    assert argv[1:3] == ["orchestration", "worker-start"]
    assert "--worktree" in argv


def test_ensure_run_argv_is_run_create() -> None:
    req = ProviderTaskRequest(prompt="Mode C", role="ensure_run", read_only=True)
    argv = build_orca_argv("/bin/orca", req, session_id="abcd1234-ffff")
    assert argv[1:3] == ["orchestration", "run-create"]


def test_extract_worktree_locator_from_receipt() -> None:
    locator = extract_worktree_locator(
        {
            "result": {
                "effects": {
                    "worktree": {
                        "path": "/tmp/child",
                        "id": "repo::/tmp/child",
                    }
                }
            }
        }
    )
    assert locator["worktree_path"] == "/tmp/child"
    assert locator["worktree_id"] == "repo::/tmp/child"


def test_one_run_id_reused_across_handoff(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            project_root=str(tmp_path),
            task_prompt="small fix",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    run_id = state.metadata["orca_run_id"]
    supervised = [
        req
        for req in orca.sent
        if (req.role or "") == MODE_C_HANDOFF_ROLE
    ]
    assert len(supervised) == 1
    assert all(req.context.get("run_id") == run_id for req in supervised)
    assert orca.run_creates == 1


def test_speckit_policy_without_competing_tree(tmp_path: Path) -> None:
    """MEDIUM scale is policy for Orca; Aichestra does not write .aichestra/speckit/."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            project_root=str(tmp_path),
            task_prompt="refactor across 12 files in multi-package monorepo",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert (state.metadata.get("speckit_path") or {}).get("scale") == "medium"
    assert state.metadata.get("speckit_policy_only") is True
    assert not (tmp_path / ".aichestra" / "speckit").exists()
    assert "speckit_artifacts" not in {(r.role or "") for r in orca.sent}
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    assert (handoff.context or {}).get("speckit_scale") == "medium"


def test_run_phase_is_early_validate_only(tmp_path: Path) -> None:
    """run_phase validates root/Orca/bootstrap; does not create a Run or schedule agents."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            project_root=str(tmp_path),
            task_prompt="small fix",
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    assert not wf.state.metadata.get("orca_run_id")
    assert orca.sent == []
    assert not any((r.role or "") == MODE_C_HANDOFF_ROLE for r in orca.sent)


def test_run_phase_without_bootstrap_does_not_create_run(tmp_path: Path) -> None:
    """Empty ExecutionTargets fail closed in run_phase without run-create."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=(),
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            project_root=str(tmp_path),
            task_prompt="no target",
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "bootstrap" in outcome.detail.lower() or "ExecutionTarget" in outcome.detail
    assert not wf.state.metadata.get("orca_run_id")
    assert orca.sent == []


def test_worktree_adoption_switches_effective_root(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()

    orca = fake_orca("success")
    original_send = orca.send

    def send_with_child(session, request):
        if (request.role or "") == MODE_C_HANDOFF_ROLE and isinstance(request.context, dict):
            request.context["worktree_path"] = str(child)
            request.context["worktree_id"] = f"fake-repo::{child}"
            request.context["integration_policy"] = "adopt_child_worktree"
        result = original_send(session, request)
        if (request.role or "") == MODE_C_HANDOFF_ROLE and result.ok:
            meta = dict(result.metadata)
            meta["worktree_path"] = str(child)
            meta["worktree_id"] = f"fake-repo::{child}"
            meta["integration_policy"] = "adopt_child_worktree"
            return ProviderTaskResult(
                ok=True,
                output=result.output,
                failure=result.failure,
                detail=result.detail,
                session_id=result.session_id,
                metadata=meta,
            )
        return result

    orca.send = send_with_child  # type: ignore[method-assign]

    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            project_root=str(parent),
            task_prompt="implement",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert wf.state.metadata.get("orca_worktree_path") == str(child.resolve())
    assert wf._effective_project_root() == str(child.resolve())
    assert wf.state.metadata["orca_integration"]["policy"] == "adopt_child_worktree"
    assert wf.state.metadata["orca_integration"]["parent_project_root"] == str(parent)
    assert GateKind.VERIFICATION.value in state.completed
    assert state.metadata.get("verification_cwd") == str(child.resolve())
