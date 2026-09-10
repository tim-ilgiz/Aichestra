"""Fake provider test doubles — never consume real Codex/Cursor quota."""

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

SCENARIOS = ("success", "unavailable", "quota", "auth", "timeout", "error")

# Roles that mint/resume a Run; all other Mode C agent roles require context.run_id.
_ENSURE_ROLES = frozenset({"ensure_run"})


def _fake_write_speckit_artifacts(request: ProviderTaskRequest) -> list[str]:
    """Simulate Orca producing Spec Kit files under the target project."""
    ctx = request.context if isinstance(request.context, dict) else {}
    root = request.cwd or ctx.get("project_root")
    if not isinstance(root, str) or not root.strip():
        return []
    required = ctx.get("required_artifacts") or [
        "brief.md",
        "plan.md",
        "clarify.md",
        "tasks.md",
    ]
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
        name_s = str(name)
        body = bodies.get(name_s)
        if body is None:
            continue
        path = spec_dir / name_s
        path.write_text(body, encoding="utf-8")
        written.append(str(path))
    return written


def _status(
    kind: ProviderKind,
    *,
    available: bool,
    role: ProviderRole,
    failure: FailureClass = FailureClass.NONE,
    detail: str = "",
) -> ProviderStatus:
    return ProviderStatus(
        kind=kind,
        available=available,
        role=role,
        binary_path=f"/fake/{kind.value}" if available else None,
        version="fake-0.0.0" if available else None,
        failure=failure,
        detail=detail or failure.value,
        intercepts_native_cli=False,
        metadata={"fake": True, "integration": "execution-v1"},
    )


class FakeProvider(ProviderAdapter):
    def __init__(
        self,
        status: ProviderStatus,
        *,
        execute_scenario: str | None = None,
        execute_output: str = "fake execution ok",
    ) -> None:
        self.kind = status.kind
        self._status = status
        self._execute_scenario = execute_scenario or (
            "success" if status.available else "unavailable"
        )
        self._execute_output = execute_output
        self.sessions: list[ProviderSession] = []
        self.sent: list[ProviderTaskRequest] = []
        self.run_creates: int = 0
        self._minted_run_id: str | None = None

    def probe(self) -> ProviderStatus:
        return self._status

    def supports_execution(self) -> bool:
        return True

    def start_session(
        self,
        *,
        role: str = "",
        context=None,
    ) -> ProviderSession:
        session = super().start_session(role=role, context=context)
        self.sessions.append(session)
        return session

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        self.sent.append(request)
        scenario = self._execute_scenario.lower().strip()
        role = (request.role or "").strip().lower()
        ctx_run = request.context.get("run_id") if isinstance(request.context, dict) else None
        has_run = isinstance(ctx_run, str) and bool(ctx_run.strip())

        # Mode C Orca agent roles must bind to a real run_id (never synthesize).
        if (
            self.kind is ProviderKind.ORCA
            and role not in _ENSURE_ROLES
            and role
            not in {
                "control_plane",
                "classify",
                "status_ping",
                "phase_report",
            }
            and not has_run
        ):
            return ProviderTaskResult(
                ok=False,
                output="",
                failure=FailureClass.ERROR,
                detail=(
                    f"fake Orca refuses role={role!r} without context.run_id "
                    "(no synthetic production ids)"
                ),
                session_id=session.session_id,
                metadata={"fake": True},
            )
        if scenario in {"success", "ok", "available"}:
            meta: dict = {"fake": True}
            if role == "mode_c_handoff" and request.gate_handler:
                meta["gate_reply"] = request.gate_handler("maintenance")
            if role == "run_status":
                meta.update({"settled": True, "receipt": {"run": {"id": ctx_run}}})
            if role == "ensure_run":
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
            elif role in {"control_plane", "classify", "phase_report", "status_ping", "run_status"}:
                run_id = (
                    ctx_run.strip()
                    if has_run
                    else (self._minted_run_id or f"fake-run-{session.session_id[:8]}")
                )
                meta.update(
                    {
                        "run_id": run_id,
                        "orca_command": "noop-phase-report",
                        "reused": True,
                    }
                )
            else:
                # Agent roles (mode_c_agents, research, lead_*, writers, …)
                run_id = ctx_run.strip()
                cwd = request.cwd
                worktree_path = cwd or f"/tmp/fake-orca-worktree-{session.session_id[:8]}"
                meta.update(
                    {
                        "run_id": run_id,
                        "orca_command": "orchestration worker-start",
                        "task_id": f"fake-task-{len(self.sent)}",
                        "dispatch_id": f"fake-dispatch-{len(self.sent)}",
                        "worktree_path": worktree_path,
                        "worktree_id": f"fake-repo::{worktree_path}",
                        "integration_policy": "adopt_child_worktree",
                        "agent_complete": True,
                    }
                )
                if request.attachments:
                    meta["attachments"] = list(request.attachments)
                    meta["bytes_delivered"] = True
                    meta["attachment_count"] = len(request.attachments)
                if role == "mode_c_agents":
                    meta["simulated_roles"] = ["research", "lead_implement"]
                if role == "mode_c_writers":
                    meta["simulated_roles"] = ["test_writer", "doc_writer"]
                if role == "speckit_artifacts":
                    written = _fake_write_speckit_artifacts(request)
                    meta["speckit_written"] = written
                    meta["simulated_roles"] = ["speckit_artifacts"]
            return ProviderTaskResult(
                ok=True,
                output=self._execute_output,
                failure=FailureClass.NONE,
                detail="fake execution ok",
                session_id=session.session_id,
                metadata=meta,
            )
        failure_map = {
            "unavailable": FailureClass.UNAVAILABLE,
            "absent": FailureClass.UNAVAILABLE,
            "missing": FailureClass.UNAVAILABLE,
            "quota": FailureClass.QUOTA,
            "auth": FailureClass.AUTH,
            "timeout": FailureClass.TIMEOUT,
            "error": FailureClass.ERROR,
            "generic": FailureClass.ERROR,
        }
        failure = failure_map.get(scenario, FailureClass.ERROR)
        run_id = ctx_run.strip() if has_run else None
        return ProviderTaskResult(
            ok=False,
            output="",
            failure=failure,
            detail=f"fake execution {failure.value}",
            session_id=session.session_id,
            metadata={"fake": True, "run_id": run_id},
        )


