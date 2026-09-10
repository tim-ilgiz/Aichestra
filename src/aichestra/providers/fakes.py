"""In-tree fake providers for Mode C when real quota is blocked (FR-039).

These adapters never spawn Codex/Cursor/Orca binaries. Used by the CLI when
``AICHESTRA_FAKE_PROVIDERS`` / ``AICHESTRA_NO_REAL_QUOTA`` is set so orchestration
can still advance without consuming account quota.
"""

from __future__ import annotations

from pathlib import Path

from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderSession,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
)

_ENSURE_ROLES = frozenset({"ensure_run"})


def _write_speckit_artifacts(request: ProviderTaskRequest) -> list[str]:
    ctx = request.context if isinstance(request.context, dict) else {}
    root = request.cwd or ctx.get("project_root")
    if not isinstance(root, str) or not root.strip():
        return []
    required = ctx.get("required_artifacts") or ["brief.md", "plan.md"]
    if not isinstance(required, (list, tuple)):
        required = ["brief.md", "plan.md"]
    prompt = str(ctx.get("task_prompt") or request.prompt or "Mode C task")
    scale = str(ctx.get("speckit_scale") or "medium")
    steps = ctx.get("speckit_steps") or []
    spec_dir = Path(root) / ".aichestra" / "speckit"
    spec_dir.mkdir(parents=True, exist_ok=True)
    bodies = {
        "brief.md": f"# Brief\n\n{prompt}\n\n## Scale\n\n{scale}\n",
        "plan.md": (
            f"# Plan\n\nObjective: {prompt}\n\n"
            f"Steps: {', '.join(str(s) for s in steps)}\n"
        ),
        "clarify.md": "# Clarify\n\nNo open clarifications recorded for this run.\n",
        "tasks.md": (
            "# Tasks\n\n"
            "- [ ] Implement objective\n"
            "- [ ] Maintenance review\n"
            "- [ ] Verification\n"
        ),
    }
    written: list[str] = []
    for name in required:
        body = bodies.get(str(name))
        if body is None:
            continue
        path = spec_dir / str(name)
        path.write_text(body, encoding="utf-8")
        written.append(str(path))
    return written


