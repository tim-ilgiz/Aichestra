"""Default orchestrated workflow phases (FR-054)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from aichestra.orchestration.maintenance_reviewer import (
    MaintenanceReviewDecision,
    review_change,
)
from aichestra.orchestration.modes import Mode, starts_full_orchestration
from aichestra.orchestration.writers import plan_doc_writes, plan_test_writes


class Phase(str, Enum):
    CLASSIFY = "classify"
    RESEARCH = "research"
    LEAD_IMPLEMENT = "lead_implement"
    MAINTENANCE_REVIEW = "maintenance_review"
    TEST_WRITER = "test_writer"
    DOC_WRITER = "doc_writer"
    VERIFICATION = "verification"
    LEAD_REVIEW = "lead_review"


def default_phases(*, research_useful: bool = True) -> list[Phase]:
    phases = [Phase.CLASSIFY]
    if research_useful:
        phases.append(Phase.RESEARCH)
    phases.extend(
        [
            Phase.LEAD_IMPLEMENT,
            Phase.MAINTENANCE_REVIEW,
            Phase.TEST_WRITER,
            Phase.DOC_WRITER,
            Phase.VERIFICATION,
            Phase.LEAD_REVIEW,
        ]
    )
    return phases


@dataclass
class WorkflowState:
    mode: Mode
    phases: list[Phase]
    current_index: int = 0
    decision: MaintenanceReviewDecision | None = None
    skipped: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def current_phase(self) -> Phase | None:
        if 0 <= self.current_index < len(self.phases):
            return self.phases[self.current_index]
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "phases": [p.value for p in self.phases],
            "current_index": self.current_index,
            "current_phase": self.current_phase.value if self.current_phase else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "skipped": list(self.skipped),
            "completed": list(self.completed),
            "metadata": dict(self.metadata),
        }


class OrchestratedWorkflow:
    """Coordinates the default orchestrated flow; tests/docs are conditional."""

    def __init__(
        self,
        *,
        mode: Mode = Mode.ORCHESTRATED,
        research_useful: bool = True,
    ) -> None:
        if not starts_full_orchestration(mode):
            raise ValueError(
                f"Orchestrated workflow requires Mode C; got {mode.value}"
            )
        self.state = WorkflowState(
            mode=mode,
            phases=default_phases(research_useful=research_useful),
        )

    def advance(self) -> Phase | None:
        phase = self.state.current_phase
        if phase is None:
            return None
        # Skip writers when maintenance-reviewer said none.
        if phase is Phase.TEST_WRITER and self.state.decision and not self.state.decision.needs_tests:
            self.state.skipped.append(phase.value)
            self.state.current_index += 1
            return self.advance()
        if phase is Phase.DOC_WRITER and self.state.decision and not self.state.decision.needs_docs:
            self.state.skipped.append(phase.value)
            self.state.current_index += 1
            return self.advance()
        self.state.completed.append(phase.value)
        self.state.current_index += 1
        return phase

    def apply_maintenance_review(self, **kwargs: Any) -> MaintenanceReviewDecision:
        decision = review_change(**kwargs)
        self.state.decision = decision
        return decision

    def writer_plans(self) -> dict[str, Any]:
        decision = self.state.decision or review_change(change_summary="unset")
        return {
            "tests": plan_test_writes(
                test_decision=decision.TEST_DECISION,
                test_scope=decision.TEST_SCOPE,
            ).__dict__,
            "docs": plan_doc_writes(
                doc_decision=decision.DOC_DECISION,
                doc_targets=decision.DOC_TARGETS,
            ).__dict__,
        }


def run_phase_hooks(
    workflow: OrchestratedWorkflow,
    hooks: dict[Phase, Callable[[WorkflowState], None]] | None = None,
) -> WorkflowState:
    """Advance through all phases, invoking optional hooks."""
    hooks = hooks or {}
    while workflow.state.current_phase is not None:
        phase = workflow.state.current_phase
        # Peek skip logic via advance
        before = workflow.state.current_index
        executed = workflow.advance()
        if executed is None:
            break
        hook = hooks.get(executed)
        if hook:
            hook(workflow.state)
        if workflow.state.current_index == before:
            break
    return workflow.state
