"""Mode C thin run controller: one Orca Run + policy/deterministic gates (FR-054).

Aichestra MUST NOT act as a second general-purpose orchestrator. Canonical
lifecycle identity is the Orca ``run_id``. Local phase progression mirrors the
policy gate schedule; agent work is dispatched only through the bound Orca
adapter under that Run.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from aichestra.orchestration.change_signals import _is_test_path, infer_change_signals
from aichestra.orchestration.factory_preserve import detect_factory_tooling
from aichestra.orchestration.handoff import (
    build_handoff_packet,
    prepare_manual_handoff,
    record_mode_c_run_id,
)
from aichestra.orchestration.maintenance_reviewer import (
    MaintenanceReviewDecision,
    review_change,
)
from aichestra.orchestration.media_routing import route_media
from aichestra.orchestration.modes import Mode, starts_full_orchestration
from aichestra.orchestration.research_compact import ResearchSummary
from aichestra.orchestration.roles import select_lead
from aichestra.orchestration.speckit_policy import (
    SpecKitPath,
    SpecKitScale,
    classify_speckit_scale,
)
from aichestra.orchestration.verification import (
    VerificationReport,
    detect_verification_commands,
    run_verification,
)
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
    """Injectable Mode C bindings — Orca required; fakes in CI.

    ``lead`` / ``local_worker`` are policy/discovery inputs for Orca agent
    selection. Mode C MUST NOT call their ``execute_task`` for agent roles.
    """

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


@dataclass
class ModeCPolicyPackage:
    """Policy package handed to Orca for Mode C agent orchestration.

    Lead/local fields are **policy inputs** for Orca (preferred/fallback /
    enabled). ``lead_agent`` / ``research_agent`` are the resolved suggestions
    derived from that policy — never invented disabled providers.
    """

    run_id: str
    task_prompt: str
    research_query: str
    research_useful: bool
    lead_agent: str
    research_agent: str | None
    speckit_scale: str
    speckit_steps: tuple[str, ...]
    attachments: tuple[str, ...]
    project_root: str
    preferred_lead: str = "codex"
    fallback_lead: str = "cursor"
    local_enabled: bool = False
    classify: dict[str, Any] = field(default_factory=dict)
    factory: dict[str, Any] = field(default_factory=dict)
    media_routing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_prompt": self.task_prompt,
            "research_query": self.research_query,
            "research_useful": self.research_useful,
            "lead_agent": self.lead_agent,
            "research_agent": self.research_agent,
            "preferred_lead": self.preferred_lead,
            "fallback_lead": self.fallback_lead,
            "local_enabled": self.local_enabled,
            "speckit_scale": self.speckit_scale,
            "speckit_steps": list(self.speckit_steps),
            "attachments": list(self.attachments),
            "project_root": self.project_root,
            "classify": dict(self.classify),
            "factory": dict(self.factory),
            "media_routing": dict(self.media_routing) if self.media_routing else None,
        }

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

    # Local deterministic gates allowed via run_phase (observability / early fail).
    _LOCAL_GATE_PHASES = frozenset(
        {Phase.CLASSIFY, Phase.MAINTENANCE_REVIEW, Phase.VERIFICATION}
    )
    # Agent roles MUST go through run_all() → mode_c_agents (Orca-owned).
    _AGENT_SCHEDULE_PHASES = frozenset(
        {
            Phase.RESEARCH,
            Phase.LEAD_IMPLEMENT,
            Phase.TEST_WRITER,
            Phase.DOC_WRITER,
            Phase.LEAD_REVIEW,
        }
    )

    def run_phase(self) -> PhaseOutcome | None:
        """Run a local gate phase, or refuse agent-phase scheduling.

        Production Mode C uses ``run_all()`` (thin coordinator). ``run_phase``
        remains for classify / early-failure checks and maintenance-gate
        enforcement. It MUST NOT schedule Orca workers per agent Phase.
        """
        if self.state.stopped:
            return None
        phase = self.state.current_phase
        if phase is None:
            return None

        # Writers: still enforce maintenance gate before any further action.
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

        if phase in self._AGENT_SCHEDULE_PHASES:
            outcome = PhaseOutcome(
                phase=phase,
                status=PhaseStatus.FAILED,
                detail=(
                    "Mode C refuses per-phase agent worker scheduling; "
                    "use run_all() thin coordinator (mode_c_agents under one Orca Run)"
                ),
            )
            self._record(outcome, advance=False, stop=True)
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

    def run_all(self) -> WorkflowState:
        """Thin Mode C coordinator — NOT a phase worker scheduler.

        Production path:
        validate root → require Orca → classify (local policy) → ensure ONE run →
        Spec Kit artifacts via Orca (MEDIUM/LARGE) → ONE ``mode_c_agents`` handoff →
        maintenance gate → ONE ``mode_c_writers`` handoff when needed →
        verification → final lead review (same run).

        Phase/WorkflowState remain observability/result representation only.
        """
        if self.state.stopped:
            return self.state

        # 1–3. Validate root, require Orca, local classify + Spec Kit *policy*.
        classify_out = self._coord_record(Phase.CLASSIFY, self._phase_classify)
        if not classify_out:
            return self.state

        # 4. Ensure exactly one Orca Run (classify also attempts); fail closed.
        run_id = self.state.metadata.get("orca_run_id")
        if not (isinstance(run_id, str) and run_id.strip()):
            ensure = self._ensure_orca_run(
                objective=self.bindings.task_prompt or "Aichestra Mode C run"
            )
            if ensure is not None:
                self._fail_stopped(
                    Phase.CLASSIFY,
                    detail=str(ensure.get("detail", "Orca Run missing")),
                    result=ensure,
                )
                return self.state
            run_id = self.state.metadata.get("orca_run_id")
        if not (isinstance(run_id, str) and run_id.strip()):
            self._fail_stopped(
                Phase.CLASSIFY,
                detail=(
                    "Mode C FAIL CLOSED: Orca ok but no run_id; "
                    "refusing synthetic aichestra-run ids"
                ),
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return self.state

        # 5. MEDIUM/LARGE Spec Kit artifacts via Orca under the same Run.
        speckit_out = self._produce_speckit_via_orca()
        if not speckit_out.get("ok", False):
            self._fail_stopped(
                Phase.CLASSIFY,
                detail=str(speckit_out.get("detail", "Spec Kit artifacts failed")),
                result=speckit_out,
            )
            return self.state

        lead_agent = self._lead_agent_kind()
        if not lead_agent:
            self._fail_stopped(
                Phase.LEAD_IMPLEMENT,
                detail=(
                    "Mode C has no available/enabled lead provider "
                    "(Codex/Cursor); refuse to invent a disabled agent"
                ),
                result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
            )
            return self.state

        # 6. ONE Orca-owned orchestration handoff (research + implement).
        agents_ok = self._coord_mode_c_agents(lead_agent=lead_agent)
        if not agents_ok:
            return self.state

        # 7. Local maintenance-reviewer gate.
        if not self._coord_record(Phase.MAINTENANCE_REVIEW, self._phase_maintenance_review):
            return self.state

        # 8. Writers via ONE Orca handoff under SAME run_id when needed.
        if not self._coord_writers_if_needed():
            return self.state

        # 9. Local verification (explicit config or safe auto-detect).
        if not self._coord_record(Phase.VERIFICATION, self._phase_verification):
            return self.state

        # 10. Final lead review via Orca under SAME run_id.
        if not self._coord_record(Phase.LEAD_REVIEW, self._phase_lead_review):
            return self.state

        record_mode_c_run_id(self.bindings.project_root, str(run_id))
        self.state.metadata["thin_coordinator"] = True
        self.state.current_index = len(self.state.phases)
        return self.state

    def _fail_stopped(
        self,
        phase: Phase,
        *,
        detail: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        outcome = PhaseOutcome(
            phase=phase,
            status=PhaseStatus.FAILED,
            detail=detail,
            result=result or {},
        )
        self._record(outcome, advance=False, stop=True)

    def _coord_record(
        self,
        phase: Phase,
        fn: Callable[[], dict[str, Any]],
    ) -> bool:
        """Run a local/agent step and record PhaseOutcome for observability."""
        if phase not in self.state.phases:
            # Insert missing observability phase at end before verification/review
            # is already handled by _apply_scale_phases; skip if truly absent.
            self.state.phases.append(phase)
            self.state.phase_outcomes[phase.value] = PhaseOutcome(
                phase=phase, status=PhaseStatus.PENDING
            )
        # Align current_index for observability.
        try:
            self.state.current_index = self.state.phases.index(phase)
        except ValueError:
            pass
        self.state.phase_outcomes[phase.value] = PhaseOutcome(
            phase=phase, status=PhaseStatus.RUNNING
        )
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            self._fail_stopped(phase, detail=str(exc))
            return False
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
            return True
        self._fail_stopped(
            phase,
            detail=str(result.get("detail", "step failed")),
            result=result,
        )
        return False

    def _coord_mode_c_agents(self, *, lead_agent: str) -> bool:
        """Dispatch research + implementation via Orca under one run."""
        research_useful = Phase.RESEARCH in self.state.phases
        research_agent = self._research_agent_kind() if research_useful else None
        scale = (self.state.metadata.get("speckit_path") or {}).get("scale", "small")
        steps = tuple(
            (self.state.metadata.get("speckit_path") or {}).get("steps") or ()
        )
        package = ModeCPolicyPackage(
            run_id=str(self.state.metadata["orca_run_id"]),
            task_prompt=self.bindings.task_prompt or "Mode C task",
            research_query=self.bindings.research_query
            or self.bindings.task_prompt
            or "",
            research_useful=research_useful,
            lead_agent=lead_agent,
            research_agent=research_agent,
            preferred_lead=self.bindings.preferred_lead,
            fallback_lead=self.bindings.fallback_lead,
            local_enabled=bool(self.bindings.local_enabled),
            speckit_scale=str(scale),
            speckit_steps=tuple(str(s) for s in steps),
            attachments=tuple(self.bindings.attachments or ()),
            project_root=str(self.bindings.project_root or ""),
            classify=dict(self.state.metadata.get("classify") or {}),
            factory=dict(self.state.metadata.get("factory") or {}),
            media_routing=self.state.metadata.get("media_routing")
            if isinstance(self.state.metadata.get("media_routing"), dict)
            else None,
        )
        self.state.metadata["mode_c_policy_package"] = package.to_dict()

        # Spec Kit implement gate (artifacts must already be ready files).
        gate = self._speckit_implement_gate()
        if gate is not None:
            self._fail_stopped(
                Phase.LEAD_IMPLEMENT,
                detail=str(gate.get("detail", "Spec Kit gate blocked")),
                result=gate,
            )
            return False

        research_out: dict[str, Any] | None = None
        if research_useful:
            r_context = sanitize_mapping(
                {
                    **package.to_dict(),
                    "agent": research_agent or lead_agent,
                    "worktree": "current",
                    "policy_package": package.to_dict(),
                }
            )
            research_out = self._run_via_orca(
                prompt=(
                    "Repository research under the existing Mode C Orca Run. "
                    "Collect compact, bounded context only (files, call-flow, diff, "
                    "dependencies, config/schema facts, and open risks). "
                    + (package.research_query or package.task_prompt)
                ),
                role="research",
                context=r_context,
                read_only=True,
                adopt_worktree=False,
            )
            if not research_out.get("ok"):
                self._fail_stopped(
                    Phase.RESEARCH,
                    detail=str(research_out.get("detail", "research failed")),
                    result=research_out,
                )
                return False
            payload: dict[str, Any] = {
                "query": package.research_query or package.task_prompt,
                "via": "orca",
                "summary": str(research_out.get("output") or "")[:12000],
                "orca": {
                    "ok": True,
                    "role": "research",
                    "run_id": package.run_id,
                    "research_agent": research_agent,
                    "provider": research_out.get("provider"),
                },
            }
            self.state.metadata["research"] = payload
            self.state.metadata["orca_research"] = research_out
            self._mark_phase(
                Phase.RESEARCH,
                PhaseStatus.SUCCEEDED,
                detail="research via orca role=research",
                result={"ok": True, "research": payload},
            )
        elif Phase.RESEARCH in self.state.phases:
            self._mark_phase(
                Phase.RESEARCH,
                PhaseStatus.SKIPPED,
                detail="research not on path",
            )

        context = sanitize_mapping(
            {
                **package.to_dict(),
                "agent": lead_agent,
                "worktree": "new-child",
                "policy_package": package.to_dict(),
                "compact_research": self.state.metadata.get("research"),
                "research_dispatch": (
                    (research_out or {}).get("provider")
                    if isinstance(research_out, dict)
                    else None
                ),
            }
        )
        out = self._run_via_orca(
            prompt=(
                f"Mode C implementation under run {package.run_id}. "
                f"Lead={lead_agent}. "
                "Use compact research context already prepared for this run. "
                + package.task_prompt
            ),
            role="mode_c_agents",
            context=context,
            read_only=False,
            adopt_worktree=True,
        )
        if not out.get("ok"):
            self._fail_stopped(
                Phase.LEAD_IMPLEMENT,
                detail=str(out.get("detail", "mode_c_agents failed")),
                result=out,
            )
            return False

        self.state.metadata["orca_mode_c_agents"] = out
        self.state.metadata["lead_lead_implement"] = out.get("provider") or out
        self._mark_phase(
            Phase.LEAD_IMPLEMENT,
            PhaseStatus.SUCCEEDED,
            detail="lead_implement via mode_c_agents",
            result=out,
        )
        return True

    def _coord_writers_if_needed(self) -> bool:
        """ONE Orca writers handoff under the Mode C Run (not per-writer phases)."""
        decision = self.state.decision
        if decision is None:
            return True
        scale = (self.state.metadata.get("speckit_path") or {}).get("scale")
        if scale == SpecKitScale.SMALL.value:
            self._maybe_insert_writers_for_small(decision)

        needs_tests = bool(
            Phase.TEST_WRITER in self.state.phases and decision.needs_tests
        )
        needs_docs = bool(Phase.DOC_WRITER in self.state.phases and decision.needs_docs)

        for phase, needed in (
            (Phase.TEST_WRITER, needs_tests),
            (Phase.DOC_WRITER, needs_docs),
        ):
            if phase not in self.state.phases:
                continue
            if not needed:
                self._mark_phase(
                    phase,
                    PhaseStatus.SKIPPED,
                    detail=(
                        "maintenance: no tests"
                        if phase is Phase.TEST_WRITER
                        else "maintenance: no docs"
                    ),
                )

        if not needs_tests and not needs_docs:
            return True

        writer_agent = self._writer_agent_kind()
        if not writer_agent:
            self._fail_stopped(
                Phase.TEST_WRITER if needs_tests else Phase.DOC_WRITER,
                detail="Mode C writers: no available/enabled lead agent",
                result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
            )
            return False

        plans = self.writer_plans()
        context = sanitize_mapping(
            {
                "agent": writer_agent,
                "preferred_lead": self.bindings.preferred_lead,
                "fallback_lead": self.bindings.fallback_lead,
                "needs_tests": needs_tests,
                "needs_docs": needs_docs,
                "plans": plans,
                "task_prompt": self.bindings.task_prompt
                or self.state.metadata.get("task_prompt", ""),
                "decision": decision.to_dict(),
                "worktree": (
                    self.state.metadata.get("orca_worktree_id")
                    or self.state.metadata.get("orca_worktree_path")
                    or "current"
                ),
            }
        )
        out = self._run_via_orca(
            prompt=(
                "Mode C writers under the same Orca Run. "
                f"needs_tests={needs_tests} needs_docs={needs_docs}. "
                "Prefer updating existing tests/docs before creating new files."
            ),
            role="mode_c_writers",
            context=context,
            read_only=False,
            adopt_worktree=False,
        )
        if not out.get("ok"):
            fail_phase = Phase.TEST_WRITER if needs_tests else Phase.DOC_WRITER
            self._fail_stopped(
                fail_phase,
                detail=str(out.get("detail", "mode_c_writers failed")),
                result=out,
            )
            return False

        self.state.metadata["orca_mode_c_writers"] = out
        if needs_tests:
            self._mark_phase(
                Phase.TEST_WRITER,
                PhaseStatus.SUCCEEDED,
                detail=f"test_writer via mode_c_writers ({writer_agent})",
                result=out,
            )
        if needs_docs:
            self._mark_phase(
                Phase.DOC_WRITER,
                PhaseStatus.SUCCEEDED,
                detail=f"doc_writer via mode_c_writers ({writer_agent})",
                result=out,
            )
        return True

    def _mark_phase(
        self,
        phase: Phase,
        status: PhaseStatus,
        *,
        detail: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        if phase.value not in self.state.phase_outcomes:
            self.state.phase_outcomes[phase.value] = PhaseOutcome(
                phase=phase, status=PhaseStatus.PENDING
            )
        outcome = PhaseOutcome(
            phase=phase,
            status=status,
            detail=detail,
            result=result or {},
        )
        advance = status in {PhaseStatus.SUCCEEDED, PhaseStatus.SKIPPED}
        self._record(outcome, advance=advance, stop=False)
        if status is PhaseStatus.SUCCEEDED and phase is not Phase.CLASSIFY:
            self._report_orca_phase(phase, outcome)

    def advance(self) -> Phase | None:
        """Run the current phase once; return it only when it succeeded or skipped.

        Failed phases return None and stop the workflow so callers cannot treat
        failure as completion.

        Prefer ``run_all()`` for production Mode C (thin coordinator).
        """
        before = self.state.current_phase
        outcome = self.run_phase()
        if outcome is None:
            return None
        if outcome.status in {PhaseStatus.SUCCEEDED, PhaseStatus.SKIPPED}:
            return outcome.phase
        return None if before is not None else None

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
        """Local deterministic gates only — never schedule agent workers here."""
        if phase is Phase.CLASSIFY:
            return self._phase_classify()
        if phase is Phase.MAINTENANCE_REVIEW:
            return self._phase_maintenance_review()
        if phase is Phase.VERIFICATION:
            return self._phase_verification()
        if phase in self._AGENT_SCHEDULE_PHASES:
            return {
                "ok": False,
                "detail": (
                    f"unreachable agent phase handler for {phase.value}; "
                    "Mode C schedules agents only via run_all thin coordinator"
                ),
                "failure": FailureClass.ERROR.value,
            }
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
        # Spec Kit artifacts are produced later via Orca under this Run (run_all).
        ensure = self._ensure_orca_run(objective=prompt)
        if ensure is not None and not ensure.get("ok", False):
            # Missing Orca is a hard Mode C failure — not a lead fallback.
            if ensure.get("failure") == FailureClass.UNAVAILABLE.value:
                return ensure
            return ensure

        classify_outcome = PhaseOutcome(
            phase=Phase.CLASSIFY,
            status=PhaseStatus.SUCCEEDED,
            detail="classified",
            result={"ok": True, "detail": "classified", **data},
        )
        self._report_orca_phase(Phase.CLASSIFY, classify_outcome)
        return {"ok": True, "detail": "classified", **data}

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
        commands = list(bindings.verification_commands or [])
        root = self._effective_project_root()
        detected = False
        if not commands:
            commands = detect_verification_commands(root or bindings.project_root)
            detected = bool(commands)
            if commands:
                self.state.metadata["verification_commands_detected"] = True
                self.state.metadata["verification_commands"] = commands
        if not commands:
            report = VerificationReport(results=[])
            self.state.metadata["verification"] = report.to_dict()
            return {
                "ok": False,
                "detail": (
                    "verification not configured: no verify commands in "
                    "project config and no safe auto-detect match"
                ),
                "verification": report.to_dict(),
                "missing_verification": True,
            }
        report = run_verification(commands, cwd=root)
        self.state.metadata["verification"] = report.to_dict()
        self.state.metadata["verification_cwd"] = root
        detail = "verification ok" if report.ok else "verification failed"
        if detected:
            detail = f"{detail} (auto-detected commands)"
        return {
            "ok": report.ok,
            "detail": detail,
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

    def _effective_project_root(self) -> str | None:
        """Checkout used for gates: adopted Orca child worktree when present."""
        adopted = self.state.metadata.get("orca_worktree_path")
        if isinstance(adopted, str) and adopted.strip():
            return adopted.strip()
        return self.bindings.project_root

    def _produce_speckit_via_orca(self) -> dict[str, Any]:
        """Produce MEDIUM/LARGE Spec Kit artifacts via Orca under the Mode C Run.

        Aichestra only file-gates readiness. Python stub writing is forbidden as
        the unlock path — Orca (or the fake Orca in CI) must create the files.
        """
        meta = self.state.metadata
        scale = (meta.get("speckit_path") or {}).get("scale")
        if scale in {None, SpecKitScale.SMALL.value}:
            return {"ok": True, "detail": "small path — no Spec Kit artifacts"}

        root = self.bindings.project_root
        if not root:
            return {
                "ok": False,
                "detail": "Spec Kit artifacts require project_root",
                "failure": FailureClass.ERROR.value,
            }
        run_id = meta.get("orca_run_id")
        if not (isinstance(run_id, str) and run_id.strip()):
            return {
                "ok": False,
                "detail": "Spec Kit via Orca requires orca_run_id",
                "failure": FailureClass.ERROR.value,
            }

        required: list[str] = []
        if meta.get("brief_required") or scale in {
            SpecKitScale.MEDIUM.value,
            SpecKitScale.LARGE_HIGH_RISK.value,
        }:
            required.append("brief.md")
            meta["brief_required"] = True
        if meta.get("plan_required") or scale in {
            SpecKitScale.MEDIUM.value,
            SpecKitScale.LARGE_HIGH_RISK.value,
        }:
            required.append("plan.md")
            meta["plan_required"] = True
        if scale == SpecKitScale.LARGE_HIGH_RISK.value or meta.get("clarify_required"):
            required.append("clarify.md")
            meta["clarify_required"] = True
        if scale == SpecKitScale.LARGE_HIGH_RISK.value or meta.get("tasks_required"):
            required.append("tasks.md")
            meta["tasks_required"] = True

        lead_agent = self._lead_agent_kind()
        if not lead_agent:
            return {
                "ok": False,
                "detail": (
                    "Spec Kit via Orca: no available/enabled lead agent; "
                    "refusing disabled provider invent"
                ),
                "failure": FailureClass.UNAVAILABLE.value,
            }

        prompt = self.bindings.task_prompt or meta.get("task_prompt") or "Mode C task"
        path_info = meta.get("speckit_path") or {}
        context = sanitize_mapping(
            {
                "agent": lead_agent,
                "preferred_lead": self.bindings.preferred_lead,
                "fallback_lead": self.bindings.fallback_lead,
                "local_enabled": bool(self.bindings.local_enabled),
                "speckit_scale": scale,
                "speckit_steps": list(path_info.get("steps") or []),
                "required_artifacts": required,
                "project_root": str(root),
                "task_prompt": prompt,
                "worktree": "current",
            }
        )
        out = self._run_via_orca(
            prompt=(
                f"Produce Spec Kit {scale} artifacts under .aichestra/speckit/: "
                + ", ".join(required)
                + f". Objective: {prompt}"
            ),
            role="speckit_artifacts",
            context=context,
            read_only=False,
            adopt_worktree=False,
        )
        if not out.get("ok"):
            return {
                "ok": False,
                "detail": str(out.get("detail") or "Spec Kit Orca handoff failed"),
                "failure": out.get("failure") or FailureClass.ERROR.value,
                "orca": out,
            }

        synced = self._sync_speckit_metadata_from_files(required)
        if not synced.get("ok"):
            return synced
        meta["speckit_execution"] = "orca_artifacts_ready"
        for key in (
            "brief_satisfied",
            "plan_satisfied",
            "clarify_satisfied",
            "tasks_satisfied",
        ):
            meta.pop(key, None)
        return synced

    def _sync_speckit_metadata_from_files(
        self, required: list[str]
    ) -> dict[str, Any]:
        """Mark Spec Kit artifacts ready only when required files exist on disk."""
        meta = self.state.metadata
        root = self.bindings.project_root
        if not root:
            return {
                "ok": False,
                "detail": "Spec Kit sync requires project_root",
                "failure": FailureClass.ERROR.value,
            }
        spec_dir = Path(root) / ".aichestra" / "speckit"
        written: list[str] = []
        missing: list[str] = []
        prompt = self.bindings.task_prompt or meta.get("task_prompt") or "Mode C task"

        name_to_key = {
            "brief.md": ("brief", "brief_required"),
            "plan.md": ("plan", "plan_required"),
            "clarify.md": ("clarify", "clarify_required"),
            "tasks.md": ("tasks", "tasks_required"),
        }
        for name in required:
            path = spec_dir / name
            key, flag = name_to_key[name]
            meta[flag] = True
            if path.is_file():
                written.append(str(path))
                blob = {
                    "required": True,
                    "status": "ready",
                    "path": str(path),
                    "summary": prompt[:500],
                }
                meta[key] = blob
            else:
                missing.append(name)
                meta[key] = {
                    "required": True,
                    "status": "pending",
                    "path": str(path),
                }

        meta["speckit_artifacts"] = {
            "dir": str(spec_dir),
            "written": written,
            "lifecycle": "orca_run",
            "missing": missing,
        }
        if missing:
            return {
                "ok": False,
                "detail": (
                    "Spec Kit Orca handoff returned ok but required files missing: "
                    + ", ".join(missing)
                    + " under .aichestra/speckit/"
                ),
                "failure": FailureClass.ERROR.value,
                "missing": missing,
            }
        return {
            "ok": True,
            "detail": f"Spec Kit artifacts ready via Orca ({len(written)} files)",
            "written": written,
        }

    def _speckit_implement_gate(self) -> dict[str, Any] | None:
        """Block implement when MEDIUM/LARGE Spec Kit artifacts are still pending.

        Readiness is determined by real artifact status/path existence — never by
        ``*_satisfied`` test metadata.
        """
        meta = self.state.metadata
        scale = (meta.get("speckit_path") or {}).get("scale")
        if scale == SpecKitScale.SMALL.value:
            return None

        def _ready(key: str, required_flag: str) -> bool:
            if not meta.get(required_flag):
                return True
            blob = meta.get(key) if isinstance(meta.get(key), dict) else {}
            status = str(blob.get("status") or "pending")
            if status in {"ready", "approved", "complete"}:
                path = blob.get("path")
                if isinstance(path, str) and path.strip():
                    return Path(path).is_file()
                artifacts = meta.get("speckit_artifacts") or {}
                return bool(artifacts.get("written"))
            return False

        pending: list[str] = []
        if not _ready("brief", "brief_required"):
            pending.append("brief")
        if not _ready("plan", "plan_required"):
            pending.append("plan")
        if not _ready("clarify", "clarify_required"):
            pending.append("clarify")
        if not _ready("tasks", "tasks_required"):
            pending.append("tasks")

        if not pending:
            return None
        detail = (
            f"Spec Kit {scale} gate blocked implement; pending artifacts: "
            + ", ".join(pending)
            + ". Produce real files under .aichestra/speckit/ (no metadata overrides)."
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
            self.state.metadata.pop("orca_run_id_synthesized", None)
            return None
        if result.ok and not run_id:
            # FAIL CLOSED — never synthesize aichestra-run-{id(self)}.
            return {
                "ok": False,
                "detail": (
                    "Mode C FAIL CLOSED: Orca ensure_run succeeded but returned "
                    "no run_id; refusing synthetic run ids"
                ),
                "failure": FailureClass.ERROR.value,
                "provider": result.to_dict(),
            }
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
        if "agent" not in ctx or not ctx.get("agent"):
            agent = self._lead_agent_kind()
            if not agent:
                return {
                    "ok": False,
                    "detail": (
                        "Mode C refuse: no available/enabled lead agent for "
                        f"role={role}"
                    ),
                    "failure": FailureClass.UNAVAILABLE.value,
                }
            ctx["agent"] = agent
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
        """Refuse direct lead dispatch — Mode C always routes via Orca.

        ``hold_edit_lease`` is accepted for API compatibility and ignored;
        worktree concurrency belongs to Orca.
        """
        del hold_edit_lease  # Mode C does not manage edit leases.
        if self.bindings.orca is not None:
            return {
                "ok": False,
                "detail": (
                    f"refusing direct lead dispatch for {role}: Mode C routes "
                    "agent work through Orca only"
                ),
                "failure": FailureClass.ERROR.value,
            }
        # Without an Orca binding this controller is misconfigured for Mode C.
        return {
            "ok": False,
            "detail": (
                f"Mode C requires Orca; refusing direct lead dispatch for {role}"
            ),
            "failure": FailureClass.UNAVAILABLE.value,
        }

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
        # Never stage into the parent project root — temp dir outside checkout
        # (or absolute paths for Orca --attach).
        stage_root = tempfile.mkdtemp(prefix="aichestra-attach-")
        delivery = stage_attachments(paths, stage_root)
        bytes_ok = bool(delivery.bytes_delivered)
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
            "staged_outside_parent": True,
            "stage_root": stage_root,
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

    def _lead_agent_kind(self) -> str | None:
        """Return available/enabled lead agent kind, or None (never invent disabled)."""
        lead = self.bindings.lead
        if lead is not None:
            try:
                status = lead.probe()
            except Exception:  # noqa: BLE001
                status = None
            if status is not None and status.available:
                return lead.kind.value
        if self.bindings.providers:
            selection = select_lead(
                self.bindings.providers,
                preferred=self.bindings.preferred_lead,
                fallback=self.bindings.fallback_lead,
            )
            if selection.lead is not None:
                return selection.lead.value
        return None

    def _research_agent_kind(self) -> str | None:
        """Prefer local/opencode only when enabled+capable; else cloud lead via Orca."""
        if self.bindings.local_enabled:
            worker = self.bindings.local_worker
            if worker is not None:
                try:
                    st = worker.probe()
                except Exception:  # noqa: BLE001
                    st = None
                if st is not None and st.available:
                    return "opencode"
            for status in self.bindings.providers:
                if (
                    status.kind is ProviderKind.LOCAL_WORKER
                    and status.available
                ):
                    return "opencode"
        return self._lead_agent_kind()

    def _writer_agent_kind(self) -> str | None:
        """Prefer local-worker for writers when enabled+available, else lead."""
        if self.bindings.local_enabled:
            worker = self.bindings.local_worker
            if worker is not None:
                try:
                    st = worker.probe()
                except Exception:  # noqa: BLE001
                    st = None
                if st is not None and st.available:
                    return "opencode"
            for status in self.bindings.providers:
                if status.kind is ProviderKind.LOCAL_WORKER and status.available:
                    return "opencode"
        return self._lead_agent_kind()

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
            worktree_path=str(self.state.metadata.get("orca_worktree_path") or ""),
            workflow_phase=role,
            completed_work=list(self.state.completed),
            remaining_work=[p.value for p in self.state.phases[self.state.current_index :]],
            known_failures=[result.detail],
            next_action="Continue implement on Cursor inside the same Orca Run",
            compacted_research=dict(self.state.metadata.get("research") or {}),
            source_lead=source if source != "orca" else self._lead_agent_kind(),
            target_lead="cursor",
            orca_run_id=str(self.state.metadata.get("orca_run_id") or ""),
        )
        handoff = prepare_manual_handoff(
            packet,
            run_id=str(self.state.metadata.get("orca_run_id") or "") or None,
        )
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


def run_phase_hooks(
    workflow: ModeCRunController,
    hooks: dict[Phase, Callable[[WorkflowState], None]] | None = None,
) -> WorkflowState:
    """Compatibility helper — prefer ``workflow.run_all()`` (thin coordinator).

    When hooks are empty, delegates to ``run_all``. With hooks, runs the thin
    coordinator then invokes hooks for succeeded/skipped phases.
    """
    hooks = hooks or {}
    state = workflow.run_all()
    if hooks:
        for phase_name, outcome in list(state.phase_outcomes.items()):
            if outcome.status in {PhaseStatus.SUCCEEDED, PhaseStatus.SKIPPED}:
                hook = hooks.get(outcome.phase)
                if hook:
                    hook(state)
            del phase_name  # unused — iterate outcomes only
    return state
