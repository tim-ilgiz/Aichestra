"""Mode C thin controller: project context + one Orca Run + deterministic gates.

Ownership contract (canonical):

```text
Coordinator running under Orca:
- owns concrete workflow/DAG
- owns task dependencies and ordering
- owns inner worker selection

Orca:
- owns canonical Run lifecycle/state
- Task/Dispatch lifecycle/state
- worker lifecycle
- terminal/worktree lifecycle
- messages/handoffs lifecycle

Aichestra:
- discovery
- ExecutionTarget resolution
- policy
- security
- deterministic gates
- verification
```

Aichestra MUST NOT hard-code research → implement → writers → review.
Production flow:

```text
task + project root
→ ProjectContext discovery
→ ExecutionTarget resolution + policy
→ bounded context + policy package
→ exactly one Orca Run
→ handoff objective/context to Orca
→ coordinator under Orca owns concrete DAG / inner workers
→ Orca owns Run/Task/Dispatch/worker/terminal/worktree lifecycle
→ deterministic Aichestra gates where required
→ Mode C result from Orca Run state + gate results
```
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from aichestra.orchestration.change_signals import infer_change_signals
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
from aichestra.orchestration.project_context import (
    ProjectContext,
    discover_project_context,
)
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
from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
)
from aichestra.execution.domain import ExecutionPolicy, ExecutionTarget
from aichestra.execution.serialize import (
    CANONICAL_EXECUTION_FIELDS,
    COORDINATOR_GATES,
    EXECUTION_TARGET_CONTRACT_VERSION,
    LAUNCH_PROOF_OPERATION,
    LEGACY_COMPATIBILITY_FIELDS,
    OWNERSHIP_METADATA,
    prove_launch_invocation,
    safe_endpoint_for_context,
    serialize_execution_policy,
    serialize_execution_target,
    serialize_launch_candidate,
)
from aichestra.security.sanitize import sanitize_mapping


class GateKind(str, Enum):
    """Deterministic Aichestra gates — not an agent workflow graph."""

    PROJECT_CONTEXT = "project_context"
    POLICY = "policy"
    ATTACHMENTS = "attachments"
    ORCA_HANDOFF = "orca_handoff"
    MAINTENANCE = "maintenance"
    VERIFICATION = "verification"


class GateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# Back-compat aliases for imports that still mention Phase/PhaseStatus.
# These are NOT agent-phase schedulers — agent Phase values are gone.
Phase = GateKind
PhaseStatus = GateStatus


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

# Single Orca orchestration handoff role — coordinator under Orca owns DAG shape.
MODE_C_HANDOFF_ROLE = "mode_c_handoff"


@dataclass
class GateOutcome:
    gate: GateKind
    status: GateStatus
    detail: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def phase(self) -> GateKind:
        """Alias for older call sites that still say ``outcome.phase``."""
        return self.gate

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "phase": self.gate.value,  # observability alias only
            "status": self.status.value,
            "detail": self.detail,
            "result": dict(self.result),
        }


# Alias
PhaseOutcome = GateOutcome


@dataclass
class WorkflowState:
    """Mode C result surface: Orca Run + deterministic gates (FR-083).

    ``completed`` / ``failed`` / ``skipped`` list gate names only — never an
    Aichestra-owned agent-phase schedule.
    """

    mode: Mode
    current_gate: GateKind | None = GateKind.PROJECT_CONTEXT
    decision: MaintenanceReviewDecision | None = None
    gate_outcomes: dict[str, GateOutcome] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    stopped: bool = False

    # Compatibility: older tests/readers looked at ``phase_outcomes``.
    @property
    def phase_outcomes(self) -> dict[str, GateOutcome]:
        return self.gate_outcomes

    @property
    def phases(self) -> list[GateKind]:
        """Observability only — not a production scheduler graph."""
        order = [
            GateKind.PROJECT_CONTEXT,
            GateKind.POLICY,
            GateKind.ATTACHMENTS,
            GateKind.ORCA_HANDOFF,
            GateKind.MAINTENANCE,
            GateKind.VERIFICATION,
        ]
        return [g for g in order if g.value in self.gate_outcomes]

    @property
    def current_phase(self) -> GateKind | None:
        return self.current_gate

    @property
    def current_index(self) -> int:
        phases = self.phases
        if self.current_gate is None:
            return len(phases)
        try:
            return phases.index(self.current_gate)
        except ValueError:
            return 0

    @current_index.setter
    def current_index(self, value: int) -> None:
        phases = self.phases
        if 0 <= value < len(phases):
            self.current_gate = phases[value]
        else:
            self.current_gate = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "gates": [g.value for g in self.phases],
            "current_gate": self.current_gate.value if self.current_gate else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "gate_outcomes": {k: v.to_dict() for k, v in self.gate_outcomes.items()},
            "phase_outcomes": {k: v.to_dict() for k, v in self.gate_outcomes.items()},
            "skipped": list(self.skipped),
            "completed": list(self.completed),
            "failed": list(self.failed),
            "stopped": self.stopped,
            "metadata": dict(self.metadata),
            "orca_run_status": dict(self.metadata.get("orca_run_status") or {}),
        }


@dataclass
class WorkflowBindings:
    """Injectable Mode C bindings — Orca required; fakes in CI.

    Provider discovery feeds **policy/capabilities** for Orca. Mode C MUST NOT
    call Codex/Cursor/local-worker ``execute_task``.
    """

    orca: ProviderAdapter | None = None
    providers: list[ProviderStatus] = field(default_factory=list)
    project_root: str | None = None
    task_prompt: str = ""
    research_query: str = ""
    attachments: tuple[str, ...] = ()
    resume_run_id: str | None = None
    classify_fn: Callable[[WorkflowState], dict[str, Any]] | None = None
    maintenance_kwargs: dict[str, Any] = field(default_factory=dict)
    verification_commands: list[list[str]] = field(default_factory=list)
    preferred_lead: str = "codex"
    fallback_lead: str = "cursor"
    local_enabled: bool = False
    local_model: Any = None
    local_endpoint: str | None = None
    local_model_ref: str | None = None
    local_capabilities: tuple[str, ...] = ()
    installed_models: tuple[dict[str, Any], ...] = ()
    # Canonical ExecutionTarget layer (T172). Empty until CLI/discovery fills it.
    execution_targets: tuple[ExecutionTarget, ...] = ()
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    aichestra_repo_root: str | None = None


@dataclass
class ModeCPolicyPackage:
    """Policy + ProjectContext package handed to Orca (not a worker schedule).

    Canonical execution facts: ``execution_targets`` (runnable only) +
    ``execution_target_candidates`` (non-dispatchable) + ``execution_policy``.
    Legacy preferred_lead / local_* / provider_policy remain as secondary
    compatibility seams for existing Orca adapter bootstrap — not the source of
    truth for new inner-worker policy.
    """

    run_id: str
    task_prompt: str
    research_query: str
    project_root: str
    project_context: dict[str, Any]
    aichestra_repo_root: str = ""
    preferred_lead: str = "codex"
    fallback_lead: str = "cursor"
    local_enabled: bool = False
    local_endpoint: str | None = None
    local_model_ref: str | None = None
    local_capabilities: tuple[str, ...] = ()
    installed_models: tuple[dict[str, Any], ...] = ()
    provider_policy: dict[str, Any] = field(default_factory=dict)
    execution_targets: tuple[dict[str, Any], ...] = ()
    execution_target_candidates: tuple[dict[str, Any], ...] = ()
    execution_policy: dict[str, Any] = field(default_factory=dict)
    execution_target_contract_version: int = EXECUTION_TARGET_CONTRACT_VERSION
    speckit_scale: str = "small"
    speckit_steps: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()
    classify: dict[str, Any] = field(default_factory=dict)
    media_routing: dict[str, Any] | None = None
    precedence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_prompt": self.task_prompt,
            "research_query": self.research_query,
            "project_root": self.project_root,
            "aichestra_repo_root": self.aichestra_repo_root,
            "project_context": dict(self.project_context),
            # Canonical ExecutionTarget contract (T172 / T163).
            "execution_target_contract_version": self.execution_target_contract_version,
            "execution_targets": [dict(t) for t in self.execution_targets],
            "execution_target_candidates": [
                dict(t) for t in self.execution_target_candidates
            ],
            "execution_policy": dict(self.execution_policy),
            "canonical_execution_fields": list(CANONICAL_EXECUTION_FIELDS),
            "launch_proof_operation": LAUNCH_PROOF_OPERATION,
            # Legacy compatibility seams — secondary; not inner-worker SoT.
            "preferred_lead": self.preferred_lead,
            "fallback_lead": self.fallback_lead,
            "local_enabled": self.local_enabled,
            "local_endpoint": self.local_endpoint,
            "local_model_ref": self.local_model_ref,
            "local_capabilities": list(self.local_capabilities),
            "installed_models": [dict(m) for m in self.installed_models],
            "provider_policy": dict(self.provider_policy),
            "legacy_compatibility_fields": list(LEGACY_COMPATIBILITY_FIELDS),
            "speckit_scale": self.speckit_scale,
            "speckit_steps": list(self.speckit_steps),
            "attachments": list(self.attachments),
            "classify": dict(self.classify),
            "media_routing": dict(self.media_routing) if self.media_routing else None,
            "precedence": list(self.precedence),
            # Explicit ownership (never ambiguous orchestration_owner=orca).
            **dict(OWNERSHIP_METADATA),
        }


class ModeCRunController:
    """Thin Mode C controller: context/policy → one Orca Run → gates."""

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
        self.state = WorkflowState(mode=mode)
        self.state.metadata["task_prompt"] = self.bindings.task_prompt
        self.state.metadata["research_useful_hint"] = research_useful
        self.state.metadata["canonical_orchestration"] = "orca_run"
        self.state.metadata["controller"] = "ModeCRunController"
        self.state.metadata.update(OWNERSHIP_METADATA)
        self.state.metadata["aichestra_owns_agent_phases"] = False
        if self.bindings.resume_run_id and self.bindings.resume_run_id.strip():
            self.state.metadata["orca_run_id"] = self.bindings.resume_run_id.strip()
            self.state.metadata["orca_run_resumed"] = True
        self._attach_stage_root: str | None = None
        self._bootstrap_target = None
        self._prepared_bootstrap_launch = None
        self._inflight_handoff_context: dict[str, Any] | None = None

    def apply_maintenance_review(self, **kwargs: Any) -> MaintenanceReviewDecision:
        decision = review_change(**kwargs)
        self.state.decision = decision
        self.state.metadata["maintenance_review"] = decision.to_dict()
        return decision

    def run_phase(self) -> GateOutcome | None:
        """Validation helper only — no Orca Run creation or agent scheduling.

        Checks project root, Orca binding/availability, and a runnable
        bootstrap ExecutionTarget. Prefer ``run_all()`` for Mode C execution.
        """
        if self.state.stopped:
            return None
        return self._early_validate_or_fail()

    def run_all(self) -> WorkflowState:
        """Project-execution control plane — not an agent-phase pipeline."""
        if self.state.stopped:
            return self.state

        try:
            if not self._gate_project_context():
                return self.state
            if not self._gate_policy():
                return self.state
            if not self._gate_attachments():
                return self.state

            from aichestra.execution.launch_strategies import (
                LaunchContext,
                abort_prepared,
                prove_bootstrap_launch,
                select_bootstrap_candidate,
                serialize_prepared_launch,
            )
            from aichestra.providers.orca import resolve_orca_binary

            candidate = select_bootstrap_candidate(self.bindings.execution_targets)
            if candidate is None:
                self._fail_gate(GateKind.ORCA_HANDOFF,
                    detail="Mode C has no runnable bootstrap ExecutionTarget",
                    result={"ok": False, "failure": FailureClass.UNAVAILABLE.value})
                return self.state

            orca = self.bindings.orca
            assert orca is not None
            status = orca.probe()
            binary = status.binary_path or resolve_orca_binary()
            if not binary:
                self._fail_gate(
                    GateKind.ORCA_HANDOFF,
                    detail="Mode C requires Orca binary for bootstrap launch proof",
                    result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
                )
                return self.state

            # candidate → prepare/attest → runnable → only then create/resume Run
            launch_ctx = LaunchContext(
                binary=binary,
                worktree="current",
                terminal_handle=(
                    str(
                        os.environ.get("ORCA_WORKER_TERMINAL_HANDLE") or ""
                    ).strip()
                    or (candidate.launch_ref or "").strip()
                    or None
                ),
                run=subprocess.run,
            )
            try:
                bootstrap, prepared = prove_bootstrap_launch(candidate, launch_ctx)
            except ValueError as exc:
                self._fail_gate(
                    GateKind.ORCA_HANDOFF,
                    detail=f"Mode C bootstrap launch proof failed: {exc}",
                    result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
                )
                return self.state

            if not bootstrap.runnable:
                abort_prepared(prepared, launch_ctx)
                self._fail_gate(
                    GateKind.ORCA_HANDOFF,
                    detail="Mode C has no runnable bootstrap ExecutionTarget",
                    result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
                )
                return self.state

            self._bootstrap_target = bootstrap
            self._prepared_bootstrap_launch = prepared
            self.state.metadata["bootstrap_execution_target"] = serialize_execution_target(
                bootstrap
            )
            self.state.metadata["bootstrap_prepared_launch"] = serialize_prepared_launch(
                prepared
            )

            ensure = self._ensure_orca_run(
                objective=self.bindings.task_prompt or "Aichestra Mode C run"
            )
            if ensure is not None:
                abort_prepared(prepared, launch_ctx)
                self._prepared_bootstrap_launch = None
                self._fail_gate(
                    GateKind.ORCA_HANDOFF,
                    detail=str(ensure.get("detail", "Orca Run missing")),
                    result=ensure,
                )
                return self.state

            run_id = self.state.metadata.get("orca_run_id")
            if not (isinstance(run_id, str) and run_id.strip()):
                abort_prepared(prepared, launch_ctx)
                self._prepared_bootstrap_launch = None
                self._fail_gate(
                    GateKind.ORCA_HANDOFF,
                    detail=(
                        "Mode C FAIL CLOSED: Orca ok but no run_id; "
                        "refusing synthetic aichestra-run ids"
                    ),
                    result={"ok": False, "failure": FailureClass.ERROR.value},
                )
                return self.state

            if not self._handoff_to_orca(str(run_id).strip()):
                return self.state

            # Maintenance + verification must have been answered while the
            # coordinator was live so canonical Orca outcome can agree.
            if self.state.decision is None:
                self._fail_gate(GateKind.MAINTENANCE, detail="Coordinator omitted maintenance gate handshake")
                return self.state
            if GateKind.VERIFICATION.value not in self.state.completed:
                self._fail_gate(
                    GateKind.VERIFICATION,
                    detail="Coordinator omitted verification gate handshake",
                )
                return self.state

            self._finalize_orca_run_status(ok=True)
            if self.state.stopped:
                return self.state
            record_mode_c_run_id(self.bindings.project_root, str(run_id))
            self.state.metadata["orchestration_shape_agnostic"] = True
            self.state.current_gate = None
            return self.state
        finally:
            self._cleanup_attachment_staging()

    def advance(self) -> GateKind | None:
        """Compatibility helper — delegates to early validate or run_all piece."""
        outcome = self.run_phase()
        if outcome is None:
            return None
        if outcome.status in {GateStatus.SUCCEEDED, GateStatus.SKIPPED}:
            return outcome.gate
        return None

    # ------------------------------------------------------------------ gates

    def _early_validate_or_fail(self) -> GateOutcome:
        """Fail-closed preflight without creating an Orca Run (no side effects)."""
        root = self.bindings.project_root
        if not root or not str(root).strip():
            outcome = GateOutcome(
                gate=GateKind.PROJECT_CONTEXT,
                status=GateStatus.FAILED,
                detail=(
                    "Mode C requires --project-root before any provider execution; "
                    "refusing ambient cwd writes"
                ),
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            self._record(outcome, stop=True)
            return outcome
        if not Path(root).is_dir():
            outcome = GateOutcome(
                gate=GateKind.PROJECT_CONTEXT,
                status=GateStatus.FAILED,
                detail=f"Mode C project-root is not a directory: {root}",
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            self._record(outcome, stop=True)
            return outcome
        if self.bindings.orca is None:
            outcome = GateOutcome(
                gate=GateKind.ORCA_HANDOFF,
                status=GateStatus.FAILED,
                detail=(
                    "Mode C requires Orca as the orchestration control plane; "
                    "native Codex/Cursor remain Mode A and are not used as Mode C fallback"
                ),
                result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
            )
            self._record(outcome, stop=True)
            return outcome
        status = self.bindings.orca.probe()
        if not status.available:
            outcome = GateOutcome(
                gate=GateKind.ORCA_HANDOFF,
                status=GateStatus.FAILED,
                detail=status.detail or "Orca unavailable",
                result={
                    "ok": False,
                    "failure": (status.failure or FailureClass.UNAVAILABLE).value,
                },
            )
            self._record(outcome, stop=True)
            return outcome
        from aichestra.execution.launch_strategies import select_bootstrap_candidate
        if select_bootstrap_candidate(self.bindings.execution_targets) is None:
            outcome = GateOutcome(
                gate=GateKind.ORCA_HANDOFF,
                status=GateStatus.FAILED,
                detail="Mode C has no runnable bootstrap ExecutionTarget",
                result={"ok": False, "failure": FailureClass.UNAVAILABLE.value},
            )
            self._record(outcome, stop=True)
            return outcome
        outcome = GateOutcome(
            gate=GateKind.PROJECT_CONTEXT,
            status=GateStatus.SUCCEEDED,
            detail="early validate ok",
            result={"ok": True},
        )
        self._record(outcome)
        return outcome

    def _gate_project_context(self) -> bool:
        root = self.bindings.project_root
        if not root or not str(root).strip():
            self._fail_gate(
                GateKind.PROJECT_CONTEXT,
                detail=(
                    "Mode C requires --project-root before any provider execution; "
                    "refusing ambient cwd writes"
                ),
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return False
        if not Path(root).is_dir():
            self._fail_gate(
                GateKind.PROJECT_CONTEXT,
                detail=f"Mode C project-root is not a directory: {root}",
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return False
        try:
            ctx = discover_project_context(root)
        except Exception as exc:  # noqa: BLE001
            self._fail_gate(
                GateKind.PROJECT_CONTEXT,
                detail=str(exc),
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return False
        self.state.metadata["project_context"] = ctx.to_dict()
        self.state.metadata["factory"] = dict(ctx.factory)
        self._succeed_gate(
            GateKind.PROJECT_CONTEXT,
            detail="project context discovered",
            result={"ok": True, "project_context": ctx.to_dict()},
        )
        return True

    def _gate_policy(self) -> bool:
        bindings = self.bindings
        prompt = bindings.task_prompt or "orchestrated task"
        self.state.metadata["task_prompt"] = prompt

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
        # Spec Kit scale is policy for Orca — not an Aichestra phase schedule.
        self.state.metadata["speckit_policy_only"] = True

        project_ctx = self.state.metadata.get("project_context") or {}
        speckit = project_ctx.get("speckit") or {}
        if speckit.get("owns_canonical"):
            self.state.metadata["speckit_canonical"] = {
                "owner": "project",
                "root": speckit.get("canonical_root"),
                "markers": list(speckit.get("markers") or []),
                "competing_aichestra_speckit_forbidden": True,
            }
        else:
            self.state.metadata["speckit_canonical"] = {
                "owner": "none",
                "compatibility_dir": speckit.get("compatibility_dir"),
                "competing_aichestra_speckit_forbidden": False,
            }

        provider_policy = self._build_provider_policy()
        self.state.metadata["provider_policy"] = provider_policy

        self._succeed_gate(
            GateKind.POLICY,
            detail="policy/classify ready for Orca",
            result={"ok": True, "classify": data, "provider_policy": provider_policy},
        )
        return True

    def _gate_attachments(self) -> bool:
        paths = list(self.bindings.attachments or ())
        if not paths:
            self._succeed_gate(
                GateKind.ATTACHMENTS,
                detail="no attachments",
                result={"ok": True, "skipped": True},
            )
            return True

        from aichestra.providers.attachments import stage_attachments

        prompt = self.bindings.task_prompt or ""
        decision = route_media(
            paths,
            text=prompt,
            local_model=self.bindings.local_model,
            local_enabled=bool(self.bindings.local_enabled),
            prefer_orca_attachments=True,
        )
        if decision.vision_required and not decision.allowed:
            self._fail_gate(
                GateKind.ATTACHMENTS,
                detail=decision.reason or "vision attachments cannot be routed",
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return False

        stage_root = tempfile.mkdtemp(prefix="aichestra-attach-")
        self._attach_stage_root = stage_root
        delivery = stage_attachments(paths, stage_root)
        if delivery.missing:
            self._fail_gate(
                GateKind.ATTACHMENTS,
                detail=(
                    "Mode C FAIL CLOSED: missing attachment path(s): "
                    + ", ".join(delivery.missing)
                ),
                result={
                    "ok": False,
                    "failure": FailureClass.ERROR.value,
                    "attachment_delivery": delivery.to_dict(),
                },
            )
            return False
        if not delivery.bytes_delivered:
            self._fail_gate(
                GateKind.ATTACHMENTS,
                detail="Mode C FAIL CLOSED: attachment bytes not delivered",
                result={
                    "ok": False,
                    "failure": FailureClass.ERROR.value,
                    "attachment_delivery": delivery.to_dict(),
                },
            )
            return False

        if delivery.staged:
            self.bindings.attachments = tuple(delivery.staged)
        elif delivery.resolved:
            self.bindings.attachments = tuple(delivery.resolved)

        routing = {
            "vision_required": decision.vision_required,
            "allowed": decision.allowed,
            "provider_hint": decision.provider_hint,
            "reason": decision.reason,
            "summarized_text": decision.summarized_text,
            "paths": list(self.bindings.attachments),
            "bytes_delivered": True,
            "attachment_delivery": delivery.to_dict(),
            "staged_outside_parent": True,
            "stage_root": stage_root,
        }
        self.state.metadata["media_routing"] = routing
        self._succeed_gate(
            GateKind.ATTACHMENTS,
            detail=f"attachments ready ({len(self.bindings.attachments)})",
            result={"ok": True, "media_routing": routing},
        )
        return True

    def _handoff_to_orca(self, run_id: str) -> bool:
        """ONE orchestration handoff — Orca owns Run lifecycle; coordinator owns DAG."""
        from aichestra.execution.launch_strategies import serialize_prepared_launch

        package = self._build_policy_package(run_id)
        self.state.metadata["mode_c_policy_package"] = package.to_dict()
        prove_cmd = prove_launch_invocation(
            project_root=package.project_root or "<root>",
            repo_root=package.aichestra_repo_root or None,
        )

        context = sanitize_mapping(
            {
                **package.to_dict(),
                "policy_package": package.to_dict(),
                "project_context": package.project_context,
                "worktree": "current",
                # Canonical ExecutionTarget facts for the coordinator (T172).
                "execution_targets": list(package.execution_targets),
                "execution_target_candidates": list(
                    package.execution_target_candidates
                ),
                "bootstrap_execution_target": serialize_execution_target(self._bootstrap_target),
                "prepared_bootstrap_launch": (
                    serialize_prepared_launch(self._prepared_bootstrap_launch)
                    if self._prepared_bootstrap_launch is not None
                    else None
                ),
                "execution_policy": dict(package.execution_policy),
                "execution_target_contract_version": (
                    package.execution_target_contract_version
                ),
                "launch_proof_operation": LAUNCH_PROOF_OPERATION,
                "aichestra_repo_root": package.aichestra_repo_root,
                # Capability hints — ExecutionTargets are canonical; legacy secondary.
                "capabilities": {
                    "execution_targets": list(package.execution_targets),
                    "execution_target_candidates": list(
                        package.execution_target_candidates
                    ),
                    "execution_policy": dict(package.execution_policy),
                    "legacy_compatibility": {
                        "preferred_lead": package.preferred_lead,
                        "fallback_lead": package.fallback_lead,
                        "local_enabled": package.local_enabled,
                        "local_endpoint": package.local_endpoint,
                        "local_model_ref": package.local_model_ref,
                        "local_capabilities": list(package.local_capabilities),
                        "installed_models": list(package.installed_models),
                        "provider_policy": package.provider_policy,
                    },
                    # Keep flat legacy keys for existing adapter tests/seams.
                    "preferred_lead": package.preferred_lead,
                    "fallback_lead": package.fallback_lead,
                    "local_enabled": package.local_enabled,
                    "local_endpoint": package.local_endpoint,
                    "local_model_ref": package.local_model_ref,
                    "local_capabilities": list(package.local_capabilities),
                    "installed_models": list(package.installed_models),
                    "provider_policy": package.provider_policy,
                },
            }
        )
        out = self._run_via_orca(
            prompt=(
                "Mode C handoff under a single Orca Run. "
                "Coordinator running under Orca owns the concrete workflow/DAG, "
                "task dependencies and ordering, and inner worker selection. "
                "Orca owns canonical Run/Task/Dispatch lifecycle, worker "
                "lifecycle, terminal/worktree lifecycle, and messages/handoffs. "
                "Aichestra supplies discovery, ExecutionTargets, policy, "
                "security, and deterministic gates/verification only. "
                "Honor project-owned AGENTS/Spec Kit/Factory "
                "instructions from project_context with stated precedence. "
                "For inner workers, Dispatch only ExecutionTargets listed in "
                "execution_targets (enabled, available, capable, allowed, and "
                "runnable). execution_target_candidates are NOT dispatchable; "
                f"promote a candidate only via `{LAUNCH_PROOF_OPERATION}` / "
                f"`{prove_cmd}` (deterministic structured process attestation; Aichestra "
                "re-resolves binding from trusted config using --repo-root) which returns a proven "
                "runnable target + prepared_launch handle — never DIY terminal "
                "show/tail recipes, embedded shell launch strings, or screen "
                "substring matching. "
                "Reuse the returned terminal_handle exactly (no second bridge). "
                "orca-existing-terminal targets are dispatchable only with launch_ref. "
                "Target locality may be local, remote, or cloud according to "
                "ExecutionPolicy. "
                "Do not infer workers from raw providers. "
                "Do not impose product-name phase routing. "
                "Unsupported targets MUST NOT be dispatched. "
                f"Objective: {package.task_prompt}"
            ),
            role=MODE_C_HANDOFF_ROLE,
            context=context,
            read_only=False,
            adopt_worktree=True,
        )
        if self.state.stopped:
            return False
        if not out.get("ok"):
            self._fail_gate(
                GateKind.ORCA_HANDOFF,
                detail=str(out.get("detail", "mode_c_handoff failed")),
                result=out,
            )
            return False

        self.state.metadata["orca_mode_c_handoff"] = out
        self.state.metadata["orca_run_status"] = {
            "run_id": run_id,
            "handoff_ok": True,
            "role": MODE_C_HANDOFF_ROLE,
            "provider": out.get("provider"),
            "source": "orca_run",
        }
        self._succeed_gate(
            GateKind.ORCA_HANDOFF,
            detail=(
                "handoff to Orca complete; "
                "coordinator owns DAG; Orca owns Run lifecycle"
            ),
            result=out,
        )
        return True

    def _gate_maintenance(self) -> bool:
        bindings = self.bindings
        summary = (
            bindings.task_prompt
            or self.state.metadata.get("classify", {}).get("prompt", "change")
        )
        root = self._effective_project_root()
        # Fail-closed: never verify/review parent when child claimed but missing.
        adopt_err = self.state.metadata.get("orca_worktree_adopt_error")
        if adopt_err:
            self._fail_gate(
                GateKind.MAINTENANCE,
                detail=str(adopt_err),
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return False

        inferred = infer_change_signals(
            project_root=root,
            change_summary=summary,
        )
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
        # Gate result is policy feedback — Aichestra does not schedule writers.
        self.state.metadata["maintenance_gate_for_orca"] = decision.to_dict()
        if _governance_pending(decision):
            self.state.metadata["governance_pending"] = {
                "ADR_REQUIRED": decision.ADR_REQUIRED,
                "SPEC_UPDATE": decision.SPEC_UPDATE,
            }
        else:
            self.state.metadata.pop("governance_pending", None)

        self._succeed_gate(
            GateKind.MAINTENANCE,
            detail="maintenance-reviewer gate complete",
            result={"ok": True, "decision": decision.to_dict()},
        )
        return True

    def _gate_verification(self) -> bool:
        if self.state.metadata.get("orca_worktree_adopt_error"):
            self._fail_gate(
                GateKind.VERIFICATION,
                detail=str(self.state.metadata["orca_worktree_adopt_error"]),
                result={"ok": False, "failure": FailureClass.ERROR.value},
            )
            return False

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
            self._fail_gate(
                GateKind.VERIFICATION,
                detail=(
                    "verification not configured: no verify commands in "
                    "project config and no safe auto-detect match"
                ),
                result={
                    "ok": False,
                    "verification": report.to_dict(),
                    "missing_verification": True,
                },
            )
            return False
        report = run_verification(commands, cwd=root)
        self.state.metadata["verification"] = report.to_dict()
        self.state.metadata["verification_cwd"] = root
        detail = "verification ok" if report.ok else "verification failed"
        if detected:
            detail = f"{detail} (auto-detected commands)"
        if not report.ok:
            self._fail_gate(
                GateKind.VERIFICATION,
                detail=detail,
                result={"ok": False, "verification": report.to_dict()},
            )
            return False
        self._succeed_gate(
            GateKind.VERIFICATION,
            detail=detail,
            result={"ok": True, "verification": report.to_dict()},
        )
        return True

    # ----------------------------------------------------------- helpers

    def _build_provider_policy(self) -> dict[str, Any]:
        providers = list(self.bindings.providers or [])
        by_kind: dict[str, Any] = {}
        for status in providers:
            by_kind[status.kind.value] = {
                "available": status.available,
                "role": status.role.value if status.role else None,
                "detail": status.detail,
                "failure": status.failure.value if status.failure else None,
            }
        selection = None
        if providers:
            selection = select_lead(
                providers,
                preferred=self.bindings.preferred_lead,
                fallback=self.bindings.fallback_lead,
            )
        lead_kind = selection.lead.value if selection and selection.lead else None
        local_available = self.bindings.local_enabled and any(
            status.kind is ProviderKind.LOCAL_WORKER and status.available
            for status in providers
        )

        return {
            "preferred_lead": self.bindings.preferred_lead,
            "fallback_lead": self.bindings.fallback_lead,
            "suggested_lead": lead_kind,
            "local_enabled": bool(self.bindings.local_enabled),
            "local_available": local_available,
            "local_endpoint": safe_endpoint_for_context(self.bindings.local_endpoint),
            "local_model_ref": self.bindings.local_model_ref,
            "local_capabilities": list(self.bindings.local_capabilities),
            "installed_models": [dict(m) for m in self.bindings.installed_models],
            "providers": by_kind,
            # Coordinator under Orca selects workers; Aichestra supplies policy.
            "scheduler": "coordinator_under_orca",
        }

    def _build_policy_package(self, run_id: str) -> ModeCPolicyPackage:
        project_ctx = dict(self.state.metadata.get("project_context") or {})
        scale = (self.state.metadata.get("speckit_path") or {}).get("scale", "small")
        steps = tuple(
            (self.state.metadata.get("speckit_path") or {}).get("steps") or ()
        )
        # Bootstrap prove may promote a provisionable binding to runnable without
        # mutating bindings; prefer the proven bootstrap target for the package.
        proven_by_id: dict[str, ExecutionTarget] = {}
        if self._bootstrap_target is not None and self._bootstrap_target.runnable:
            proven_by_id[self._bootstrap_target.id] = self._bootstrap_target

        def _effective(target: ExecutionTarget) -> ExecutionTarget:
            return proven_by_id.get(target.id, target)

        serialized_targets = tuple(
            serialize_execution_target(_effective(t))
            for t in self.bindings.execution_targets
            if _effective(t).runnable
        )
        serialized_candidates = tuple(
            serialize_launch_candidate(t)
            for t in self.bindings.execution_targets
            if t.provisionable and t.id not in proven_by_id
        )
        serialized_policy = serialize_execution_policy(self.bindings.execution_policy)
        return ModeCPolicyPackage(
            run_id=run_id,
            task_prompt=self.bindings.task_prompt or "Mode C task",
            research_query=self.bindings.research_query
            or self.bindings.task_prompt
            or "",
            project_root=str(self.bindings.project_root or ""),
            aichestra_repo_root=str(self.bindings.aichestra_repo_root or ""),
            project_context=project_ctx,
            preferred_lead=self.bindings.preferred_lead,
            fallback_lead=self.bindings.fallback_lead,
            local_enabled=bool(self.bindings.local_enabled),
            local_endpoint=safe_endpoint_for_context(self.bindings.local_endpoint),
            local_model_ref=self.bindings.local_model_ref,
            local_capabilities=tuple(self.bindings.local_capabilities or ()),
            installed_models=tuple(self.bindings.installed_models or ()),
            provider_policy=dict(self.state.metadata.get("provider_policy") or {}),
            execution_targets=serialized_targets,
            execution_target_candidates=serialized_candidates,
            execution_policy=serialized_policy,
            execution_target_contract_version=EXECUTION_TARGET_CONTRACT_VERSION,
            speckit_scale=str(scale),
            speckit_steps=tuple(str(s) for s in steps),
            attachments=tuple(self.bindings.attachments or ()),
            classify=dict(self.state.metadata.get("classify") or {}),
            media_routing=self.state.metadata.get("media_routing")
            if isinstance(self.state.metadata.get("media_routing"), dict)
            else None,
            precedence=tuple(project_ctx.get("precedence") or ()),
        )

    def _effective_project_root(self) -> str | None:
        adopted = self.state.metadata.get("orca_worktree_path")
        if isinstance(adopted, str) and adopted.strip():
            return adopted.strip()
        return self.bindings.project_root

    def _ensure_orca_run(self, *, objective: str | None = None) -> dict[str, Any] | None:
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
                # Run creation is control-plane bookkeeping, not a read-only
                # worker. Appending a read-only constraint here contradicts
                # implementation objectives later executed in this same Run.
                read_only=False,
            )
        )
        self.state.metadata["orca_ensure_run"] = result.to_dict()
        run_id = (result.metadata or {}).get("run_id")
        if result.ok and isinstance(run_id, str) and run_id.strip():
            self.state.metadata["orca_run_id"] = run_id.strip()
            self.state.metadata.pop("orca_run_id_synthesized", None)
            return None
        if result.ok and not run_id:
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

    def _adopt_orca_worktree(self, result: ProviderTaskResult) -> dict[str, Any] | None:
        """Adopt child worktree fail-closed when claimed but locator missing."""
        meta = result.metadata or {}
        path = meta.get("worktree_path")
        worktree_id = meta.get("worktree_id")
        policy = meta.get("integration_policy")
        parent = self.bindings.project_root
        parent_resolved = Path(parent).resolve() if parent else None

        claimed_child = False
        if isinstance(path, str) and path.strip() and parent_resolved is not None:
            try:
                if Path(path).resolve() != parent_resolved:
                    claimed_child = True
            except OSError:
                claimed_child = True
        if policy == "adopt_child_worktree" and worktree_id:
            # Explicit child claim — require a resolvable path distinct from parent
            # OR a path that exists. Same-as-parent with this policy is ignored
            # (fake/legacy), not treated as a confirmed child.
            if isinstance(path, str) and path.strip() and parent_resolved is not None:
                try:
                    if Path(path).resolve() != parent_resolved:
                        claimed_child = True
                except OSError:
                    claimed_child = True

        if claimed_child:
            if not (isinstance(path, str) and path.strip()):
                err = (
                    "Mode C FAIL CLOSED: editing worker claimed a child worktree "
                    "but locator path is missing; refusing parent-checkout verification"
                )
                self.state.metadata["orca_worktree_adopt_error"] = err
                return {"ok": False, "detail": err, "failure": FailureClass.ERROR.value}
            resolved = Path(path.strip())
            if not resolved.is_dir():
                err = (
                    "Mode C FAIL CLOSED: child worktree locator not confirmed "
                    f"({path}); refusing parent-checkout verification"
                )
                self.state.metadata["orca_worktree_adopt_error"] = err
                return {"ok": False, "detail": err, "failure": FailureClass.ERROR.value}
            self.state.metadata["orca_worktree_path"] = str(resolved.resolve())
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
            return None

        # No confirmed child — gates use parent; record optional ids for observability.
        if isinstance(worktree_id, str) and worktree_id.strip():
            self.state.metadata["orca_worktree_id"] = worktree_id.strip()
        self.state.metadata["orca_integration"] = {
            "policy": "parent_or_unspecified",
            "parent_project_root": parent,
            "worktree_path": parent,
            "note": "No confirmed child worktree; gates use project root",
        }
        return None

    def _adopt_inflight_worktree(self) -> dict[str, Any] | None:
        """Adopt the live coordinator worktree before answering in-Run gates."""
        ctx = self._inflight_handoff_context
        if not isinstance(ctx, dict):
            return None
        if self.state.metadata.get("orca_worktree_path"):
            return None
        path = ctx.get("worktree_path")
        if not isinstance(path, str) or not path.strip():
            return None
        stub = ProviderTaskResult(
            ok=True,
            metadata={
                "worktree_path": path.strip(),
                "worktree_id": ctx.get("worktree_id"),
                "integration_policy": ctx.get("integration_policy")
                or "adopt_child_worktree",
            },
        )
        return self._adopt_orca_worktree(stub)

    def _run_via_orca(
        self,
        *,
        prompt: str,
        role: str,
        context: Mapping[str, Any] | None = None,
        read_only: bool = False,
        adopt_worktree: bool = False,
    ) -> dict[str, Any]:
        ensure = self._ensure_orca_run(objective=prompt)
        if ensure is not None:
            return ensure

        orca = self.bindings.orca
        assert orca is not None
        run_id = str(self.state.metadata["orca_run_id"])
        ctx = sanitize_mapping(dict(context or {}))
        ctx["run_id"] = run_id
        self._inflight_handoff_context = ctx if role == MODE_C_HANDOFF_ROLE else None

        root = self._effective_project_root()
        try:
            result = orca.execute_task(
                ProviderTaskRequest(
                    prompt=prompt,
                    role=role,
                    context=ctx,
                    cwd=root,
                    timeout_seconds=600.0,
                    read_only=read_only,
                    attachments=tuple(self.bindings.attachments or ()),
                    execution_target=self._bootstrap_target if role == MODE_C_HANDOFF_ROLE else None,
                    gate_handler=self._answer_orca_gate if role == MODE_C_HANDOFF_ROLE else None,
                )
            )
        finally:
            self._inflight_handoff_context = None
        self.state.metadata[f"orca_{role}"] = result.to_dict()
        if result.ok and adopt_worktree and not self.state.metadata.get("orca_worktree_path"):
            adopt_fail = self._adopt_orca_worktree(result)
            if adopt_fail is not None:
                return adopt_fail
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

    def _answer_orca_gate(self, gate: str) -> dict[str, Any]:
        """Deterministic callback, invoked by the adapter during a blocking Orca ask."""
        name = str(gate or "").strip().lower()
        run_id = self.state.metadata.get("orca_run_id")
        adopt_fail = self._adopt_inflight_worktree()
        if adopt_fail is not None:
            return {
                "ok": False,
                "run_id": run_id,
                "gate": name,
                "detail": adopt_fail.get("detail"),
            }
        if name not in COORDINATOR_GATES:
            raise ValueError("Unsupported deterministic gate")
        if name == "maintenance":
            if not self._gate_maintenance():
                return {"ok": False, "run_id": run_id, "gate": name}
            return {
                "ok": True,
                "run_id": run_id,
                "gate": name,
                "decision": self.state.decision.to_dict(),
            }
        # verification — authoritative result for the same Orca Run
        ok = self._gate_verification()
        return {
            "ok": ok,
            "run_id": run_id,
            "gate": name,
            "verification": dict(self.state.metadata.get("verification") or {}),
        }

    def _finalize_orca_run_status(self, *, ok: bool) -> None:
        status = dict(self.state.metadata.get("orca_run_status") or {})
        status.update(
            {
                "run_id": self.state.metadata.get("orca_run_id"),
                "ok": ok,
                "gates_completed": list(self.state.completed),
                "gates_failed": list(self.state.failed),
                "source": "orca_run_plus_aichestra_gates",
            }
        )
        run_id = self.state.metadata.get("orca_run_id")
        if run_id and self.bindings.orca:
            result = self.bindings.orca.execute_task(ProviderTaskRequest(
                prompt="Read canonical Run state", role="run_status", read_only=True,
                context={"run_id": run_id}, cwd=self.bindings.project_root,
            ))
            status["canonical_read"] = result.to_dict()
            status["canonical_state"] = result.metadata.get("receipt")
            status["source"] = "orca_run_show_plus_aichestra_gates" if result.ok else "aichestra_gates_run_unverifiable"
        canonical_ok = bool(run_id and self.bindings.orca and result.ok
                            and result.metadata.get("settled") is True)
        status["ok"] = bool(ok and canonical_ok)
        if ok and not canonical_ok:
            self._record(GateOutcome(gate=GateKind.ORCA_HANDOFF,
                status=GateStatus.FAILED,
                detail="Canonical Orca Run is failed, unsettled, or unverifiable",
                result={"canonical_read": status.get("canonical_read")}), stop=True)
            status["gates_failed"] = list(self.state.failed)
        self.state.metadata["orca_run_status"] = status

    def _cleanup_attachment_staging(self) -> None:
        root = self._attach_stage_root
        self._attach_stage_root = None
        if not root:
            return
        try:
            shutil.rmtree(root, ignore_errors=True)
        except OSError:
            pass

    def _succeed_gate(
        self,
        gate: GateKind,
        *,
        detail: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        outcome = GateOutcome(
            gate=gate,
            status=GateStatus.SUCCEEDED,
            detail=detail,
            result=result or {},
        )
        self._record(outcome)

    def _fail_gate(
        self,
        gate: GateKind,
        *,
        detail: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        outcome = GateOutcome(
            gate=gate,
            status=GateStatus.FAILED,
            detail=detail,
            result=result or {},
        )
        self._record(outcome, stop=True)
        self._finalize_orca_run_status(ok=False)

    def _record(self, outcome: GateOutcome, *, stop: bool = False) -> None:
        self.state.gate_outcomes[outcome.gate.value] = outcome
        self.state.current_gate = outcome.gate
        if outcome.status is GateStatus.SUCCEEDED:
            if outcome.gate.value not in self.state.completed:
                self.state.completed.append(outcome.gate.value)
        elif outcome.status is GateStatus.SKIPPED:
            if outcome.gate.value not in self.state.skipped:
                self.state.skipped.append(outcome.gate.value)
        elif outcome.status is GateStatus.FAILED:
            if outcome.gate.value not in self.state.failed:
                self.state.failed.append(outcome.gate.value)
        if stop:
            self.state.stopped = True

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
            remaining_work=[],
            known_failures=[result.detail],
            next_action="Continue inside the same Orca Run",
            compacted_research={},
            source_lead=source if source != "orca" else self.bindings.preferred_lead,
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


def default_phases(*, research_useful: bool = True) -> list[GateKind]:
    """Deprecated: returns gate kinds for observability only (not a scheduler)."""
    del research_useful
    return [
        GateKind.PROJECT_CONTEXT,
        GateKind.POLICY,
        GateKind.ATTACHMENTS,
        GateKind.ORCA_HANDOFF,
        GateKind.MAINTENANCE,
        GateKind.VERIFICATION,
    ]


def phases_for_scale(scale: SpecKitScale) -> list[GateKind]:
    """Deprecated: scale no longer rebuilds an Aichestra agent phase graph."""
    del scale
    return default_phases()


def run_phase_hooks(
    workflow: ModeCRunController,
    hooks: dict[GateKind, Callable[[WorkflowState], None]] | None = None,
) -> WorkflowState:
    """Compatibility helper — prefer ``workflow.run_all()``."""
    hooks = hooks or {}
    state = workflow.run_all()
    if hooks:
        for _name, outcome in list(state.gate_outcomes.items()):
            if outcome.status in {GateStatus.SUCCEEDED, GateStatus.SKIPPED}:
                hook = hooks.get(outcome.gate)
                if hook:
                    hook(state)
    return state