class FakeModeCProvider(ProviderAdapter):
    """Deterministic Mode C stand-in — no subprocess, no quota."""

    def __init__(
        self,
        kind: ProviderKind,
        role: ProviderRole,
        *,
        available: bool = True,
        output: str = "fake mode-c execution ok",
    ) -> None:
        self.kind = kind
        self._role = role
        self._available = available
        self._output = output
        self.sent: list[ProviderTaskRequest] = []
        self.run_creates: int = 0
        self._minted_run_id: str | None = None

    def probe(self) -> ProviderStatus:
        return ProviderStatus(
            kind=self.kind,
            available=self._available,
            role=self._role,
            binary_path=f"/fake/{self.kind.value}" if self._available else None,
            version="fake-0.0.0" if self._available else None,
            failure=FailureClass.NONE if self._available else FailureClass.UNAVAILABLE,
            detail="fake provider (AICHESTRA_FAKE_PROVIDERS)",
            intercepts_native_cli=False,
            metadata={"fake": True},
        )

    def supports_execution(self) -> bool:
        return True

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        self.sent.append(request)
        if not self._available:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail="fake provider unavailable",
                session_id=session.session_id,
                metadata={"fake": True},
            )

        role = (request.role or "").strip().lower()
        ctx_run = (
            request.context.get("run_id")
            if isinstance(request.context, dict)
            else None
        )
        has_run = isinstance(ctx_run, str) and bool(ctx_run.strip())
        meta: dict = {"fake": True}
        if self.kind is ProviderKind.ORCA and role == "mode_c_handoff" and request.gate_handler:
            meta["gate_reply"] = request.gate_handler("maintenance")
        if self.kind is ProviderKind.ORCA and role == "run_status":
            meta.update({"settled": True, "receipt": {"run": {"id": ctx_run}}})

        if self.kind is ProviderKind.ORCA and role == "ensure_run":
            self.run_creates += 1
            run_id = f"fake-run-{session.session_id[:8]}-{self.run_creates}"
            self._minted_run_id = run_id
            meta.update(
                {
                    "run_id": run_id,
                    "orca_command": "orchestration run-create",
                    "reused": False,
                }
            )
        elif self.kind is ProviderKind.ORCA and role not in _ENSURE_ROLES and not has_run:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.ERROR,
                detail=(
                    f"fake Orca refuses role={role!r} without context.run_id "
                    "(no synthetic production ids)"
                ),
                session_id=session.session_id,
                metadata={"fake": True},
            )
        else:
            run_id = (
                ctx_run.strip()
                if has_run
                else (self._minted_run_id or f"fake-run-{session.session_id[:8]}")
            )
            meta.update(
                {
                    "run_id": run_id,
                    "orca_command": "orchestration worker-start",
                    "task_id": f"fake-task-{len(self.sent)}",
                    "dispatch_id": f"fake-dispatch-{len(self.sent)}",
                    "worktree_path": request.cwd,
                    "worktree_id": f"fake-repo::{request.cwd}" if request.cwd else None,
                    "integration_policy": "adopt_child_worktree",
                    "agent_complete": True,
                }
            )
            if role == "speckit_artifacts":
                meta["speckit_written"] = _write_speckit_artifacts(request)
            if role == "mode_c_handoff":
                ctx = request.context if isinstance(request.context, dict) else {}
                project_ctx = ctx.get("project_context") or {}
                agents = project_ctx.get("agents_files") or []
                shape = "default_implement"
                if agents:
                    shape = "project_instruction_driven"
                if ctx.get("speckit_scale") in {"medium", "large_high_risk"}:
                    shape = f"speckit_{ctx.get('speckit_scale')}"
                meta["simulated_workflow_shape"] = shape
                meta["canonical_lifecycle_owner"] = "orca"
                meta["workflow_dag_owner"] = "coordinator_under_orca"
                meta["inner_worker_selection_owner"] = "coordinator_under_orca"
            if role == "mode_c_agents":
                meta["simulated_roles"] = ["research", "lead_implement"]
            if role == "mode_c_writers":
                meta["simulated_roles"] = ["test_writer", "doc_writer"]

        return ProviderTaskResult(
            ok=True,
            output=self._output,
            failure=FailureClass.NONE,
            detail="fake execution ok",
            session_id=session.session_id,
            metadata=meta,
        )


def fake_orca() -> FakeModeCProvider:
    return FakeModeCProvider(ProviderKind.ORCA, ProviderRole.CONTROL_PLANE)


def fake_codex_lead() -> FakeModeCProvider:
    return FakeModeCProvider(ProviderKind.CODEX, ProviderRole.LEAD)


def fake_cursor_lead() -> FakeModeCProvider:
    return FakeModeCProvider(ProviderKind.CURSOR, ProviderRole.FALLBACK_LEAD)


def fake_local_worker(*, enabled: bool) -> FakeModeCProvider:
    return FakeModeCProvider(
        ProviderKind.LOCAL_WORKER,
        ProviderRole.WORKER,
        available=enabled,
        output="fake local-worker research summary",
    )


def fake_execution_targets(statuses, policy):
    """Quota-free CLI test bindings. These are never live launch evidence."""
    from aichestra.execution.domain import AgentRuntime, Compatibility, DiscoveryFacts, LaunchCapability, LaunchStrategy
    from aichestra.execution.targets import resolve_targets
    runtimes = tuple(AgentRuntime(s.kind.value, available=s.available)
                     for s in statuses if s.kind.value in {"codex", "cursor"})
    return tuple(resolve_targets(DiscoveryFacts(runtimes=runtimes),
        tuple(Compatibility(r.id) for r in runtimes), policy,
        tuple(LaunchCapability(r.id, strategy=LaunchStrategy.ORCA_NATIVE) for r in runtimes)))
