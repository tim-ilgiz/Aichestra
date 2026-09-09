"""Mode C workflow execution, phase status, and maintenance-reviewer gate."""

from __future__ import annotations

from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    OrchestratedWorkflow,
    Phase,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import FailureClass
from tests.fakes.providers import fake_codex, fake_local_worker, fake_orca


def test_phase_succeeded_only_after_operation_ok() -> None:
    lead = fake_codex("success")
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=lead,
            task_prompt="implement feature",
            maintenance_kwargs={"change_summary": "feature"},
        ),
    )
    outcome = wf.run_phase()  # classify
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    assert Phase.CLASSIFY.value in wf.state.completed
    assert wf.state.current_phase is Phase.LEAD_IMPLEMENT

    # Fail lead implement — must not mark completed.
    lead._execute_scenario = "error"
    failed = wf.run_phase()
    assert failed is not None
    assert failed.status is PhaseStatus.FAILED
    assert Phase.LEAD_IMPLEMENT.value not in wf.state.completed
    assert Phase.LEAD_IMPLEMENT.value in wf.state.failed
    assert wf.state.stopped is True


def test_maintenance_reviewer_gates_writers() -> None:
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            lead=fake_codex("success"),
            task_prompt="api change",
            # Skip running real maintenance by forcing writer without decision:
        ),
    )
    # Manually jump to TEST_WRITER without maintenance-reviewer success.
    wf.state.current_index = wf.state.phases.index(Phase.TEST_WRITER)
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "maintenance-reviewer gate" in outcome.detail


def test_mode_c_wires_providers_end_to_end(fixture_project_a: Path) -> None:
    orca = fake_orca("success")
    lead = fake_codex("success")
    worker = fake_local_worker("success")

    def research_fn(_state):
        from aichestra.orchestration.research_compact import research_paths

        return research_paths(
            fixture_project_a,
            query="README",
            local_worker=worker,
        )

    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            local_worker=worker,
            project_root=str(fixture_project_a),
            task_prompt="ship portable bootstrap",
            research_query="README",
            research_fn=research_fn,
            maintenance_kwargs={
                "change_summary": "bootstrap",
                "touches_behavior": False,
            },
        ),
    )
    state = wf.run_all()
    assert not state.failed
    assert Phase.RESEARCH.value in state.completed
    assert state.metadata["research"]["PROVIDER"] == "local-worker"
    assert state.metadata["research"]["QUERY"] == "README"
    assert orca.sent
    assert lead.sent
    assert worker.sent
    assert state.phase_outcomes[Phase.LEAD_IMPLEMENT.value].status is PhaseStatus.SUCCEEDED


def test_failed_provider_result_shape() -> None:
    from aichestra.providers.base import ProviderTaskRequest

    lead = fake_codex("quota")
    result = lead.execute_task(ProviderTaskRequest(prompt="x"))
    assert result.ok is False
    assert result.failure is FailureClass.QUOTA
