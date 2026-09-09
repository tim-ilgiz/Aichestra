"""Contract: Mode C uses one Orca Run + worktree adoption; no lead bypass."""

from __future__ import annotations

import sys
from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    OrchestratedWorkflow,
    Phase,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.orca import build_orca_argv, extract_worktree_locator
from aichestra.providers.base import ProviderTaskRequest, ProviderTaskResult
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


def test_one_run_id_reused_across_agent_phases(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            project_root=str(tmp_path),
            task_prompt="small fix",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    # Drive classify + implement only.
    assert wf.run_phase().status is PhaseStatus.SUCCEEDED
    run_id = wf.state.metadata["orca_run_id"]
    assert wf.run_phase().status is PhaseStatus.SUCCEEDED  # implement
    supervised = [
        req
        for req in orca.sent
        if (req.role or "") == "lead_implement"
    ]
    assert supervised
    assert all(req.context.get("run_id") == run_id for req in supervised)
    # phase_report must not look like ensure_run / run-create
    reports = [req for req in orca.sent if (req.role or "") == "phase_report"]
    assert reports
    assert all(req.context.get("run_id") == run_id for req in reports)


def test_medium_speckit_blocks_implement_until_brief_ready(tmp_path: Path) -> None:
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            project_root=str(tmp_path),
            task_prompt="refactor across 12 files in multi-package monorepo",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    assert wf.run_phase().status is PhaseStatus.SUCCEEDED  # classify
    # MEDIUM keeps research before implement.
    while wf.state.current_phase is not Phase.LEAD_IMPLEMENT:
        assert wf.run_phase().status is PhaseStatus.SUCCEEDED
    blocked = wf.run_phase()  # implement
    assert blocked is not None
    assert blocked.status is PhaseStatus.FAILED
    assert "Spec Kit" in blocked.detail
    assert "brief" in (blocked.result.get("pending_artifacts") or [])


def test_worktree_adoption_switches_effective_root(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()

    orca = fake_orca("success")
    original_send = orca.send

    def send_with_child(session, request):
        result = original_send(session, request)
        if (request.role or "") == "lead_implement" and result.ok:
            meta = dict(result.metadata)
            meta["worktree_path"] = str(child)
            meta["worktree_id"] = f"fake-repo::{child}"
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

    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            project_root=str(parent),
            task_prompt="implement",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[["true"]],
        ),
    )
    wf.run_phase()  # classify
    wf.run_phase()  # implement
    assert wf.state.metadata.get("orca_worktree_path") == str(child)
    assert wf._effective_project_root() == str(child)
    assert wf.state.metadata["orca_integration"]["policy"] == "adopt_child_worktree"
    assert wf.state.metadata["orca_integration"]["parent_project_root"] == str(parent)
