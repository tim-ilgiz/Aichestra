"""Mode C thin run controller: one Orca Run + policy/deterministic gates (FR-054).

Aichestra MUST NOT act as a second general-purpose orchestrator. Canonical
lifecycle identity is the Orca ``run_id``. Local phase progression mirrors the
policy gate schedule; agent work is dispatched only through the bound Orca
adapter under that Run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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
    """Injectable Mode C bindings — Orca required; fakes in CI."""

    orca: ProviderAdapter | None = None
    lead: ProviderAdapter | None = None
    local_worker: ProviderAdapter | None = None
    providers: list[ProviderStatus] = field(default_factory=list)
    project_root: str | None = None
    task_prompt: str = ""
    research_query: str = ""
    attachments: tuple[str, ...] = ()
    resume_run_id: str | None = None
    classify_fn: Callable[[WorkflowState], dict[str, Any]] | None = None
    research_fn: Callable[[WorkflowState], ResearchSummary] | None = None
    maintenance_kwargs: dict[str, Any] = field(default_factory=dict)
    verification_commands: list[list[str]] = field(default_factory=list)
    preferred_lead: str = "codex"
    fallback_lead: str = "cursor"
    local_enabled: bool = False
    local_model: Any = None
    # Deprecated Mode C seam — ignored when orca is bound (writers go via Orca).
    writer_fn: Callable[[WorkflowState, Phase], ProviderTaskResult] | None = None


class ModeCRunController:
    """Thin Mode C controller over one Orca Run + local policy/gates.

    Agent steps (research, implement, writers, lead_review) execute only through
    the Orca adapter under a single ``orca_run_id``. Deterministic gates
    (classify heuristics, maintenance-reviewer, verification-runner) run locally
    against the adopted child worktree when present.
    """

    def __init__(
        self,
        *,
        mode: Mode = Mode.ORCHESTRATED,
        research_useful: bool = True,
        bindings: WorkflowBindings | None = None,
    ) -> None:
        if not starts_full_orchestration(mode):
            raise ValueError(
                f"Mode C run controller requires Mode C; got {mode.value}"
            )
        self.bindings = bindings or WorkflowBindings()
        self.state = WorkflowState(
            mode=mode,
            phases=default_phases(research_useful=research_useful),
        )
        self.state.metadata["task_prompt"] = self.bindings.task_prompt
        self.state.metadata["research_useful_default"] = research_useful
        self.state.metadata["canonical_orchestration"] = "orca_run"
        self.state.metadata["controller"] = "ModeCRunController"
        if self.bindings.resume_run_id and self.bindings.resume_run_id.strip():
            self.state.metadata["orca_run_id"] = self.bindings.resume_run_id.strip()
            self.state.metadata["orca_run_resumed"] = True
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
            return self._phase_research()

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

        # Mode C must not run providers against ambient cwd.
        root = bindings.project_root
        if not root or not str(root).strip():
            return {
                "ok": False,
                "detail": (
                    "Mode C requires --project-root before any provider execution; "
                    "refusing ambient cwd writes"
                ),
                "failure": FailureClass.ERROR.value,
            }
        if not Path(root).is_dir():
            return {
                "ok": False,
                "detail": f"Mode C project-root is not a directory: {root}",
                "failure": FailureClass.ERROR.value,
            }

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
            if attachment_routing.get("vision_required") and not attachment_routing.get(
                "allowed", True
            ):
                return {
                    "ok": False,
                    "detail": attachment_routing.get("reason")
                    or "vision attachments cannot be routed",
                    "failure": FailureClass.ERROR.value,
                }

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
                "status": "pending",
                "summary": prompt[:500],
            }
            self.state.metadata["brief_required"] = True
        if path.scale is SpecKitScale.LARGE_HIGH_RISK:
            self.state.metadata["plan_required"] = True
            self.state.metadata["brief_required"] = True
            self.state.metadata["clarify_required"] = True
            self.state.metadata["tasks_required"] = True
            self.state.metadata["brief"] = {
                "required": True,
                "status": "pending",
                "summary": prompt[:500],
            }
            self.state.metadata["plan"] = {
                "required": True,
                "status": "pending",
                "note": (
                    "LARGE path hard-blocks implement until clarify/plan/tasks "
                    "artifacts are ready (no fake status=recorded success)"
                ),
                "summary": prompt[:500],
            }
            self.state.metadata["tasks"] = {
                "required": True,
                "status": "pending",
            }
            self.state.metadata["clarify"] = {
                "required": True,
                "status": "pending",
            }
            self.state.metadata["speckit_execution"] = "hard_blocked_until_artifacts"

        self._apply_scale_phases(path.scale)

        # Mode C binds exactly one Orca Run at classify (policy → control plane).
        ensure = self._ensure_orca_run(objective=prompt)
        if ensure is not None and not ensure.get("ok", False):
            # Missing Orca is a hard Mode C failure — not a lead fallback.
            if ensure.get("failure") == FailureClass.UNAVAILABLE.value:
                return ensure
            # Soft: non-unavailable ensure issues still fail Mode C start.
            return ensure

        classify_outcome = PhaseOutcome(
            phase=Phase.CLASSIFY,
            status=PhaseStatus.SUCCEEDED,
            detail="classified",
            result={"ok": True, "detail": "classified", **data},
        )
        self._report_orca_phase(Phase.CLASSIFY, classify_outcome)
        return {"ok": True, "detail": "classified", **data}

    def _phase_research(self) -> dict[str, Any]:
        """Research agent via the same Orca Run; compact locally for handoff."""
        bindings = self.bindings
        query = bindings.research_query or bindings.task_prompt or "repository research"
        context: dict[str, Any] = {
            "query": query,
            "agent": "opencode",
            "prefer_local_worker": True,
            "read_only": True,
        }
        orca_out = self._run_via_orca(
            prompt=f"Repository research (read-only): {query}",
            role="research",
            context=context,
            read_only=True,
            adopt_worktree=False,
        )
        if not orca_out.get("ok"):
            return orca_out

        # Deterministic compaction helper (filesystem) — not a direct local-worker
        # dispatch. Optional research_fn is a test seam for summary shape only.
        if bindings.research_fn:
            summary = bindings.research_fn(self.state)
        else:
            root = self._effective_project_root() or bindings.project_root or "."
            summary = research_paths(
                root,
                query=query,
                prefer_local_worker=False,
                local_worker=None,
            )
        payload = summary.to_dict()
        payload["via"] = "orca"
        payload["orca"] = {
            "ok": orca_out.get("ok"),
            "detail": orca_out.get("detail"),
            "run_id": orca_out.get("run_id"),
        }
        provider = orca_out.get("provider")
        if isinstance(provider, dict) and isinstance(provider.get("output"), str):
            payload["ORCA_OUTPUT"] = provider["output"][:2000]
        self.state.metadata["research"] = payload
        return {"ok": True, "detail": "research via Orca compacted", "research": payload}

    def _phase_lead_implement(self) -> dict[str, Any]:
        bindings = self.bindings
        prompt = bindings.task_prompt or "Implement the classified task."
        gate = self._speckit_implement_gate()
        if gate is not None:
            return gate
        attachment_routing = self._route_attachments(prompt=prompt)
        if attachment_routing is not None:
            self.state.metadata["media_routing"] = attachment_routing
            if attachment_routing.get("vision_required") and not attachment_routing.get(
                "bytes_delivered"
            ):
                # FR-058: vision inputs must reach a vision-capable provider via
                # native attachments — path text alone is insufficient.
                return {
                    "ok": False,
                    "detail": (
                        "Mode C vision attachments require native byte delivery "
                        "(Orca --attach / staged inbox); path-only prompts refused"
                    ),
                    "failure": FailureClass.ERROR.value,
                    "media_routing": attachment_routing,
                }
            if attachment_routing.get("vision_required") and not attachment_routing.get(
                "allowed", True
            ):
                return {
                    "ok": False,
                    "detail": attachment_routing.get("reason")
                    or "vision routing refused",
                    "failure": FailureClass.ERROR.value,
                }

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
        # First write placement: isolated Orca child worktree.
        context["worktree"] = "new-child"
        context = sanitize_mapping(context)

        return self._run_via_orca(
            prompt=prompt,
            role="lead_implement",
            context=context,
            read_only=False,
            adopt_worktree=True,
        )

    def _phase_maintenance_review(self) -> dict[str, Any]:
        bindings = self.bindings
        summary = (
            bindings.task_prompt
            or self.state.metadata.get("classify", {}).get("prompt", "change")
        )
        root = self._effective_project_root()
        inferred = infer_change_signals(
            project_root=root,
            change_summary=summary,
        )
        # Keep full diff in metadata for maintenance-reviewer / writers; do not
        # pass the raw blob into review_change kwargs (structured fields only).
        implementation_diff = str(inferred.get("implementation_diff") or "")
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
            "project_root": root,
            "implementation_diff": implementation_diff,
            "implementation_diff_chars": len(implementation_diff),
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
        root = self._effective_project_root()
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
        report = run_verification(commands, cwd=root)
        self.state.metadata["verification"] = report.to_dict()
        self.state.metadata["verification_cwd"] = root
        return {
            "ok": report.ok,
            "detail": "verification ok" if report.ok else "verification failed",
            "verification": report.to_dict(),
        }

    def _phase_lead_review(self) -> dict[str, Any]:
        bindings = self.bindings
        if (
            self.state.metadata.get("governance_pending")
            and not bindings.maintenance_kwargs.get("governance_resolved")
            and not self.state.metadata.get("governance_resolved_via_orca")
        ):
            # Attempt one Orca governance task under the same Run before hard-stop.
            if not self.state.metadata.get("governance_orca_attempted"):
                pending = self.state.metadata["governance_pending"]
                gov = self._run_via_orca(
                    prompt=(
                        "Resolve pending governance before final review: "
                        f"ADR_REQUIRED={pending.get('ADR_REQUIRED')}, "
                        f"SPEC_UPDATE={pending.get('SPEC_UPDATE')}. "
                        "Create or update required ADR/spec artifacts in the worktree."
                    ),
                    role="governance",
                    context={
                        "agent": self._lead_agent_kind(),
                        "governance_pending": pending,
                        "worktree": (
                            self.state.metadata.get("orca_worktree_id")
                            or self.state.metadata.get("orca_worktree_path")
                            or "current"
                        ),
                    },
                    read_only=False,
                    adopt_worktree=False,
                )
                self.state.metadata["governance_orca_attempted"] = True
                self.state.metadata["governance_orca"] = gov
                if gov.get("ok"):
                    self.state.metadata["governance_resolved_via_orca"] = True
                else:
                    return {
                        "ok": False,
                        "detail": (
                            "governance still pending before lead review "
                            f"(ADR_REQUIRED={pending.get('ADR_REQUIRED')}, "
                            f"SPEC_UPDATE={pending.get('SPEC_UPDATE')}); "
                            "Orca governance task failed — set maintenance_kwargs "
                            "governance_resolved=True after ADR/spec work"
                        ),
                        "governance_pending": pending,
                        "governance_orca": gov,
                    }
            else:
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
                "change_signals": self.state.metadata.get("change_signals"),
                "orca_integration": self.state.metadata.get("orca_integration"),
                "orca_worktree_path": self.state.metadata.get("orca_worktree_path"),
                "orca_run_id": self.state.metadata.get("orca_run_id"),
                "agent": self._lead_agent_kind(),
            }
        )
        # Refresh worktree diff for final review when adoption occurred.
        root = self._effective_project_root()
        if root:
            review_signals = infer_change_signals(
                project_root=root,
                change_summary=bindings.task_prompt or "review",
            )
            context["review_change_signals"] = sanitize_mapping(
                {
                    k: v
                    for k, v in review_signals.items()
                    if k
                    in {
                        "change_summary",
                        "changed_paths",
                        "implementation_diff",
                        "git_status",
                        "touches_behavior",
                        "touches_public_api",
                    }
                }
            )
            if isinstance(review_signals.get("implementation_diff"), str):
                diff = review_signals["implementation_diff"]
                context["implementation_diff_excerpt"] = diff[:12000]
        # Continue on adopted child worktree when present.
        if self.state.metadata.get("orca_worktree_id"):
            context["worktree"] = self.state.metadata["orca_worktree_id"]
        elif self.state.metadata.get("orca_worktree_path"):
            context["worktree"] = self.state.metadata["orca_worktree_path"]
        else:
            context["worktree"] = "current"

        return self._run_via_orca(
            prompt=(
                "Review verification results against the original task and "
                "summarize outcome."
            ),
            role="lead_review",
            context=context,
            read_only=True,
            adopt_worktree=False,
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

        decision = self.state.decision
        plan_hint = ""
        decision_payload: dict[str, Any] | None = None
        if decision is not None:
            decision_payload = decision.to_dict()
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
        context: dict[str, Any] = sanitize_mapping(
            {
                "phase": phase.value,
                "plan_hint": plan_hint,
                "plan": plan,
                "task_prompt": self.bindings.task_prompt
                or self.state.metadata.get("task_prompt", ""),
                "decision": decision_payload,
                "agent": self._lead_agent_kind(),
            }
        )
        if self.state.metadata.get("orca_worktree_id"):
            context["worktree"] = self.state.metadata["orca_worktree_id"]
        elif self.state.metadata.get("orca_worktree_path"):
            context["worktree"] = self.state.metadata["orca_worktree_path"]
        else:
            context["worktree"] = "current"

        # Writers always go through Orca under the Mode C Run — no lead bypass.
        if self.bindings.writer_fn is not None:
            self.state.metadata["writer_fn_ignored"] = (
                "Mode C ignores writer_fn; writers dispatch via Orca only"
            )

        return {
            **self._run_via_orca(
                prompt=prompt,
                role=phase.value,
                context=context,
                read_only=False,
                adopt_worktree=False,
            ),
            "plan": plan,
        }

    def _effective_project_root(self) -> str | None:
        """Checkout used for gates: adopted Orca child worktree when present."""
        adopted = self.state.metadata.get("orca_worktree_path")
        if isinstance(adopted, str) and adopted.strip():
            return adopted.strip()
        return self.bindings.project_root

    def _speckit_implement_gate(self) -> dict[str, Any] | None:
        """Block implement when MEDIUM/LARGE Spec Kit artifacts are still pending."""
        meta = self.state.metadata
        scale = (meta.get("speckit_path") or {}).get("scale")
        if scale == SpecKitScale.SMALL.value:
            return None

        pending: list[str] = []
        if meta.get("brief_required"):
            brief = meta.get("brief") if isinstance(meta.get("brief"), dict) else {}
            status = str(brief.get("status") or "pending")
            if status not in {"ready", "approved", "complete", "satisfied"}:
                # Allow explicit override via bindings metadata injection.
                if not meta.get("brief_satisfied"):
                    pending.append("brief")
        if meta.get("plan_required"):
            plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
            status = str(plan.get("status") or "pending")
            if status not in {"ready", "approved", "complete", "satisfied"}:
                if not meta.get("plan_satisfied"):
                    pending.append("plan")
        if meta.get("clarify_required") and not meta.get("clarify_satisfied"):
            clarify = meta.get("clarify") if isinstance(meta.get("clarify"), dict) else {}
            if str(clarify.get("status") or "pending") not in {
                "ready",
                "approved",
                "complete",
                "satisfied",
            }:
                pending.append("clarify")
        if meta.get("tasks_required") and not meta.get("tasks_satisfied"):
            tasks = meta.get("tasks") if isinstance(meta.get("tasks"), dict) else {}
            if str(tasks.get("status") or "pending") not in {
                "ready",
                "approved",
                "complete",
                "satisfied",
            }:
                pending.append("tasks")

        if not pending:
            return None
        detail = (
            f"Spec Kit {scale} gate blocked implement; pending artifacts: "
            + ", ".join(pending)
            + ". Mark metadata statuses ready/approved or set *_satisfied before implement."
        )
        self.state.metadata["speckit_gate_blocked"] = pending
        return {
            "ok": False,
            "detail": detail,
            "failure": FailureClass.ERROR.value,
            "pending_artifacts": pending,
        }

    def _ensure_orca_run(self, *, objective: str | None = None) -> dict[str, Any] | None:
        """Create or reuse the single Mode C Orca Run. Returns failure dict or None."""
        existing = self.state.metadata.get("orca_run_id")
        if isinstance(existing, str) and existing.strip():
            return None

        orca = self.bindings.orca
        if orca is None:
            return {
                "ok": False,
                "detail": (
                    "Mode C requires Orca as the orchestration control plane; "
                    "native Codex/Cursor remain Mode A and are not used as Mode C fallback"
                ),
                "failure": FailureClass.UNAVAILABLE.value,
            }

        prompt = objective or self.bindings.task_prompt or "Aichestra Mode C run"
        root = self.bindings.project_root
        result = orca.execute_task(
            ProviderTaskRequest(
                prompt=prompt,
                role="ensure_run",
                context={},
                cwd=root,
                timeout_seconds=60.0,
                read_only=True,
            )
        )
        self.state.metadata["orca_ensure_run"] = result.to_dict()
        run_id = (result.metadata or {}).get("run_id")
        if result.ok and isinstance(run_id, str) and run_id.strip():
            self.state.metadata["orca_run_id"] = run_id.strip()
            return None
        if result.ok and not run_id:
            # Fakes / degraded receipts: synthesize a stable workflow-local id only
            # when the adapter explicitly marked reuse/success without an id.
            synthesized = f"aichestra-run-{id(self)}"
            self.state.metadata["orca_run_id"] = synthesized
            self.state.metadata["orca_run_id_synthesized"] = True
            return None
        return {
            "ok": False,
            "detail": result.detail or "failed to create Orca Run",
            "failure": result.failure.value,
            "provider": result.to_dict(),
        }

    def _adopt_orca_worktree(self, result: ProviderTaskResult) -> None:
        """Record child worktree from Orca receipt and switch effective root."""
        meta = result.metadata or {}
        path = meta.get("worktree_path")
        worktree_id = meta.get("worktree_id")
        parent = self.bindings.project_root
        if isinstance(path, str) and path.strip():
            self.state.metadata["orca_worktree_path"] = path.strip()
        if isinstance(worktree_id, str) and worktree_id.strip():
            self.state.metadata["orca_worktree_id"] = worktree_id.strip()
        self.state.metadata["orca_integration"] = {
            "policy": "adopt_child_worktree",
            "parent_project_root": parent,
            "worktree_path": self.state.metadata.get("orca_worktree_path"),
            "worktree_id": self.state.metadata.get("orca_worktree_id"),
            "note": (
                "Remaining Mode C gates run against the adopted Orca worktree; "
                "parent checkout is not silently verified as if it received the edits"
            ),
        }

    def _run_via_orca(
        self,
        *,
        prompt: str,
        role: str,
        context: Mapping[str, Any] | None = None,
        read_only: bool = False,
        adopt_worktree: bool = False,
    ) -> dict[str, Any]:
        """Dispatch an agent phase through Orca only (no Codex/Cursor bypass)."""
        ensure = self._ensure_orca_run(objective=prompt)
        if ensure is not None:
            return ensure

        orca = self.bindings.orca
        assert orca is not None  # ensured above
        run_id = str(self.state.metadata["orca_run_id"])
        ctx = sanitize_mapping(dict(context or {}))
        ctx["run_id"] = run_id
        if "agent" not in ctx:
            ctx["agent"] = self._lead_agent_kind()
        if "research" in self.state.metadata and "research" not in ctx:
            research = self.state.metadata["research"]
            ctx["research"] = (
                sanitize_mapping(research) if isinstance(research, dict) else research
            )

        root = self._effective_project_root()
        result = orca.execute_task(
            ProviderTaskRequest(
                prompt=prompt,
                role=role,
                context=ctx,
                cwd=root,
                timeout_seconds=300.0,
                read_only=read_only,
                attachments=tuple(self.bindings.attachments or ()),
            )
        )
        key = f"orca_{role}"
        self.state.metadata[key] = result.to_dict()
        if role == "lead_implement":
            self.state.metadata["lead_lead_implement"] = result.to_dict()
        if result.ok and adopt_worktree:
            self._adopt_orca_worktree(result)
        if self._is_quota_failure(result):
            return self._quota_handoff_failure(result, role=role, source="orca")
        return {
            "ok": result.ok,
            "detail": result.detail,
            "provider": result.to_dict(),
            "kind": "orca",
            "via": "orca",
            "run_id": run_id,
        }

    def _run_lead(
        self,
        *,
        prompt: str,
        role: str,
        context: Mapping[str, Any] | None = None,
        hold_edit_lease: bool = False,
    ) -> dict[str, Any]:
        """Direct lead adapter — Mode A / test seams only; not Mode C fallback."""
        # Mode C must not reach here for agent work. Keep for explicit non-orchestrated
        # callers and unit seams; refuse when an Orca binding is present.
        if self.bindings.orca is not None:
            return {
                "ok": False,
                "detail": (
                    f"refusing direct lead dispatch for {role}: Mode C routes "
                    "agent work through Orca only"
                ),
                "failure": FailureClass.ERROR.value,
            }
        lead = self.bindings.lead
        if lead is None and self.bindings.providers:
            selection = select_lead(
                self.bindings.providers,
                preferred=self.bindings.preferred_lead,
                fallback=self.bindings.fallback_lead,
            )
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
                "detail": "no lead provider bound",
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

        root = self._effective_project_root()
        if needs_edit and root:
            if not hold_edit_lease and not self.state.metadata.get("edit_lease_held"):
                lease_fail = self._acquire_serial_edit_lease(role=role)
                if lease_fail is not None:
                    return lease_fail
            result = _execute(root)
            if not hold_edit_lease and not self.state.metadata.get("edit_lease_held"):
                self._release_serial_edit_lease()
        else:
            result = _execute(root)

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
        root = self._effective_project_root()
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
        root = self._effective_project_root() or self.bindings.project_root
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
        """Record phase status locally; never create additional Orca Runs."""
        report = {
            "phase": phase.value,
            "status": outcome.status.value,
            "detail": outcome.detail,
            "run_id": self.state.metadata.get("orca_run_id"),
        }
        reports = self.state.metadata.setdefault("orca_phase_reports", [])
        if isinstance(reports, list):
            reports.append(report)
        orca = self.bindings.orca
        if orca is None:
            return
        run_id = self.state.metadata.get("orca_run_id")
        if not run_id:
            return
        try:
            result = orca.execute_task(
                ProviderTaskRequest(
                    prompt=f"Mode C phase report: {phase.value}={outcome.status.value}",
                    role="phase_report",
                    context=sanitize_mapping({**report, "run_id": run_id}),
                    cwd=self._effective_project_root(),
                    timeout_seconds=15.0,
                    read_only=True,
                )
            )
            self.state.metadata[f"orca_report_{phase.value}"] = {
                "ok": result.ok,
                "failure": result.failure.value,
                "detail": result.detail,
                "run_id": (result.metadata or {}).get("run_id") or run_id,
            }
        except Exception as exc:  # noqa: BLE001 — best-effort only
            self.state.metadata[f"orca_report_{phase.value}"] = {
                "ok": False,
                "failure": FailureClass.UNAVAILABLE.value,
                "detail": str(exc),
            }

    def _route_attachments(self, *, prompt: str) -> dict[str, Any] | None:
        paths = list(self.bindings.attachments or ())
        if not paths:
            return None
        from aichestra.providers.attachments import stage_attachments

        decision = route_media(
            paths,
            text=prompt,
            local_model=self.bindings.local_model,
            local_enabled=bool(self.bindings.local_enabled),
            prefer_orca_attachments=True,
        )
        delivery = stage_attachments(paths, self._effective_project_root())
        bytes_ok = bool(delivery.bytes_delivered)
        # Prefer staged paths for subsequent provider dispatch.
        if delivery.staged:
            self.bindings.attachments = tuple(delivery.staged)
        elif delivery.resolved:
            self.bindings.attachments = tuple(delivery.resolved)
        return {
            "vision_required": decision.vision_required,
            "allowed": decision.allowed,
            "provider_hint": decision.provider_hint,
            "reason": decision.reason,
            "summarized_text": decision.summarized_text,
            "paths": list(self.bindings.attachments),
            "bytes_delivered": bytes_ok,
            "attachment_delivery": delivery.to_dict(),
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
            selection = select_lead(
                self.bindings.providers,
                preferred=self.bindings.preferred_lead,
                fallback=self.bindings.fallback_lead,
            )
            if selection.lead is not None:
                return selection.lead.value
        preferred = (self.bindings.preferred_lead or "codex").strip().lower()
        return preferred or ProviderKind.CODEX.value

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


# Compat alias — prefer ModeCRunController in new code.
OrchestratedWorkflow = ModeCRunController


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
    workflow: ModeCRunController,
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
