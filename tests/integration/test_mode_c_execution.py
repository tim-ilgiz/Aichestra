"""Mode C thin run controller: one Orca Run, no direct provider fan-out."""

from __future__ import annotations

import sys
from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    ModeCRunController,
    Phase,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import FailureClass
from tests.fakes.providers import fake_codex, fake_local_worker, fake_orca


def test_phase_succeeded_only_after_operation_ok() -> None:
    """Orca error on implement must fail the phase — no lead bypass."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            task_prompt="implement feature",
            project_root="/tmp/mode-c-proj",
            maintenance_kwargs={"change_summary": "feature"},
        ),
    )
    outcome = wf.run_phase()  # classify
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    assert Phase.CLASSIFY.value in wf.state.completed
    assert wf.state.current_phase is Phase.LEAD_IMPLEMENT
    assert wf.state.metadata.get("orca_run_id")
    assert wf.state.metadata.get("canonical_orchestration") == "orca_run"

    # Fail Orca implement — must not mark completed and must not call lead.
    orca._execute_scenario = "error"
    lead_sent_before = len(lead.sent)
    failed = wf.run_phase()
    assert failed is not None
    assert failed.status is PhaseStatus.FAILED
    assert Phase.LEAD_IMPLEMENT.value not in wf.state.completed
    assert Phase.LEAD_IMPLEMENT.value in wf.state.failed
    assert wf.state.stopped is True
    assert len(lead.sent) == lead_sent_before


def test_mode_c_without_orca_fails_at_classify() -> None:
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            lead=fake_codex("success"),
            task_prompt="implement feature",
            project_root="/tmp/mode-c-proj",
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "Orca" in outcome.detail
    assert outcome.result.get("failure") == FailureClass.UNAVAILABLE.value


def test_mode_c_orca_unavailable_does_not_fallback_to_lead() -> None:
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("unavailable"),
            lead=lead,
            task_prompt="implement feature",
            project_root="/tmp/mode-c-proj",
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert lead.sent == []


def test_mode_c_resume_run_id() -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            task_prompt="resume me",
            project_root="/tmp/mode-c-proj",
            resume_run_id="existing-orca-run-42",
            maintenance_kwargs={"change_summary": "x"},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    assert wf.state.metadata["orca_run_id"] == "existing-orca-run-42"
    assert wf.state.metadata.get("orca_run_resumed") is True
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    assert wf.state.metadata["orca_run_id"] == "existing-orca-run-42"
    # ensure_run should reuse — no second identity.
    ensure_roles = [r.role for r in orca.sent if (r.role or "") == "ensure_run"]
    assert ensure_roles == []


def test_maintenance_reviewer_gates_writers() -> None:
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            task_prompt="api change",
            project_root="/tmp/mode-c-proj",
        ),
    )
    # Manually jump to TEST_WRITER without maintenance-reviewer success.
    wf.state.current_index = wf.state.phases.index(Phase.TEST_WRITER)
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "maintenance-reviewer gate" in outcome.detail


def test_mode_c_wires_orca_only_end_to_end(fixture_project_a: Path) -> None:
    """Mode C agents go through Orca; lead/local_worker are not direct executors."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    worker = fake_local_worker("success")

    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            local_worker=worker,
            project_root=str(fixture_project_a),
            task_prompt="ship portable bootstrap across 12 files in multi-package monorepo",
            research_query="README",
            maintenance_kwargs={
                "change_summary": "bootstrap",
                "touches_behavior": False,
            },
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    # MEDIUM Spec Kit: satisfy brief/plan gate for this contract path.
    outcome = wf.run_phase()  # classify
    assert outcome is not None and outcome.status is PhaseStatus.SUCCEEDED
    wf.state.metadata["brief_satisfied"] = True
    if wf.state.metadata.get("plan_required"):
        wf.state.metadata["plan_satisfied"] = True
    state = wf.run_all()
    assert not state.failed, state.failed
    assert Phase.RESEARCH.value in state.completed
    assert state.metadata["research"]["via"] == "orca"
    assert state.metadata["research"]["QUERY"] == "README"
    assert orca.sent
    # Mode C must not call lead or local_worker adapters directly.
    assert lead.sent == []
    assert worker.sent == []
    assert state.metadata.get("orca_run_id")
    agent_roles = {
        (req.role or "")
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
    }
    assert "research" in agent_roles
    assert "lead_implement" in agent_roles
    run_ids = {
        (req.context or {}).get("run_id")
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
        and (req.context or {}).get("run_id")
    }
    assert run_ids == {state.metadata["orca_run_id"]}
