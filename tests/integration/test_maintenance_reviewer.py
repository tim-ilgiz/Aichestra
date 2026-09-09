"""Integration: maintenance-reviewer and writer skip behavior."""

from __future__ import annotations

from aichestra.orchestration.maintenance_reviewer import review_change
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import OrchestratedWorkflow, Phase
from aichestra.orchestration.writers import plan_doc_writes, plan_test_writes


def test_reviewer_none_none() -> None:
    decision = review_change(change_summary="comment typo")
    assert decision.TEST_DECISION == "none"
    assert decision.DOC_DECISION == "none"
    assert decision.needs_tests is False
    assert decision.needs_docs is False
    data = decision.to_dict()
    for key in (
        "TEST_DECISION",
        "TEST_SCOPE",
        "DOC_DECISION",
        "DOC_TARGETS",
        "ADR_REQUIRED",
        "SPEC_UPDATE",
        "RATIONALE",
    ):
        assert key in data


def test_reviewer_tests_and_docs_needed() -> None:
    decision = review_change(
        change_summary="new public API",
        touches_behavior=True,
        touches_public_api=True,
        existing_tests_cover=False,
        canonical_doc="README.md",
        risk="high",
    )
    assert decision.TEST_DECISION == "required"
    assert decision.DOC_DECISION == "update_canonical"
    assert decision.DOC_TARGETS == ["README.md"]
    assert decision.ADR_REQUIRED is True
    assert decision.SPEC_UPDATE == "update-living-spec"


def test_reviewer_existing_coverage_is_none() -> None:
    decision = review_change(
        change_summary="behavior already covered",
        touches_behavior=True,
        existing_tests_cover=True,
        risk="medium",
    )
    assert decision.TEST_DECISION == "none"


def test_reviewer_high_risk_without_behavior_skips_spec() -> None:
    decision = review_change(
        change_summary="noise",
        touches_behavior=False,
        touches_public_api=False,
        risk="high",
    )
    assert decision.SPEC_UPDATE == "none"
    assert decision.ADR_REQUIRED is False


def test_writers_skip_on_none() -> None:
    tests = plan_test_writes(test_decision="none")
    docs = plan_doc_writes(doc_decision="none")
    assert tests.action == "skip"
    assert docs.action == "skip"


def test_workflow_skips_writers_when_none(tmp_path) -> None:
    import sys

    from aichestra.orchestration.workflow import PhaseStatus, WorkflowBindings
    from tests.fakes.providers import fake_codex, fake_orca

    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            project_root=str(tmp_path),
            task_prompt="noop typo",
            maintenance_kwargs={"change_summary": "noop"},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    # SMALL/none path skips writer phases entirely (or marks them skipped if present).
    assert Phase.TEST_WRITER.value not in state.completed
    assert Phase.DOC_WRITER.value not in state.completed
    assert Phase.TEST_WRITER.value not in state.failed
    assert Phase.DOC_WRITER.value not in state.failed
    assert Phase.VERIFICATION.value in state.completed
    assert state.phase_outcomes[Phase.MAINTENANCE_REVIEW.value].status is PhaseStatus.SUCCEEDED
    assert Phase.LEAD_IMPLEMENT.value in state.completed
