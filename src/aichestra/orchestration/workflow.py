"""Default orchestrated workflow phases (FR-054) with real Mode C execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from aichestra.orchestration.change_signals import infer_change_signals
from aichestra.orchestration.maintenance_reviewer import (
    MaintenanceReviewDecision,
    review_change,
)
from aichestra.orchestration.modes import Mode, starts_full_orchestration
from aichestra.orchestration.research_compact import ResearchSummary, research_paths
from aichestra.orchestration.roles import select_lead
from aichestra.orchestration.verification import VerificationReport, run_verification
from aichestra.orchestration.worktrees import release_edit_lease, request_edit_lease
from aichestra.orchestration.writers import plan_doc_writes, plan_test_writes
from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
)


class Phase(str, Enum):
    CLASSIFY = "classify"
    RESEARCH = "research"
    LEAD_IMPLEMENT = "lead_implement"
    MAINTENANCE_REVIEW = "maintenance_review"
    TEST_WRITER = "test_writer"
    DOC_WRITER = "doc_writer"
    VERIFICATION = "verification"
    LEAD_REVIEW = "lead_review"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


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
class PhaseOutcome:
    phase: Phase
    status: PhaseStatus
    detail: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "status": self.status.value,
            "detail": self.detail,
            "result": dict(self.result),
        }


@dataclass
class WorkflowState:
    mode: Mode
    phases: list[Phase]
    current_index: int = 0
    decision: MaintenanceReviewDecision | None = None
    phase_outcomes: dict[str, PhaseOutcome] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    stopped: bool = False

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
            "phase_outcomes": {k: v.to_dict() for k, v in self.phase_outcomes.items()},
            "skipped": list(self.skipped),
            "completed": list(self.completed),
            "failed": list(self.failed),
            "stopped": self.stopped,
            "metadata": dict(self.metadata),
        }


@dataclass
class WorkflowBindings:
    """Injectable Mode C executors — fakes in CI, real adapters on operator machines."""

    orca: ProviderAdapter | None = None
    lead: ProviderAdapter | None = None
    local_worker: ProviderAdapter | None = None
    providers: list[ProviderStatus] = field(default_factory=list)
    project_root: str | None = None
    task_prompt: str = ""
    research_query: str = ""
    classify_fn: Callable[[WorkflowState], dict[str, Any]] | None = None
    research_fn: Callable[[WorkflowState], ResearchSummary] | None = None
    maintenance_kwargs: dict[str, Any] = field(default_factory=dict)
    verification_commands: list[list[str]] = field(default_factory=list)
    writer_fn: Callable[[WorkflowState, Phase], ProviderTaskResult] | None = None


class OrchestratedWorkflow:
    """Coordinates Mode C: real phase execution with explicit phase status."""

    def __init__(
        self,
        *,
        mode: Mode = Mode.ORCHESTRATED,
        research_useful: bool = True,
        bindings: WorkflowBindings | None = None,
    ) -> None:
        if not starts_full_orchestration(mode):
            raise ValueError(
                f"Orchestrated workflow requires Mode C; got {mode.value}"
            )
        self.bindings = bindings or WorkflowBindings()
        self.state = WorkflowState(
            mode=mode,
            phases=default_phases(research_useful=research_useful),
        )
        for phase in self.state.phases:
            self.state.phase_outcomes[phase.value] = PhaseOutcome(
                phase=phase, status=PhaseStatus.PENDING
            )

    def apply_maintenance_review(self, **kwargs: Any) -> MaintenanceReviewDecision:
        decision = review_change(**kwargs)
        self.state.decision = decision
        self.state.metadata["maintenance_review"] = decision.to_dict()
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

    def run_phase(self) -> PhaseOutcome | None:
        """Execute the current phase; mark succeeded only after real work ok."""
        if self.state.stopped:
            return None
        phase = self.state.current_phase
        if phase is None:
            return None

        # Writers are gated by a successful maintenance-reviewer phase (FR-023).
        if phase in {Phase.TEST_WRITER, Phase.DOC_WRITER}:
            gate = self._maintenance_gate_status()
            if gate is not PhaseStatus.SUCCEEDED:
                outcome = PhaseOutcome(
                    phase=phase,
                    status=PhaseStatus.FAILED,
                    detail=(
                        "maintenance-reviewer gate not satisfied before writer phase "
                        f"(gate={gate.value})"
                    ),
                )
                self._record(outcome, advance=False, stop=True)
                return outcome
            decision = self.state.decision
            assert decision is not None
            if phase is Phase.TEST_WRITER and not decision.needs_tests:
                outcome = PhaseOutcome(
                    phase=phase,
                    status=PhaseStatus.SKIPPED,
                    detail="maintenance-reviewer decided no tests",
                )
                self._record(outcome, advance=True)
                return outcome
            if phase is Phase.DOC_WRITER and not decision.needs_docs:
                outcome = PhaseOutcome(
                    phase=phase,
                    status=PhaseStatus.SKIPPED,
                    detail="maintenance-reviewer decided no docs",
                )
                self._record(outcome, advance=True)
                return outcome

        running = PhaseOutcome(phase=phase, status=PhaseStatus.RUNNING)
        self.state.phase_outcomes[phase.value] = running

        try:
            result = self._execute_phase(phase)
        except Exception as exc:  # noqa: BLE001 — surface as failed phase
            outcome = PhaseOutcome(
                phase=phase,
                status=PhaseStatus.FAILED,
                detail=str(exc),
            )
            self._record(outcome, advance=False, stop=True)
            return outcome

        if result.get("ok", False):
            outcome = PhaseOutcome(
                phase=phase,
                status=PhaseStatus.SUCCEEDED,
                detail=str(result.get("detail", "ok")),
                result=result,
            )
            self._record(outcome, advance=True)
            return outcome

        outcome = PhaseOutcome(
            phase=phase,
            status=PhaseStatus.FAILED,
            detail=str(result.get("detail", "phase failed")),
            result=result,
        )
        self._record(outcome, advance=False, stop=True)
        return outcome

    def advance(self) -> Phase | None:
        """Run the current phase once; return it only when it succeeded or skipped.

        Failed phases return None and stop the workflow so callers cannot treat
        failure as completion.
        """
        before = self.state.current_phase
        outcome = self.run_phase()
        if outcome is None:
            return None
        if outcome.status in {PhaseStatus.SUCCEEDED, PhaseStatus.SKIPPED}:
            return outcome.phase
        return None if before is not None else None

    def run_all(self) -> WorkflowState:
        while self.state.current_phase is not None and not self.state.stopped:
            self.run_phase()
        return self.state

    def _maintenance_gate_status(self) -> PhaseStatus:
        outcome = self.state.phase_outcomes.get(Phase.MAINTENANCE_REVIEW.value)
        if outcome is None:
            return PhaseStatus.PENDING
        if self.state.decision is None:
            return PhaseStatus.PENDING
        return outcome.status

    def _record(
        self,
        outcome: PhaseOutcome,
        *,
        advance: bool,
        stop: bool = False,
    ) -> None:
        self.state.phase_outcomes[outcome.phase.value] = outcome
        if outcome.status is PhaseStatus.SUCCEEDED:
            if outcome.phase.value not in self.state.completed:
                self.state.completed.append(outcome.phase.value)
        elif outcome.status is PhaseStatus.SKIPPED:
            if outcome.phase.value not in self.state.skipped:
                self.state.skipped.append(outcome.phase.value)
        elif outcome.status is PhaseStatus.FAILED:
            if outcome.phase.value not in self.state.failed:
                self.state.failed.append(outcome.phase.value)
        if advance:
            self.state.current_index += 1
        if stop:
            self.state.stopped = True

    def _execute_phase(self, phase: Phase) -> dict[str, Any]:
        bindings = self.bindings
        if phase is Phase.CLASSIFY:
            if bindings.classify_fn:
                data = bindings.classify_fn(self.state)
            else:
                data = {
                    "classification": "medium",
                    "prompt": bindings.task_prompt or "orchestrated task",
                }
            self.state.metadata["classify"] = data
            # Optional Orca control-plane session start for Mode C.
            if bindings.orca is not None:
                orca_result = bindings.orca.execute_task(
                    ProviderTaskRequest(
                        prompt=f"Mode C classify: {data}",
                        role="control_plane",
                        context=data,
                        timeout_seconds=60.0,
                        read_only=True,
                    )
                )
                self.state.metadata["orca_classify"] = orca_result.to_dict()
                if not orca_result.ok and orca_result.failure not in {
                    FailureClass.UNAVAILABLE,
                }:
                    return {
                        "ok": False,
                        "detail": orca_result.detail,
                        "provider": orca_result.to_dict(),
                    }
            return {"ok": True, "detail": "classified", **data}

        if phase is Phase.RESEARCH:
            if bindings.research_fn:
                summary = bindings.research_fn(self.state)
            else:
                root = bindings.project_root or "."
                summary = research_paths(
                    root,
                    query=bindings.research_query,
                    prefer_local_worker=True,
                    local_worker=bindings.local_worker,
                )
            payload = summary.to_dict()
            self.state.metadata["research"] = payload
            return {"ok": True, "detail": "research compacted", "research": payload}

        if phase is Phase.LEAD_IMPLEMENT:
            return self._run_lead(
                prompt=bindings.task_prompt or "Implement the classified task.",
                role="lead_implement",
            )

        if phase is Phase.MAINTENANCE_REVIEW:
            summary = (
                bindings.task_prompt
                or self.state.metadata.get("classify", {}).get("prompt", "change")
            )
            inferred = infer_change_signals(
                project_root=bindings.project_root,
                change_summary=summary,
            )
            # Explicit bindings override inference; unused diagnostic keys stay out.
            kwargs = {
                key: value
                for key, value in inferred.items()
                if key
                in {
                    "change_summary",
                    "touches_behavior",
                    "touches_public_api",
                    "existing_tests_cover",
                    "docs_stale",
                    "canonical_doc",
                    "risk",
                    "rationale",
                }
            }
            kwargs.update(bindings.maintenance_kwargs)
            kwargs.setdefault("change_summary", summary)
            self.state.metadata["change_signals"] = {
                "inferred": inferred,
                "effective": dict(kwargs),
            }
            decision = self.apply_maintenance_review(**kwargs)
            return {
                "ok": True,
                "detail": "maintenance-reviewer complete",
                "decision": decision.to_dict(),
                "change_signals": inferred,
            }

        if phase is Phase.TEST_WRITER:
            return self._run_writer(phase, plan_key="tests")

        if phase is Phase.DOC_WRITER:
            return self._run_writer(phase, plan_key="docs")

        if phase is Phase.VERIFICATION:
            commands = bindings.verification_commands
            if not commands:
                # Missing verify config is not a successful verification (FR-056).
                report = VerificationReport(results=[])
                self.state.metadata["verification"] = report.to_dict()
                return {
                    "ok": False,
                    "detail": (
                        "verification not configured: no verify commands in "
                        "project config"
                    ),
                    "verification": report.to_dict(),
                    "missing_verification": True,
                }
            report = run_verification(commands, cwd=bindings.project_root)
            self.state.metadata["verification"] = report.to_dict()
            return {
                "ok": report.ok,
                "detail": "verification ok" if report.ok else "verification failed",
                "verification": report.to_dict(),
            }

        if phase is Phase.LEAD_REVIEW:
            return self._run_lead(
                prompt="Review verification results and summarize outcome.",
                role="lead_review",
                context={"verification": self.state.metadata.get("verification", {})},
            )

        return {"ok": False, "detail": f"unknown phase {phase.value}"}

    def _run_writer(self, phase: Phase, *, plan_key: str) -> dict[str, Any]:
        plans = self.writer_plans()
        plan = plans.get(plan_key, {})
        action = plan.get("action") if isinstance(plan, dict) else None
        if action == "skip":
            return {
                "ok": True,
                "detail": f"{phase.value} skipped by plan",
                "plan": plan,
            }
        if self.bindings.writer_fn is None:
            # Required writer work without an executor is a hard block, not success.
            return {
                "ok": False,
                "detail": (
                    f"{phase.value} required (action={action or 'unknown'}) but no "
                    "writer executor bound; inject WorkflowBindings.writer_fn"
                ),
                "plan": plan,
                "failure": FailureClass.UNAVAILABLE.value,
            }

        writer_fn = self.bindings.writer_fn
        root = self.bindings.project_root
        agent_id = f"writer-{phase.value}-{id(self)}"
        if root:
            lease = request_edit_lease(root, agent_id)
            self.state.metadata["edit_lease"] = {
                "role": phase.value,
                "agent_id": agent_id,
                "allowed": lease.allowed,
                "policy": lease.policy.value,
                "path": lease.path,
                "reason": lease.reason,
            }
            if not lease.allowed:
                return {
                    "ok": False,
                    "detail": lease.reason,
                    "plan": plan,
                    "failure": FailureClass.ERROR.value,
                    "edit_lease": self.state.metadata["edit_lease"],
                }
            try:
                result = writer_fn(self.state, phase)
            finally:
                release_edit_lease(root, agent_id)
        else:
            result = writer_fn(self.state, phase)
        return {
            "ok": result.ok,
            "detail": result.detail,
            "plan": plan,
            "provider": result.to_dict(),
        }

    def _run_lead(
        self,
        *,
        prompt: str,
        role: str,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        lead = self.bindings.lead
        if lead is None and self.bindings.providers:
            selection = select_lead(self.bindings.providers)
            self.state.metadata["lead_selection"] = {
                "lead": selection.lead.value if selection.lead else None,
                "reason": selection.reason,
            }
            if selection.lead is None:
                return {
                    "ok": False,
                    "detail": selection.reason,
                    "failure": FailureClass.UNAVAILABLE.value,
                }
            return {
                "ok": False,
                "detail": (
                    f"lead {selection.lead.value} selected but no executable "
                    "adapter bound; inject WorkflowBindings.lead"
                ),
                "failure": FailureClass.UNAVAILABLE.value,
            }
        if lead is None:
            return {
                "ok": False,
                "detail": "no lead provider bound for Mode C execution",
                "failure": FailureClass.UNAVAILABLE.value,
            }
        ctx = dict(context or {})
        if "research" in self.state.metadata:
            ctx.setdefault("research", self.state.metadata["research"])

        needs_edit = role == "lead_implement"

        def _execute(cwd: str | None) -> ProviderTaskResult:
            return lead.execute_task(
                ProviderTaskRequest(
                    prompt=prompt,
                    role=role,
                    context=ctx,
                    cwd=cwd,
                    timeout_seconds=300.0,
                    read_only=not needs_edit,
                )
            )

        if needs_edit and self.bindings.project_root:
            raw = self._with_edit_lease(
                agent_id=f"lead-{role}-{id(self)}",
                role=role,
                run=_execute,
            )
            if isinstance(raw, dict):
                return raw
            result = raw
        else:
            result = _execute(self.bindings.project_root)

        key = f"lead_{role}"
        self.state.metadata[key] = result.to_dict()
        return {
            "ok": result.ok,
            "detail": result.detail,
            "provider": result.to_dict(),
            "kind": lead.kind.value,
        }

    def _with_edit_lease(
        self,
        *,
        agent_id: str,
        role: str,
        run: Callable[[str | None], ProviderTaskResult],
    ) -> ProviderTaskResult | dict[str, Any]:
        root = self.bindings.project_root
        if not root:
            return run(None)
        lease = request_edit_lease(root, agent_id)
        self.state.metadata["edit_lease"] = {
            "role": role,
            "agent_id": agent_id,
            "allowed": lease.allowed,
            "policy": lease.policy.value,
            "path": lease.path,
            "reason": lease.reason,
        }
        if not lease.allowed:
            return {
                "ok": False,
                "detail": lease.reason,
                "failure": FailureClass.ERROR.value,
                "edit_lease": self.state.metadata["edit_lease"],
            }
        try:
            return run(lease.path)
        finally:
            release_edit_lease(root, agent_id)


def bound_writer_from_lead(
    lead: ProviderAdapter,
    *,
    project_root: str | None = None,
) -> Callable[[WorkflowState, Phase], ProviderTaskResult]:
    """Default writer executor: run required test/doc writes through the lead."""

    def _writer(state: WorkflowState, phase: Phase) -> ProviderTaskResult:
        decision = state.decision
        plan_hint = ""
        if decision is not None:
            if phase is Phase.TEST_WRITER:
                plan_hint = (
                    f"TEST_DECISION={decision.TEST_DECISION}; "
                    f"TEST_SCOPE={list(decision.TEST_SCOPE)}"
                )
            elif phase is Phase.DOC_WRITER:
                plan_hint = (
                    f"DOC_DECISION={decision.DOC_DECISION}; "
                    f"DOC_TARGETS={list(decision.DOC_TARGETS)}"
                )
        prompt = (
            f"Mode C {phase.value}: implement the required writer work. {plan_hint}"
        ).strip()
        return lead.execute_task(
            ProviderTaskRequest(
                prompt=prompt,
                role=phase.value,
                context={"phase": phase.value, "plan_hint": plan_hint},
                cwd=project_root,
                timeout_seconds=300.0,
                read_only=False,
            )
        )

    return _writer


def run_phase_hooks(
    workflow: OrchestratedWorkflow,
    hooks: dict[Phase, Callable[[WorkflowState], None]] | None = None,
) -> WorkflowState:
    """Run all phases; optional hooks fire only after a phase succeeds/skips."""
    hooks = hooks or {}
    while workflow.state.current_phase is not None and not workflow.state.stopped:
        outcome = workflow.run_phase()
        if outcome is None:
            break
        if outcome.status in {PhaseStatus.SUCCEEDED, PhaseStatus.SKIPPED}:
            hook = hooks.get(outcome.phase)
            if hook:
                hook(workflow.state)
        if outcome.status is PhaseStatus.FAILED:
            break
    return workflow.state
