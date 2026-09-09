"""Mode C thin run controller: one Orca Run handoff, no direct provider fan-out."""

from __future__ import annotations

import sys
from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    GateKind,
    GateStatus,
    ModeCRunController,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import FailureClass
from tests.fakes.providers import fake_codex, fake_local_worker, fake_orca


def test_handoff_failed_does_not_complete_or_bypass_lead(tmp_path: Path) -> None:
    """Orca error on mode_c_handoff must fail — no lead bypass."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            orca=orca,
            providers=[(lead).probe()],
            task_prompt="implement feature",
            project_root=str(proj),
            maintenance_kwargs={"change_summary": "feature"},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    assert wf.state.metadata.get("orca_run_id")
    assert wf.state.metadata.get("canonical_orchestration") == "orca_run"

    orca._execute_scenario = "error"
    lead_sent_before = len(lead.sent)
    state = wf.run_all()
    assert state.failed
    assert GateKind.ORCA_HANDOFF.value not in state.completed
    assert len(lead.sent) == lead_sent_before


def test_mode_c_without_orca_fails_at_validate(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            providers=[(fake_codex("success")).probe()],
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
        bindings=WorkflowBindings(
            orca=fake_orca("unavailable"),
            providers=[(lead).probe()],
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
        bindings=WorkflowBindings(
            orca=orca,
            providers=[(fake_codex("success")).probe()],
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
    ensure_roles = [r.role for r in orca.sent if (r.role or "") == "ensure_run"]
    assert ensure_roles == []


def test_maintenance_is_gate_not_writer_scheduler(tmp_path: Path) -> None:
    """Maintenance decision is recorded; Aichestra does not schedule writers."""
    proj = tmp_path / "proj"
    proj.mkdir()
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            task_prompt="api change",
            project_root=str(proj),
            maintenance_kwargs={
                "change_summary": "api",
                "touches_behavior": True,
                "touches_public_api": True,
            },
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert GateKind.MAINTENANCE.value in state.completed
    assert state.decision is not None
    roles = {(r.role or "") for r in orca.sent}
    assert "mode_c_writers" not in roles
    assert "test_writer" not in roles
    assert "doc_writer" not in roles
    assert "maintenance_gate_for_orca" in state.metadata


def test_mode_c_wires_orca_only_end_to_end(fixture_project_a: Path) -> None:
    """Mode C handoff goes through Orca; lead/local_worker are not direct executors."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    worker = fake_local_worker("success")

    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            orca=orca,
            providers=[(lead).probe()] + [p.probe() for p in [(worker)] if p is not None],

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
    assert state.metadata.get("orchestration_shape_agnostic") is True
    assert state.metadata.get("workflow_owner") == "orca"
    assert orca.sent
    assert lead.sent == []
    assert worker.sent == []
    assert state.metadata.get("orca_run_id")
    agent_roles = {
        (req.role or "")
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
    }
    assert MODE_C_HANDOFF_ROLE in agent_roles
    assert "research" not in agent_roles
    assert "mode_c_agents" not in agent_roles
    assert "lead_review" not in agent_roles
    run_ids = {
        (req.context or {}).get("run_id")
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
        and (req.context or {}).get("run_id")
    }
    assert run_ids == {state.metadata["orca_run_id"]}
    # Aichestra must not create competing Spec Kit artifacts for this run.
    assert "speckit_artifacts" not in agent_roles
    assert state.metadata.get("speckit_policy_only") is True
    assert GateKind.PROJECT_CONTEXT.value in state.completed
    assert GateStatus.SUCCEEDED is state.gate_outcomes[GateKind.ORCA_HANDOFF.value].status
