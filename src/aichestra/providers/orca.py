"""Orca control-plane adapter — discovery + Mode C session/task execution.

Uses supported Orca CLI surfaces (status / orchestration / worktree), never the
nonexistent ``orca run`` entrypoint. Inspect installed ``orca skills get
orchestration --full`` on the developer machine before extending primitives.
Aichestra must not duplicate a general-purpose orchestrator (no CAO layer).
Native Codex/Cursor remain unintercepted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aichestra.platform_detect import OperatingSystem, detect_os
from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderSession,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
    probe_version,
    which_binary,
)
from aichestra.providers.execution import run_cli_task

# Common CLI names; discovery only for PATH — never wraps/intercepts user invocations.
_ORCA_BINARIES = ("orca", "orca-cli")
_DEFAULT_AGENT = "codex"


def resolve_orca_binary() -> str | None:
    """Resolve Orca CLI from PATH or well-known install locations (no user hard-code)."""
    found = which_binary(_ORCA_BINARIES)
    if found:
        return found
    for candidate in _platform_cli_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def _platform_cli_candidates() -> list[Path]:
    os_name = detect_os()
    home = Path.home()
    if os_name == OperatingSystem.MACOS:
        return [
            Path("/Applications/Orca.app/Contents/Resources/bin/orca"),
            home / "Applications/Orca.app/Contents/Resources/bin/orca",
        ]
    if os_name == OperatingSystem.WINDOWS:
        local = Path.home() / "AppData" / "Local"
        return [
            local / "Programs" / "Orca" / "orca.exe",
            local / "Orca" / "orca.exe",
        ]
    # Linux: common user/system prefixes; PATH remains primary.
    return [
        home / ".local" / "bin" / "orca",
        Path("/usr/local/bin/orca"),
        Path("/usr/bin/orca"),
    ]


def build_orca_argv(
    binary: str,
    request: ProviderTaskRequest,
    *,
    session_id: str,
    agent: str = _DEFAULT_AGENT,
) -> list[str]:
    """Build the primary Orca argv for a Mode C dispatch (for tests / inspection).

    Control-plane / read-only work registers an orchestration run.
    Write work starts a supervised worker on an isolated child worktree.
    """
    prompt = request.bounded_prompt()
    if request.read_only or request.role in {"control_plane", "classify"}:
        return [
            binary,
            "orchestration",
            "run-create",
            "--objective",
            prompt,
            "--json",
        ]
    name = f"aichestra-{session_id[:8]}"
    return [
        binary,
        "worktree",
        "create",
        "--name",
        name,
        "--agent",
        agent,
        "--prompt",
        prompt,
        "--setup",
        "skip",
        "--json",
    ]


def _parse_orca_json(output: str) -> dict[str, Any]:
    """Parse Orca ``--json`` stdout; tolerate trailing/leading noise."""
    text = (output or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"value": data}
    except json.JSONDecodeError:
        pass
    # Prefer last JSON object line (heartbeats may precede final receipt).
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return {}
    return {}


def _dig_id(payload: dict[str, Any], *keys: str) -> str | None:
    """Best-effort id extraction across Orca receipt shapes."""
    cursor: Any = payload
    for key in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    if isinstance(cursor, str) and cursor.strip():
        return cursor.strip()
    if isinstance(cursor, dict):
        for field in ("id", "runId", "taskId", "dispatchId"):
            value = cursor.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def interpret_orca_wait_event(
    payload: dict[str, Any] | None,
    *,
    dispatch_id: str | None,
) -> tuple[bool, str, dict[str, Any]]:
    """Interpret ``orca orchestration check --wait`` receipt.

    Only ``worker_done`` for the launched dispatch counts as success.
    ``question`` / ``escalation`` and mismatched dispatch ids leave work incomplete.
    """
    data = payload if isinstance(payload, dict) else {}
    event = _extract_wait_event(data)
    event_type = str(
        event.get("type")
        or event.get("eventType")
        or event.get("kind")
        or data.get("type")
        or data.get("eventType")
        or ""
    ).strip().lower()
    event_dispatch = (
        _dig_id(event, "dispatchId")
        or _dig_id(event, "dispatch_id")
        or _dig_id(event, "id")
        or _dig_id(data, "dispatchId")
        or _dig_id(data, "result", "dispatchId")
    )
    meta = {
        "event_type": event_type or None,
        "event_dispatch_id": event_dispatch,
        "expected_dispatch_id": dispatch_id,
        "event": event or data,
    }
    if event_type in {"question", "escalation"}:
        return (
            False,
            f"worker incomplete: received {event_type} (not worker_done)",
            meta,
        )
    if event_type and event_type != "worker_done":
        return False, f"unexpected wait event type: {event_type}", meta
    if not event_type:
        # Some Orca builds nest the type; treat missing type as incomplete.
        return False, "wait receipt missing worker_done event type", meta
    if dispatch_id and event_dispatch and event_dispatch != dispatch_id:
        return (
            False,
            (
                f"dispatch_id mismatch: expected {dispatch_id}, "
                f"got {event_dispatch}"
            ),
            meta,
        )
    # Optional explicit failure flag on the done event.
    status = str(event.get("status") or event.get("result") or "").lower()
    if status in {"failed", "error", "cancelled"}:
        return False, f"worker_done with failure status: {status}", meta
    return True, "worker_done", meta


def _extract_wait_event(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("event", "result", "check", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            # Prefer nested event when present.
            inner = nested.get("event")
            if isinstance(inner, dict):
                return inner
            if any(k in nested for k in ("type", "eventType", "kind", "dispatchId")):
                return nested
    events = payload.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            return first
    return payload


class OrcaProvider(ProviderAdapter):
    kind = ProviderKind.ORCA

    def probe(self) -> ProviderStatus:
        binary = resolve_orca_binary()
        if not binary:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.CONTROL_PLANE,
                failure=FailureClass.UNAVAILABLE,
                detail="Orca binary not found on PATH or known install locations",
                intercepts_native_cli=False,
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.CONTROL_PLANE,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="Orca available as opt-in control plane",
            intercepts_native_cli=False,
            metadata={"integration": "execution-v1"},
        )

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        """Dispatch Mode C work through supported Orca orchestration/worktree CLIs."""
        status = self.probe()
        if not status.available or not status.binary_path:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=status.detail or "Orca unavailable",
                session_id=session.session_id,
            )

        binary = status.binary_path
        # Runtime reachability — fail closed as unavailable when Orca is not up.
        ping = run_cli_task(
            binary=binary,
            argv=[binary, "status", "--json"],
            session=session,
            request=ProviderTaskRequest(
                prompt="status",
                role=request.role,
                timeout_seconds=min(30.0, request.timeout_seconds),
                cwd=request.cwd,
                read_only=True,
            ),
            unavailable_detail="Orca binary unavailable",
        )
        if not ping.ok:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=ping.detail or "Orca runtime not reachable (orca status)",
                session_id=session.session_id,
                metadata={"orca_status": ping.to_dict()},
            )

        agent = str(request.context.get("agent") or _DEFAULT_AGENT)
        if request.read_only or request.role in {"control_plane", "classify"}:
            return self._register_run(binary, session, request)
        return self._dispatch_supervised(binary, session, request, agent=agent)

    def _register_run(
        self,
        binary: str,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        """Control-plane path: create a durable orchestration Run (no write agent)."""
        argv = build_orca_argv(
            binary, request, session_id=session.session_id
        )
        result = run_cli_task(
            binary=binary,
            argv=argv,
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        payload = _parse_orca_json(result.output)
        run_id = (
            _dig_id(payload, "result", "id")
            or _dig_id(payload, "result", "runId")
            or _dig_id(payload, "id")
            or _dig_id(payload, "runId")
        )
        meta = dict(result.metadata)
        meta.update(
            {
                "control_plane": True,
                "integration": "execution-v1",
                "orca_command": "orchestration run-create",
                "run_id": run_id,
                "receipt": payload,
            }
        )
        return ProviderTaskResult(
            ok=result.ok,
            output=result.output,
            failure=result.failure,
            detail=result.detail,
            session_id=result.session_id,
            metadata=meta,
        )

    def _dispatch_supervised(
        self,
        binary: str,
        session: ProviderSession,
        request: ProviderTaskRequest,
        *,
        agent: str,
    ) -> ProviderTaskResult:
        """Write path: run → task → worker-start → wait for worker_done."""
        prompt = request.bounded_prompt()
        steps: list[dict[str, Any]] = []

        run_result = run_cli_task(
            binary=binary,
            argv=[
                binary,
                "orchestration",
                "run-create",
                "--objective",
                prompt,
                "--json",
            ],
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        run_payload = _parse_orca_json(run_result.output)
        run_id = (
            _dig_id(run_payload, "result", "id")
            or _dig_id(run_payload, "result", "runId")
            or _dig_id(run_payload, "id")
        )
        steps.append({"step": "run-create", "ok": run_result.ok, "run_id": run_id})
        if not run_result.ok:
            return self._failed_dispatch(run_result, steps, session.session_id)

        title = (request.role or "aichestra-task")[:80]
        task_result = run_cli_task(
            binary=binary,
            argv=[
                binary,
                "orchestration",
                "task-create",
                "--spec",
                prompt,
                "--task-title",
                title,
                "--json",
            ],
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        task_payload = _parse_orca_json(task_result.output)
        task_id = (
            _dig_id(task_payload, "result", "id")
            or _dig_id(task_payload, "result", "taskId")
            or _dig_id(task_payload, "id")
            or _dig_id(task_payload, "taskId")
        )
        steps.append({"step": "task-create", "ok": task_result.ok, "task_id": task_id})
        if not task_result.ok or not task_id:
            return self._failed_dispatch(
                task_result
                if not task_result.ok
                else ProviderTaskResult(
                    ok=False,
                    failure=FailureClass.ERROR,
                    detail="Orca task-create returned no task id",
                    session_id=session.session_id,
                    output=task_result.output,
                ),
                steps,
                session.session_id,
            )

        name = f"aichestra-{session.session_id[:8]}"
        worker_argv = [
            binary,
            "orchestration",
            "worker-start",
            "--task",
            task_id,
            "--worktree",
            "new-child",
            "--name",
            name,
            "--agent",
            agent,
            "--setup",
            "skip",
            "--json",
        ]
        worker_result = run_cli_task(
            binary=binary,
            argv=worker_argv,
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        worker_payload = _parse_orca_json(worker_result.output)
        dispatch_id = (
            _dig_id(worker_payload, "result", "dispatchId")
            or _dig_id(worker_payload, "result", "id")
            or _dig_id(worker_payload, "dispatchId")
        )
        steps.append(
            {
                "step": "worker-start",
                "ok": worker_result.ok,
                "dispatch_id": dispatch_id,
            }
        )
        if not worker_result.ok:
            return self._failed_dispatch(worker_result, steps, session.session_id)

        timeout_ms = max(1_000, int(float(request.timeout_seconds) * 1000))
        wait_result = run_cli_task(
            binary=binary,
            argv=[
                binary,
                "orchestration",
                "check",
                "--wait",
                "--types",
                "worker_done,escalation,question",
                "--timeout-ms",
                str(timeout_ms),
                "--json",
            ],
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        wait_payload = _parse_orca_json(wait_result.output)
        done_ok, done_detail, done_meta = interpret_orca_wait_event(
            wait_payload, dispatch_id=dispatch_id
        )
        steps.append(
            {
                "step": "check-wait",
                "ok": wait_result.ok and done_ok,
                "detail": done_detail,
                **done_meta,
            }
        )
        meta = {
            "control_plane": True,
            "integration": "execution-v1",
            "orca_command": "orchestration worker-start",
            "run_id": run_id,
            "task_id": task_id,
            "dispatch_id": dispatch_id,
            "agent": agent,
            "steps": steps,
            "receipt": wait_payload or worker_payload,
            "wait_interpretation": done_meta,
        }
        if not wait_result.ok:
            return ProviderTaskResult(
                ok=False,
                output=wait_result.output,
                failure=wait_result.failure,
                detail=wait_result.detail,
                session_id=session.session_id,
                metadata=meta,
            )
        if not done_ok:
            return ProviderTaskResult(
                ok=False,
                output=wait_result.output or worker_result.output,
                failure=FailureClass.ERROR,
                detail=done_detail,
                session_id=session.session_id,
                metadata=meta,
            )
        return ProviderTaskResult(
            ok=True,
            output=wait_result.output or worker_result.output,
            failure=FailureClass.NONE,
            detail="ok",
            session_id=session.session_id,
            metadata=meta,
        )

    def _failed_dispatch(
        self,
        result: ProviderTaskResult,
        steps: list[dict[str, Any]],
        session_id: str,
    ) -> ProviderTaskResult:
        meta = dict(result.metadata)
        meta.update(
            {
                "control_plane": True,
                "integration": "execution-v1",
                "steps": steps,
            }
        )
        return ProviderTaskResult(
            ok=False,
            output=result.output,
            failure=result.failure,
            detail=result.detail,
            session_id=session_id,
            metadata=meta,
        )
