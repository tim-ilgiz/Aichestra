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


def test_phase_succeeded_only_after_operation_ok(tmp_path: Path) -> None:
    """Orca error on mode_c_agents must fail — no lead bypass."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            task_prompt="implement feature",
            project_root=str(proj),
            maintenance_kwargs={"change_summary": "feature"},
        ),
    )
    outcome = wf.run_phase()  # classify
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    assert Phase.CLASSIFY.value in wf.state.completed
    assert wf.state.metadata.get("orca_run_id")
    assert wf.state.metadata.get("canonical_orchestration") == "orca_run"

    # Fail Orca agents — must not mark completed and must not call lead.
    orca._execute_scenario = "error"
    lead_sent_before = len(lead.sent)
    state = wf.run_all()
    assert state.failed
    assert Phase.LEAD_IMPLEMENT.value not in state.completed
    assert len(lead.sent) == lead_sent_before


def test_mode_c_without_orca_fails_at_classify(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            lead=fake_codex("success"),
            task_prompt="implement feature",
            project_root=str(proj),
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "Orca" in outcome.detail
    assert outcome.result.get("failure") == FailureClass.UNAVAILABLE.value


def test_mode_c_orca_unavailable_does_not_fallback_to_lead(tmp_path: Path) -> None:
    lead = fake_codex("success")
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("unavailable"),
            lead=lead,
            task_prompt="implement feature",
            project_root=str(proj),
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert lead.sent == []


def test_mode_c_resume_run_id(tmp_path: Path) -> None:
    orca = fake_orca("success")
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            task_prompt="resume me",
            project_root=str(proj),
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


def test_maintenance_reviewer_gates_writers(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            task_prompt="api change",
            project_root=str(proj),
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
    state = wf.run_all()
    assert not state.failed, state.failed
    assert state.metadata.get("thin_coordinator") is True
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
    assert "mode_c_agents" in agent_roles
    assert "lead_review" in agent_roles
    run_ids = {
        (req.context or {}).get("run_id")
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
        and (req.context or {}).get("run_id")
    }
    assert run_ids == {state.metadata["orca_run_id"]}
    # Spec Kit MEDIUM artifacts must be real files (no metadata hacks).
    spec_dir = Path(fixture_project_a) / ".aichestra" / "speckit"
    assert (spec_dir / "brief.md").is_file()
    assert (spec_dir / "plan.md").is_file()