def fake_codex(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.CODEX, ProviderRole.LEAD, scenario),
        execute_scenario=scenario,
    )


def fake_cursor(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.CURSOR, ProviderRole.FALLBACK_LEAD, scenario),
        execute_scenario=scenario,
    )


def fake_local_worker(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.LOCAL_WORKER, ProviderRole.WORKER, scenario),
        execute_scenario=scenario,
        execute_output="fake local-worker research summary",
    )


def fake_orca(scenario: str = "success") -> FakeProvider:
    return FakeProvider(
        _scenario(ProviderKind.ORCA, ProviderRole.CONTROL_PLANE, scenario),
        execute_scenario=scenario,
    )


def _scenario(
    kind: ProviderKind, role: ProviderRole, scenario: str
) -> ProviderStatus:
    scenario = scenario.lower().strip()
    if scenario in {"success", "ok", "available"}:
        return _status(kind, available=True, role=role, detail="fake success")
    if scenario in {"unavailable", "absent", "missing"}:
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.UNAVAILABLE,
            detail="fake unavailable",
        )
    if scenario == "quota":
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.QUOTA,
            detail="fake quota exhausted",
        )
    if scenario == "auth":
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.AUTH,
            detail="fake auth failure",
        )
    if scenario == "timeout":
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.TIMEOUT,
            detail="fake timeout",
        )
    if scenario in {"error", "generic"}:
        return _status(
            kind,
            available=False,
            role=role,
            failure=FailureClass.ERROR,
            detail="fake generic error",
        )
    raise ValueError(f"unknown fake scenario: {scenario}")


def fake_provider_set(
    *,
    codex: str = "unavailable",
    cursor: str = "unavailable",
    local: str = "unavailable",
    orca: str = "unavailable",
) -> list[ProviderStatus]:
    """Return ProviderStatus list for lead-selection / degradation tests."""
    return [
        fake_orca(orca).probe(),
        fake_codex(codex).probe(),
        fake_cursor(cursor).probe(),
        fake_local_worker(local).probe(),
    ]


def fake_execution_targets(runtime="codex"):
    """Explicit runnable binding for fake Orca, never production launch evidence."""
    from aichestra.execution.domain import AgentRuntime, Compatibility, DiscoveryFacts, LaunchCapability, LaunchStrategy
    from aichestra.execution.targets import resolve_targets
    return tuple(resolve_targets(DiscoveryFacts(runtimes=(AgentRuntime(runtime, available=True),)),
        (Compatibility(runtime),), known_launches=(LaunchCapability(runtime, strategy=LaunchStrategy.ORCA_NATIVE, proven=True),)))
