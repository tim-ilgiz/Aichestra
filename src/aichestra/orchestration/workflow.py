"""Default orchestrated workflow phases (FR-054) with real Mode C execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from aichestra.orchestration.change_signals import _is_test_path, infer_change_signals
from aichestra.orchestration.factory_preserve import detect_factory_tooling
from aichestra.orchestration.handoff import build_handoff_packet, prepare_manual_handoff
from aichestra.orchestration.maintenance_reviewer import (
    MaintenanceReviewDecision,
    review_change,
)
from aichestra.orchestration.media_routing import route_media
from aichestra.orchestration.modes import Mode, starts_full_orchestration
from aichestra.orchestration.research_compact import ResearchSummary, research_paths
from aichestra.orchestration.roles import select_lead
from aichestra.orchestration.speckit_policy import (
    SpecKitPath,
    SpecKitScale,
    classify_speckit_scale,
)
from aichestra.orchestration.verification import VerificationReport, run_verification
from aichestra.orchestration.worktrees import release_edit_lease, request_edit_lease
from aichestra.orchestration.writers import plan_doc_writes, plan_test_writes
from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
)
from aichestra.security.sanitize import sanitize_mapping


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


_SECURITY_HINTS = (
    "security",
    "auth",
    "crypto",
    "secret",
    "permission",
    "ssh",
    "token",
)
_MONEY_HINTS = ("payment", "billing", "money", "invoice", "checkout")
_FULL_SDD_HINTS = (
    "full sdd",
    "spec kit",
    "specify",
    "full specification",
    "large high risk",
)
_SCOPE_HINTS = (
    "multi-package",
    "multi package",
    "across packages",
    "monorepo",
    "refactor",
    "migrate",
)


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


def phases_for_scale(scale: SpecKitScale) -> list[Phase]:
    """Rebuild workflow phases for Spec Kit proportionality."""
    if scale is SpecKitScale.SMALL:
        return [
            Phase.CLASSIFY,
            Phase.LEAD_IMPLEMENT,
            Phase.MAINTENANCE_REVIEW,
            Phase.VERIFICATION,
            Phase.LEAD_REVIEW,
        ]
    # MEDIUM and LARGE keep research + writers (gated) on the default path.
    return default_phases(research_useful=True)


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
    attachments: tuple[str, ...] = ()
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
        self.state.metadata["task_prompt"] = self.bindings.task_prompt
        self.state.metadata["research_useful_default"] = research_useful
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
        signals = self.state.metadata.get("change_signals") or {}
        changed_paths = list(
            (signals.get("inferred") or {}).get("changed_paths")
            or (signals.get("effective") or {}).get("changed_paths")
            or []
        )
        existing_test_files = [p for p in changed_paths if _is_test_path(p)]
        return {
            "tests": plan_test_writes(
                test_decision=decision.TEST_DECISION,
                test_scope=decision.TEST_SCOPE,
                existing_test_files=existing_test_files,
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
            if phase is not Phase.CLASSIFY:
                self._report_orca_phase(phase, outcome)
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
        # Release serial edit lease when verification finishes or workflow stops
        # after implement started (lease held across implement → verification).
        if outcome.phase is Phase.VERIFICATION or (
            stop and self.state.metadata.get("edit_lease_held")
        ):
            self._release_serial_edit_lease()

    def _execute_phase(self, phase: Phase) -> dict[str, Any]:
        bindings = self.bindings
        if phase is Phase.CLASSIFY:
            return self._phase_classify()

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
            return self._phase_lead_implement()

        if phase is Phase.MAINTENANCE_REVIEW:
            return self._phase_maintenance_review()

        if phase is Phase.TEST_WRITER:
            return self._run_writer(phase, plan_key="tests")

        if phase is Phase.DOC_WRITER:
            return self._run_writer(phase, plan_key="docs")

        if phase is Phase.VERIFICATION:
            return self._phase_verification()

        if phase is Phase.LEAD_REVIEW:
            return self._phase_lead_review()

        return {"ok": False, "detail": f"unknown phase {phase.value}"}

    def _phase_classify(self) -> dict[str, Any]:
        bindings = self.bindings
        prompt = bindings.task_prompt or "orchestrated task"
        self.state.metadata["task_prompt"] = prompt

        if bindings.project_root:
            factory = detect_factory_tooling(bindings.project_root)
            self.state.metadata["factory"] = {
                "present": factory.present,
                "markers": list(factory.markers),
                "may_delete": factory.may_delete,
                "may_migrate": factory.may_migrate,
                "reason": factory.reason,
            }

        attachment_routing = self._route_attachments(prompt=prompt)
        if attachment_routing is not None:
            self.state.metadata["media_routing"] = attachment_routing

        if bindings.classify_fn:
            data = bindings.classify_fn(self.state)
            path = self._speckit_from_classify_data(data, prompt=prompt)
        else:
            heuristics = _prompt_heuristics(prompt)
            path = classify_speckit_scale(**heuristics)
            data = {
                "classification": path.scale.value,
                "prompt": prompt,
                "heuristics": heuristics,
                "speckit_path": {
                    "scale": path.scale.value,
                    "steps": list(path.steps),
                    "rationale": path.rationale,
                },
            }

        self.state.metadata["classify"] = data
        self.state.metadata["speckit_path"] = {
            "scale": path.scale.value,
            "steps": list(path.steps),
            "rationale": path.rationale,
        }
        if path.scale is SpecKitScale.MEDIUM:
            self.state.metadata["brief"] = {
                "required": True,
                "status": "recorded",
                "summary": prompt[:500],
            }
        if path.scale is SpecKitScale.LARGE_HIGH_RISK:
            self.state.metadata["plan_required"] = True
            self.state.metadata["brief_required"] = True
            self.state.metadata["plan"] = {
                "required": True,
                "status": "recorded",
                "note": "LARGE path requires plan/brief before implement",
                "summary": prompt[:500],
            }

        self._apply_scale_phases(path.scale)

        if bindings.orca is not None:
            if not bindings.project_root:
                self.state.metadata["orca_skipped_reason"] = "no_project_root"
            else:
                ctx = sanitize_mapping(
                    {
                        **data,
                        "attachments": list(bindings.attachments or []),
                    }
                )
                orca_result = bindings.orca.execute_task(
                    ProviderTaskRequest(
                        prompt=f"Mode C classify: {data.get('classification', path.scale.value)}",
                        role="control_plane",
                        context=ctx,
                        cwd=bindings.project_root,
                        timeout_seconds=60.0,
                        read_only=True,
                        attachments=tuple(bindings.attachments or ()),
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

        classify_outcome = PhaseOutcome(
            phase=Phase.CLASSIFY,
            status=PhaseStatus.SUCCEEDED,
            detail="classified",
            result={"ok": True, "detail": "classified", **data},
        )
        self._report_orca_phase(Phase.CLASSIFY, classify_outcome)
        return {"ok": True, "detail": "classified", **data}

    def _phase_lead_implement(self) -> dict[str, Any]:
        bindings = self.bindings
        prompt = bindings.task_prompt or "Implement the classified task."
        attachment_routing = self._route_attachments(prompt=prompt)
        if attachment_routing is not None:
            self.state.metadata["media_routing"] = attachment_routing

        context: dict[str, Any] = {
            "task_prompt": prompt,
            "classify": self.state.metadata.get("classify", {}),
            "attachments": list(bindings.attachments or []),
        }
        if "research" in self.state.metadata:
            context["research"] = self.state.metadata["research"]
        if "factory" in self.state.metadata:
            factory = self.state.metadata["factory"]
            context["factory"] = factory
            if factory.get("present"):
                context["factory_preserve"] = (
                    "Preserve target-project Factory / AI Factory tooling; "
                    "do not delete or migrate without explicit approval."
                )
        if self.state.metadata.get("plan_required") or self.state.metadata.get(
            "brief_required"
        ):
            context["speckit_gate"] = {
                "plan_required": bool(self.state.metadata.get("plan_required")),
                "brief_required": bool(self.state.metadata.get("brief_required")),
                "plan": self.state.metadata.get("plan"),
                "brief": self.state.metadata.get("brief"),
            }

        lease_fail = self._acquire_serial_edit_lease(role="lead_implement")
        if lease_fail is not None:
            return lease_fail

        lead_kind = self._lead_agent_kind()
        context["agent"] = lead_kind
        context = sanitize_mapping(context)

        # Prefer Orca control-plane dispatch for implement when available.
        if bindings.orca is not None and bindings.project_root:
            orca_result = bindings.orca.execute_task(
                ProviderTaskRequest(
                    prompt=prompt,
                    role="lead_implement",
                    context=context,
                    cwd=bindings.project_root,
                    timeout_seconds=300.0,
                    read_only=False,
                    attachments=tuple(bindings.attachments or ()),
                )
            )
            self.state.metadata["orca_lead_implement"] = orca_result.to_dict()
            if orca_result.ok:
                self.state.metadata["lead_lead_implement"] = orca_result.to_dict()
                return {
                    "ok": True,
                    "detail": orca_result.detail,
                    "provider": orca_result.to_dict(),
                    "kind": "orca",
                    "via": "orca",
                }
            if self._is_quota_failure(orca_result):
                return self._quota_handoff_failure(
                    orca_result, role="lead_implement", source="orca"
                )
            if orca_result.failure is not FailureClass.UNAVAILABLE:
                return {
                    "ok": False,
                    "detail": orca_result.detail,
                    "provider": orca_result.to_dict(),
                    "kind": "orca",
                    "via": "orca",
                }
            self.state.metadata["orca_implement_fallback"] = "unavailable"

        return self._run_lead(
            prompt=prompt,
            role="lead_implement",
            context=context,
            hold_edit_lease=True,
        )

    def _phase_maintenance_review(self) -> dict[str, Any]:
        bindings = self.bindings
        summary = (
            bindings.task_prompt
            or self.state.metadata.get("classify", {}).get("prompt", "change")
        )
        inferred = infer_change_signals(
            project_root=bindings.project_root,
            change_summary=summary,
        )
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
        kwargs.update(
            {
                k: v
                for k, v in bindings.maintenance_kwargs.items()
                if k != "governance_resolved"
            }
        )
        kwargs.setdefault("change_summary", summary)
        self.state.metadata["change_signals"] = {
            "inferred": inferred,
            "effective": dict(kwargs),
        }
        decision = self.apply_maintenance_review(**kwargs)

        if _governance_pending(decision):
            self.state.metadata["governance_pending"] = {
                "ADR_REQUIRED": decision.ADR_REQUIRED,
                "SPEC_UPDATE": decision.SPEC_UPDATE,
            }
        else:
            self.state.metadata.pop("governance_pending", None)

        # SMALL path: insert writers only when maintenance says they are needed.
        scale = (self.state.metadata.get("speckit_path") or {}).get("scale")
        if scale == SpecKitScale.SMALL.value:
            self._maybe_insert_writers_for_small(decision)

        return {
            "ok": True,
            "detail": "maintenance-reviewer complete",
            "decision": decision.to_dict(),
            "change_signals": inferred,
        }

    def _phase_verification(self) -> dict[str, Any]:
        bindings = self.bindings
        commands = bindings.verification_commands
        if not commands:
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

    def _phase_lead_review(self) -> dict[str, Any]:
        bindings = self.bindings
        if self.state.metadata.get("governance_pending") and not bindings.maintenance_kwargs.get(
            "governance_resolved"
        ):
            pending = self.state.metadata["governance_pending"]
            return {
                "ok": False,
                "detail": (
                    "governance still pending before lead review "
                    f"(ADR_REQUIRED={pending.get('ADR_REQUIRED')}, "
                    f"SPEC_UPDATE={pending.get('SPEC_UPDATE')}); "
                    "set maintenance_kwargs governance_resolved=True after ADR/spec work"
                ),
                "governance_pending": pending,
            }

        verification_raw = self.state.metadata.get("verification", {})
        verification = (
            sanitize_mapping(verification_raw)
            if isinstance(verification_raw, dict)
            else {}
        )
        implement = self.state.metadata.get("lead_lead_implement") or self.state.metadata.get(
            "orca_lead_implement"
        ) or {}
        implement_summary = {
            "ok": implement.get("ok"),
            "detail": implement.get("detail"),
            "failure": implement.get("failure"),
            "kind": implement.get("kind") if "kind" in implement else None,
        }
        if isinstance(implement.get("output"), str):
            implement_summary["output"] = implement["output"][:800]

        context = sanitize_mapping(
            {
                "task_prompt": bindings.task_prompt
                or self.state.metadata.get("task_prompt", ""),
                "acceptance_criteria": bindings.task_prompt
                or self.state.metadata.get("task_prompt", ""),
                "classify": self.state.metadata.get("classify", {}),
                "maintenance_decision": (
                    self.state.decision.to_dict() if self.state.decision else None
                ),
                "lead_implement": sanitize_mapping(implement_summary)
                if isinstance(implement_summary, dict)
                else implement_summary,
                "verification": verification,
                "research": self.state.metadata.get("research"),
            }
        )
        return self._run_lead(
            prompt=(
                "Review verification results against the original task and "
                "summarize outcome."
            ),
            role="lead_review",
            context=context,
            hold_edit_lease=False,
        )

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
        holder = self.state.metadata.get("edit_lease_holder") or (
            f"writer-{phase.value}-{id(self)}"
        )
        if root:
            # Share the serial lease held from implement → verification when present.
            if not self.state.metadata.get("edit_lease_held"):
                lease_fail = self._acquire_serial_edit_lease(
                    role=phase.value, agent_id=holder
                )
                if lease_fail is not None:
                    return {**lease_fail, "plan": plan}
            else:
                lease = request_edit_lease(root, str(holder))
                self.state.metadata["edit_lease"] = {
                    "role": phase.value,
                    "agent_id": holder,
                    "allowed": lease.allowed,
                    "policy": lease.policy.value,
                    "path": lease.path,
                    "reason": lease.reason,
                    "shared_holder": True,
                }
                if not lease.allowed:
                    return {
                        "ok": False,
                        "detail": lease.reason,
                        "plan": plan,
                        "failure": FailureClass.ERROR.value,
                        "edit_lease": self.state.metadata["edit_lease"],
                    }
            result = writer_fn(self.state, phase)
            # Do not release here — lease spans through verification.
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
        hold_edit_lease: bool = False,
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
        ctx = sanitize_mapping(dict(context or {}))
        if "research" in self.state.metadata and "research" not in ctx:
            research = self.state.metadata["research"]
            ctx["research"] = (
                sanitize_mapping(research) if isinstance(research, dict) else research
            )

        needs_edit = role == "lead_implement"
        attachments = tuple(self.bindings.attachments or ())

        def _execute(cwd: str | None) -> ProviderTaskResult:
            return lead.execute_task(
                ProviderTaskRequest(
                    prompt=prompt,
                    role=role,
                    context=ctx,
                    cwd=cwd,
                    timeout_seconds=300.0,
                    read_only=not needs_edit,
                    attachments=attachments,
                )
            )

        if needs_edit and self.bindings.project_root:
            if not hold_edit_lease and not self.state.metadata.get("edit_lease_held"):
                lease_fail = self._acquire_serial_edit_lease(role=role)
                if lease_fail is not None:
                    return lease_fail
            result = _execute(self.bindings.project_root)
            if not hold_edit_lease and not self.state.metadata.get("edit_lease_held"):
                self._release_serial_edit_lease()
        else:
            result = _execute(self.bindings.project_root)

        key = f"lead_{role}"
        self.state.metadata[key] = result.to_dict()
        if self._is_quota_failure(result) and needs_edit:
            return self._quota_handoff_failure(result, role=role, source=lead.kind.value)
        return {
            "ok": result.ok,
            "detail": result.detail,
            "provider": result.to_dict(),
            "kind": lead.kind.value,
        }

    def _acquire_serial_edit_lease(
        self,
        *,
        role: str,
        agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        root = self.bindings.project_root
        if not root:
            return None
        if self.state.metadata.get("edit_lease_held"):
            return None
        holder = agent_id or f"lead-{role}-{id(self)}"
        lease = request_edit_lease(root, holder)
        self.state.metadata["edit_lease"] = {
            "role": role,
            "agent_id": holder,
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
        self.state.metadata["edit_lease_holder"] = holder
        self.state.metadata["edit_lease_held"] = True
        return None

    def _release_serial_edit_lease(self) -> None:
        root = self.bindings.project_root
        holder = self.state.metadata.get("edit_lease_holder")
        if not root or not holder:
            self.state.metadata.pop("edit_lease_held", None)
            return
        try:
            release_edit_lease(root, str(holder))
        finally:
            self.state.metadata["edit_lease_held"] = False
            self.state.metadata.pop("edit_lease_holder", None)

    def _report_orca_phase(self, phase: Phase, outcome: PhaseOutcome) -> None:
        """Best-effort Orca phase metadata / read-only ping; never fails the workflow."""
        report = {
            "phase": phase.value,
            "status": outcome.status.value,
            "detail": outcome.detail,
        }
        reports = self.state.metadata.setdefault("orca_phase_reports", [])
        if isinstance(reports, list):
            reports.append(report)
        orca = self.bindings.orca
        if orca is None:
            return
        if not self.bindings.project_root:
            self.state.metadata.setdefault("orca_skipped_reason", "no_project_root")
            return
        try:
            result = orca.execute_task(
                ProviderTaskRequest(
                    prompt=f"Mode C phase report: {phase.value}={outcome.status.value}",
                    role="control_plane",
                    context=sanitize_mapping(report),
                    cwd=self.bindings.project_root,
                    timeout_seconds=30.0,
                    read_only=True,
                )
            )
            self.state.metadata[f"orca_report_{phase.value}"] = {
                "ok": result.ok,
                "failure": result.failure.value,
                "detail": result.detail,
            }
        except Exception as exc:  # noqa: BLE001 — best-effort only
            self.state.metadata[f"orca_report_{phase.value}"] = {
                "ok": False,
                "failure": FailureClass.UNAVAILABLE.value,
                "detail": str(exc),
            }

    def _route_attachments(self, *, prompt: str) -> dict[str, Any] | None:
        paths = list(self.bindings.attachments or [])
        if not paths:
            return None
        decision = route_media(paths, text=prompt)
        return {
            "vision_required": decision.vision_required,
            "allowed": decision.allowed,
            "provider_hint": decision.provider_hint,
            "reason": decision.reason,
            "summarized_text": decision.summarized_text,
            "paths": paths,
        }

    def _apply_scale_phases(self, scale: SpecKitScale) -> None:
        """Rebuild remaining phases after CLASSIFY according to Spec Kit scale."""
        research_default = bool(self.state.metadata.get("research_useful_default", True))
        if scale is SpecKitScale.SMALL:
            new_phases = phases_for_scale(scale)
        elif scale is SpecKitScale.MEDIUM:
            new_phases = default_phases(research_useful=True)
        else:
            new_phases = default_phases(research_useful=True)
            if not research_default and Phase.RESEARCH not in new_phases:
                new_phases = phases_for_scale(scale)

        # Preserve CLASSIFY as index 0; replace the tail.
        completed_classify = self.state.phases[:1] if self.state.phases else [Phase.CLASSIFY]
        rebuilt = list(completed_classify) + [
            p for p in new_phases if p is not Phase.CLASSIFY
        ]
        self.state.phases = rebuilt
        for phase in rebuilt:
            if phase.value not in self.state.phase_outcomes:
                self.state.phase_outcomes[phase.value] = PhaseOutcome(
                    phase=phase, status=PhaseStatus.PENDING
                )
        # Drop outcomes for phases no longer on the path (except already recorded).
        keep = {p.value for p in rebuilt} | set(self.state.completed) | set(self.state.skipped)
        for key in list(self.state.phase_outcomes):
            if key not in keep and key != Phase.CLASSIFY.value:
                # Keep historical outcomes; just leave them.
                pass

    def _maybe_insert_writers_for_small(
        self, decision: MaintenanceReviewDecision
    ) -> None:
        """SMALL skips writers unless maintenance says otherwise."""
        if not decision.needs_tests and not decision.needs_docs:
            return
        insert_at = None
        for i, phase in enumerate(self.state.phases):
            if phase is Phase.VERIFICATION:
                insert_at = i
                break
        if insert_at is None:
            return
        extras: list[Phase] = []
        if decision.needs_tests and Phase.TEST_WRITER not in self.state.phases:
            extras.append(Phase.TEST_WRITER)
        if decision.needs_docs and Phase.DOC_WRITER not in self.state.phases:
            extras.append(Phase.DOC_WRITER)
        if not extras:
            return
        self.state.phases = (
            self.state.phases[:insert_at] + extras + self.state.phases[insert_at:]
        )
        for phase in extras:
            self.state.phase_outcomes[phase.value] = PhaseOutcome(
                phase=phase, status=PhaseStatus.PENDING
            )

    def _lead_agent_kind(self) -> str:
        lead = self.bindings.lead
        if lead is not None:
            return lead.kind.value
        if self.bindings.providers:
            selection = select_lead(self.bindings.providers)
            if selection.lead is not None:
                return selection.lead.value
        return ProviderKind.CODEX.value

    def _speckit_from_classify_data(
        self, data: Mapping[str, Any], *, prompt: str
    ) -> SpecKitPath:
        raw = data.get("speckit_path")
        if isinstance(raw, SpecKitPath):
            return raw
        if isinstance(raw, Mapping) and raw.get("scale"):
            try:
                scale = SpecKitScale(str(raw["scale"]))
            except ValueError:
                scale = None
            if scale is not None:
                return SpecKitPath(
                    scale=scale,
                    steps=tuple(raw.get("steps") or ()),
                    rationale=str(raw.get("rationale") or "classify_fn"),
                )
        classification = str(data.get("classification") or "").lower()
        if classification in {SpecKitScale.SMALL.value, "small"}:
            return classify_speckit_scale(risk="low", estimated_files=1)
        if classification in {
            SpecKitScale.LARGE_HIGH_RISK.value,
            "large",
            "large_high_risk",
        }:
            return classify_speckit_scale(risk="high", user_requested_full_sdd=True)
        if classification in {SpecKitScale.MEDIUM.value, "medium"}:
            return classify_speckit_scale(risk="medium")
        heuristics = _prompt_heuristics(prompt)
        return classify_speckit_scale(**heuristics)

    def _is_quota_failure(self, result: ProviderTaskResult) -> bool:
        if result.failure is FailureClass.QUOTA:
            return True
        blob = f"{result.detail} {result.output}".lower()
        return "quota" in blob

    def _quota_handoff_failure(
        self,
        result: ProviderTaskResult,
        *,
        role: str,
        source: str,
    ) -> dict[str, Any]:
        packet = build_handoff_packet(
            original_request=self.bindings.task_prompt
            or self.state.metadata.get("task_prompt", ""),
            accepted_decisions=[
                str((self.state.metadata.get("classify") or {}).get("classification", "")),
            ],
            repo_path=self.bindings.project_root or "",
            workflow_phase=role,
            completed_work=list(self.state.completed),
            remaining_work=[p.value for p in self.state.phases[self.state.current_index :]],
            known_failures=[result.detail],
            next_action="Continue implement on Cursor via one-action manual handoff",
            compacted_research=dict(self.state.metadata.get("research") or {}),
            source_lead=source if source != "orca" else self._lead_agent_kind(),
            target_lead="cursor",
        )
        handoff = prepare_manual_handoff(packet)
        self.state.metadata["manual_handoff"] = handoff
        return {
            "ok": False,
            "detail": (
                f"quota failure during {role}; manual handoff prepared — "
                "see metadata.manual_handoff"
            ),
            "provider": result.to_dict(),
            "failure": FailureClass.QUOTA.value,
            "manual_handoff": True,
        }


def _prompt_heuristics(prompt: str) -> dict[str, Any]:
    text = (prompt or "").lower()
    touches_security = any(h in text for h in _SECURITY_HINTS)
    touches_money = any(h in text for h in _MONEY_HINTS)
    user_requested_full_sdd = any(h in text for h in _FULL_SDD_HINTS)
    multi_package = any(h in text for h in _SCOPE_HINTS)

    estimated_files = 1
    file_match = re.search(r"\b(\d+)\s+files?\b", text)
    if file_match:
        estimated_files = max(1, int(file_match.group(1)))
    elif multi_package or "across" in text:
        estimated_files = 8
    elif any(w in text for w in ("refactor", "migrate", "rewrite", "overhaul")):
        estimated_files = 10
    elif any(w in text for w in ("feature", "implement", "add ")):
        estimated_files = 3

    risk = "low"
    if touches_security or touches_money or user_requested_full_sdd:
        risk = "high"
    elif multi_package or estimated_files >= 8:
        risk = "medium"
    elif any(w in text for w in ("medium", "several modules")):
        risk = "medium"

    return {
        "risk": risk,
        "touches_security": touches_security,
        "touches_money": touches_money,
        "multi_package": multi_package,
        "estimated_files": estimated_files,
        "user_requested_full_sdd": user_requested_full_sdd,
    }


def _governance_pending(decision: MaintenanceReviewDecision) -> bool:
    if decision.ADR_REQUIRED:
        return True
    spec = decision.SPEC_UPDATE
    if isinstance(spec, bool):
        return bool(spec)
    return str(spec).lower() not in {"none", "", "false", "0"}


def bound_writer_from_lead(
    lead: ProviderAdapter,
    *,
    project_root: str | None = None,
    attachments: tuple[str, ...] = (),
) -> Callable[[WorkflowState, Phase], ProviderTaskResult]:
    """Default writer executor: run required test/doc writes through the lead."""

    def _writer(state: WorkflowState, phase: Phase) -> ProviderTaskResult:
        decision = state.decision
        plan_hint = ""
        decision_payload: dict[str, Any] | None = None
        if decision is not None:
            to_dict = getattr(decision, "to_dict", None)
            if callable(to_dict):
                decision_payload = to_dict()
            else:
                decision_payload = {
                    "TEST_DECISION": getattr(decision, "TEST_DECISION", None),
                    "TEST_SCOPE": list(getattr(decision, "TEST_SCOPE", []) or []),
                    "DOC_DECISION": getattr(decision, "DOC_DECISION", None),
                    "DOC_TARGETS": list(getattr(decision, "DOC_TARGETS", []) or []),
                }
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
        task_prompt = str(
            (getattr(state, "metadata", None) or {}).get("task_prompt")
            or ((getattr(state, "metadata", None) or {}).get("classify") or {}).get(
                "prompt"
            )
            or ""
        )
        prompt = (
            f"Mode C {phase.value}: implement the required writer work. {plan_hint}"
        ).strip()
        context = sanitize_mapping(
            {
                "phase": phase.value,
                "plan_hint": plan_hint,
                "task_prompt": task_prompt,
                "decision": decision_payload,
            }
        )
        return lead.execute_task(
            ProviderTaskRequest(
                prompt=prompt,
                role=phase.value,
                context=context,
                cwd=project_root,
                timeout_seconds=300.0,
                read_only=False,
                attachments=attachments,
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
