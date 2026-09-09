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
import os
from dataclasses import replace
import time
from pathlib import Path
from typing import Any

from aichestra.platform_detect import OperatingSystem, detect_os
from aichestra.providers.attachments import orca_attach_flags, stage_attachments
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
    """Build a representative Orca argv for Mode C (tests / inspection).

    ``ensure_run`` / first control-plane bind → ``orchestration run-create``.
    Supervised agent work → ``orchestration worker-start`` (task must already exist).
    Full ownership handoff (outside Mode C supervision) uses ``worktree create``.
    """
    prompt = request.bounded_prompt()
    role = (request.role or "").strip().lower()
    # Phase reports / status pings never mint Runs — status only.
    if role in {"phase_report", "status_ping"}:
        return [binary, "status", "--json"]
    if role in {"ensure_run", "control_plane", "classify"}:
        existing = request.context.get("run_id")
        if isinstance(existing, str) and existing.strip():
            return [
                binary,
                "orchestration",
                "run-use",
                "--id",
                existing.strip(),
                "--json",
            ]
        return [
            binary,
            "orchestration",
            "run-create",
            "--objective",
            prompt,
            "--json",
        ]
    name = f"aichestra-{session_id[:8]}"
    worktree = str(request.context.get("worktree") or "new-child")
    from aichestra.providers.attachments import orca_attach_flags

    return [
        binary,
        "orchestration",
        "worker-start",
        "--task",
        str(request.context.get("task_id") or "<task-id>"),
        "--worktree",
        worktree,
        "--name",
        name,
        "--agent",
        agent,
        "--setup",
        "skip",
        *orca_attach_flags(request.attachments),
        "--json",
    ]


def extract_worktree_locator(payload: dict[str, Any] | None) -> dict[str, str | None]:
    """Best-effort worktree path/id from an Orca worker-start / wait receipt."""
    data = payload if isinstance(payload, dict) else {}
    path: str | None = None
    worktree_id: str | None = None

    def _from_mapping(obj: Any) -> None:
        nonlocal path, worktree_id
        if not isinstance(obj, dict):
            return
        for key in ("path", "worktreePath", "worktree_path", "cwd", "folder"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip() and path is None:
                path = value.strip()
        for key in ("id", "worktreeId", "worktree_id", "fullId", "selector"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip() and worktree_id is None:
                # Prefer full ``repo::path`` selectors when present.
                if "::" in value or worktree_id is None:
                    worktree_id = value.strip()

    for key in ("worktree", "result", "effects", "worker", "launch", "data"):
        nested = data.get(key)
        _from_mapping(nested)
        if isinstance(nested, dict):
            _from_mapping(nested.get("worktree"))
            _from_mapping(nested.get("effects"))
            effects = nested.get("effects")
            if isinstance(effects, dict):
                _from_mapping(effects.get("worktree"))
            created = nested.get("created")
            if isinstance(created, dict):
                _from_mapping(created.get("worktree"))
                _from_mapping(created)

    if path is None:
        for key in ("worktreePath", "worktree_path"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                path = value.strip()
                break
    if worktree_id is None:
        for key in ("worktreeId", "worktree_id"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                worktree_id = value.strip()
                break

    return {"worktree_path": path, "worktree_id": worktree_id}


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


def _receipt_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    """Missing/malformed lists are not evidence of an empty Run."""
    data = payload.get("result", payload.get("value", payload))
    if isinstance(data, dict):
        data = data.get(key)
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        return None
    return data


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
    terminal_handle: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Interpret ``orca orchestration check --wait`` receipt.

    Only ``worker_done`` for the launched dispatch counts as success.
    ``question`` / ``escalation`` and mismatched dispatch ids leave work incomplete.
    """
    data = payload if isinstance(payload, dict) else {}
    ack_delivery_id = (
        _dig_id(data, "deliveryId")
        or _dig_id(data, "delivery_id")
        or _dig_id(data, "result", "deliveryId")
        or _dig_id(data, "result", "delivery_id")
    )
    events = _extract_wait_events(data)
    unrelated: list[dict[str, Any]] = []
    for event in events:
        event_type = str(
            event.get("type")
            or event.get("eventType")
            or event.get("kind")
            or ""
        ).strip().lower()
        event_dispatch = (
            _dig_id(event, "dispatchId")
            or _dig_id(event, "dispatch_id")
            or _dig_id(event, "from_dispatch_id")
        )
        # Current ask messages can identify their sender by the runtime-issued
        # terminal instead of carrying lifecycle payload. Bind only the exact
        # coordinator terminal returned by worker-start.
        if (not event_dispatch and event_type == "question" and terminal_handle
                and event.get("from_handle") == terminal_handle):
            event_dispatch = dispatch_id
        if dispatch_id and event_dispatch and event_dispatch != dispatch_id:
            unrelated.append(
                {
                    "event_type": event_type or None,
                    "event_dispatch_id": event_dispatch,
                }
            )
            continue
        meta = {
            "event_type": event_type or None,
            "event_dispatch_id": event_dispatch,
            "expected_dispatch_id": dispatch_id,
            "event": event,
            "ack_delivery_id": ack_delivery_id,
            "retryable_unrelated": False,
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
            return False, "wait receipt missing worker_done event type", meta
        if dispatch_id and not event_dispatch:
            return (
                False,
                (
                    f"wait event missing dispatch id (expected {dispatch_id}); "
                    "refusing to treat as worker_done success"
                ),
                meta,
            )
        status = str(event.get("outcome") or event.get("status") or event.get("result") or "").lower()
        if status != "succeeded":
            return False, f"worker_done with failure status: {status}", meta
        return True, "worker_done", meta

    return (
        False,
        "wait delivery contained only unrelated dispatch events",
        {
            "event_type": None,
            "event_dispatch_id": None,
            "expected_dispatch_id": dispatch_id,
            "event": events[0] if events else data,
            "unrelated_events": unrelated,
            "ack_delivery_id": ack_delivery_id,
            "retryable_unrelated": bool(dispatch_id and unrelated),
        },
    )


def _extract_wait_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw_events = payload.get("messages", payload.get("events"))
    if isinstance(raw_events, list):
        for item in raw_events:
            if isinstance(item, dict):
                events.append(item)
    for key in ("event", "result", "check", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            inner = nested.get("event")
            if isinstance(inner, dict):
                events.append(inner)
            if any(k in nested for k in ("type", "eventType", "kind", "dispatchId")):
                events.append(nested)
            rows = nested.get("messages", nested.get("events"))
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        events.append(row)
    if events:
        normalized = []
        for event in events:
            detail = event.get("payload")
            if isinstance(detail, str):
                detail = _parse_orca_json(detail)
            normalized.append({**(detail if isinstance(detail, dict) else {}), **event})
        return normalized
    return [payload]


def _extract_wait_event(payload: dict[str, Any]) -> dict[str, Any]:
    events = _extract_wait_events(payload)
    return events[0] if events else payload


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
        runtime = _parse_orca_json(ping.output).get("result", {}).get("runtime", {})
        if not ping.ok or runtime.get("reachable") is False:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=ping.detail or "Orca runtime not reachable (orca status)",
                session_id=session.session_id,
                metadata={"orca_status": ping.to_dict()},
            )

        role = (request.role or "").strip().lower()
        # Every route below the read-only operations can mutate orchestration.
        if role not in {"run_status", "phase_report", "status_ping"} and not os.environ.get("ORCA_TERMINAL_HANDLE", "").strip():
            return ProviderTaskResult(ok=False, failure=FailureClass.UNAVAILABLE,
                detail="Mode C requires a live Orca terminal identity (ORCA_TERMINAL_HANDLE)",
                session_id=session.session_id)
        agent = str(request.context.get("agent") or _DEFAULT_AGENT)
        if role == "mode_c_handoff":
            policy = request.context.get("provider_policy") or {}
            available = policy.get("providers") or {}
            agent = next((name for name in (
                policy.get("preferred_lead"), policy.get("fallback_lead")
            ) if name in {"codex", "cursor"} and available.get(name, {}).get("available")), "")
            if not agent and policy.get("local_enabled") and policy.get("local_available"):
                agent = "opencode"
            if not agent:
                return ProviderTaskResult(ok=False, failure=FailureClass.UNAVAILABLE,
                    detail="No enabled, available coordinator provider", session_id=session.session_id)
            # Preserve the complete policy; generic bounded_prompt truncates context.
            contract = (
                "Target:\nYou are the explicit Mode C coordinator for project_root in ProjectContext. "
                "Read applicable project instructions; nested AGENTS.md applies to its subtree.\n"
                "Change:\nComplete the original objective below.\n"
                "Constraints:\nLoad the Orca orchestration skill using " + binary + ". "
                "Bind the existing Run with run-use; NEVER create another Run. "
                "Honor project instruction precedence and security constraints. "
                "Use only enabled, available cloud providers. OpenCode launch is unavailable "
                "until Orca supports a proven pinned model/endpoint launch contract.\n"
                "Ownership:\nYou own the dynamic DAG: create arbitrary Tasks/Dispatches through Orca. "
                "Orca owns child workers and worktrees. Aichestra owns deterministic gates. "
                "At the implementation boundary, converge edits into the coordinator checkout, "
                "settle implementation workers, then call orchestration ask --question "
                "AICHESTRA_GATE:maintenance --json. Wait for the authoritative reply before "
                "dispatching test/doc/spec/ADR writers. Required writer work MUST be dispatched "
                "through Orca in this Run. If implementation changes again, repeat the gate. "
                "Even a no-implementation task must request the gate before completion.\n"
                "Observable acceptance:\nAll required gate handshakes completed; required writer "
                "Dispatches completed; all child Dispatches settled; changes converged into "
                "coordinator checkout. After each child worker_done reuse or worker-release it; "
                "worker-list --run <run_id> --terminal-state reclaimable must be empty. "
                "Only then send worker_done with explicit outcome succeeded; unresolved work "
                "requires outcome failed. Aichestra verifies commands and canonical Run state.\n"
            )
            full_prompt = contract + "POLICY_PACKAGE: " + json.dumps(request.context) + "\n" + request.prompt
            request = replace(request, prompt=full_prompt, context={**request.context, "worktree": "current"},
                              max_prompt_chars=len(full_prompt) + 5000)

        if role == "run_status":
            result = run_cli_task(binary=binary,
                argv=[binary, "orchestration", "run-show", "--id", str(request.context["run_id"]), "--json"],
                session=session, request=request, unavailable_detail="Orca unavailable")
            receipt = _parse_orca_json(result.output)
            run = receipt.get("result", receipt)
            if isinstance(run, dict):
                run = run.get("run", run)
            valid = (result.ok and isinstance(run, dict)
                     and (run.get("id") or run.get("runId")) == request.context["run_id"]
                     and not run.get("legacy")
                     and (run.get("state") or run.get("status")) in {None, "active", "completed", "succeeded"})
            tasks = run_cli_task(binary=binary,
                argv=[binary, "orchestration", "task-list", "--run", str(request.context["run_id"]), "--json"],
                session=session, request=request, unavailable_detail="Orca unavailable")
            rows = _receipt_rows(_parse_orca_json(tasks.output), "tasks")
            settled = bool(valid and tasks.ok and rows and all(
                row.get("status") == "completed" for row in rows))
            return replace(result, ok=settled,
                failure=FailureClass.NONE if settled else FailureClass.ERROR,
                detail="Canonical Run settled" if settled else "Canonical Run failed, unsettled, or unverifiable",
                metadata={**result.metadata, "receipt": receipt, "tasks": _parse_orca_json(tasks.output),
                          "settled": settled})

        if agent == "opencode" and role not in {"ensure_run", "control_plane", "classify", "phase_report", "status_ping"}:
            return ProviderTaskResult(ok=False, failure=FailureClass.UNAVAILABLE,
                detail="Orca OpenCode launch cannot yet pin the selected Ollama model/endpoint; local-only Mode C unavailable",
                session_id=session.session_id)

        # Phase reports must never create Runs — local metadata only at adapter.
        if role in {"phase_report", "status_ping"}:
            return ProviderTaskResult(
                ok=True,
                failure=FailureClass.NONE,
                detail="phase report acknowledged (no new Orca Run)",
                session_id=session.session_id,
                metadata={
                    "control_plane": True,
                    "integration": "execution-v1",
                    "orca_command": "noop-phase-report",
                    "run_id": request.context.get("run_id"),
                },
            )

        # Bind or create exactly one Run when requested; reuse context.run_id.
        if role in {"ensure_run", "control_plane", "classify"}:
            existing = request.context.get("run_id")
            if isinstance(existing, str) and existing.strip():
                return ProviderTaskResult(
                    ok=True,
                    failure=FailureClass.NONE,
                    detail="reusing existing Orca Run",
                    session_id=session.session_id,
                    metadata={
                        "control_plane": True,
                        "integration": "execution-v1",
                        "orca_command": "run-reuse",
                        "run_id": existing.strip(),
                        "reused": True,
                    },
                )
            return self._register_run(binary, session, request)

        if request.read_only and role not in {
            "lead_implement",
            "lead_review",
            "test_writer",
            "doc_writer",
            "research",
        }:
            existing = request.context.get("run_id")
            if isinstance(existing, str) and existing.strip():
                return ProviderTaskResult(
                    ok=True,
                    failure=FailureClass.NONE,
                    detail="read-only under existing Orca Run",
                    session_id=session.session_id,
                    metadata={
                        "control_plane": True,
                        "integration": "execution-v1",
                        "orca_command": "run-reuse",
                        "run_id": existing.strip(),
                        "reused": True,
                    },
                )
            return self._register_run(binary, session, request)

        return self._dispatch_supervised(binary, session, request, agent=agent)

    def _register_run(
        self,
        binary: str,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        """Create one durable orchestration Run (no write agent)."""
        prompt = request.bounded_prompt()
        result = run_cli_task(
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
        payload = _parse_orca_json(result.output)
        run_id = (
            _dig_id(payload, "result", "run", "id")
            or _dig_id(payload, "result", "id")
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
                "reused": False,
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
        """Task → worker-start → wait; reuse workflow Run (never run-create here)."""
        prompt = request.bounded_prompt()
        steps: list[dict[str, Any]] = []
        run_id = request.context.get("run_id")
        if not (isinstance(run_id, str) and run_id.strip()):
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.ERROR,
                detail=(
                    "Mode C supervised dispatch requires context.run_id; "
                    "call ensure_run once per workflow"
                ),
                session_id=session.session_id,
                metadata={"steps": steps, "integration": "execution-v1"},
            )
        run_id = run_id.strip()
        steps.append({"step": "run-reuse", "ok": True, "run_id": run_id})

        # Bind the existing Run so task-create inherits Run namespace (no second run-create).
        use_result = run_cli_task(
            binary=binary,
            argv=[
                binary,
                "orchestration",
                "run-use",
                "--id",
                run_id,
                "--json",
            ],
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        steps.append(
            {
                "step": "run-use",
                "ok": use_result.ok,
                "run_id": run_id,
                "detail": use_result.detail,
            }
        )
        # Fail closed: never task-create without a proven Run binding.
        if not use_result.ok:
            return self._failed_dispatch(
                ProviderTaskResult(
                    ok=False,
                    failure=use_result.failure
                    if use_result.failure is not FailureClass.NONE
                    else FailureClass.ERROR,
                    detail=(
                        use_result.detail
                        or f"orca run-use failed for run_id={run_id}; "
                        "refusing unbound task-create"
                    ),
                    session_id=session.session_id,
                    output=use_result.output,
                ),
                steps,
                session.session_id,
            )

        title = (request.role or "aichestra-task")[:80]
        # Explicitly associate the task with the Mode C Run (run-use + --run).
        # Never retry task-create without --run after a failed --run attempt.
        task_argv = [
            binary,
            "orchestration",
            "task-create",
            "--spec",
            prompt,
            "--task-title",
            title,
            "--run",
            run_id,
            "--json",
        ]
        task_result = run_cli_task(
            binary=binary,
            argv=task_argv,
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        task_payload = _parse_orca_json(task_result.output)
        task_id = (
            _dig_id(task_payload, "result", "task", "id")
            or _dig_id(task_payload, "result", "id")
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
        worktree = str(
            request.context.get("worktree")
            or request.context.get("worktree_id")
            or "new-child"
        )
        # Do NOT stage into the parent project checkout. Prefer absolute paths
        # for Orca --attach; optionally stage under a temp dir outside the repo.
        delivery = stage_attachments(request.attachments, None)
        attach_flags = orca_attach_flags(
            delivery.staged or delivery.resolved or request.attachments
        )
        worker_argv = [
            binary,
            "orchestration",
            "worker-start",
            "--task",
            task_id,
            "--worktree",
            worktree,
            "--name",
            name,
            "--agent",
            agent,
            "--setup",
            "skip",
            *attach_flags,
            "--json",
        ]
        if worktree not in {"new-child", "new-top-level"}:
            for flag in ("--name", "--setup"):
                index = worker_argv.index(flag)
                del worker_argv[index:index + 2]
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
        terminal_handle = (
            _dig_id(worker_payload, "result", "agentTerminalHandle")
            or _dig_id(worker_payload, "result", "terminalHandle")
            or _dig_id(worker_payload, "result", "effects", "terminal", "handle")
            or _dig_id(worker_payload, "result", "worker", "terminalHandle")
        )
        locator = extract_worktree_locator(worker_payload)
        steps.append(
            {
                "step": "worker-start",
                "ok": worker_result.ok,
                "dispatch_id": dispatch_id,
                **locator,
            }
        )
        if not worker_result.ok or not dispatch_id:
            return self._failed_dispatch(replace(worker_result, ok=False,
                failure=FailureClass.ERROR, detail=worker_result.detail or "Missing coordinator dispatch id"),
                steps, session.session_id)

        timeout_ms = max(1_000, int(float(request.timeout_seconds) * 1000))
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        wait_result: ProviderTaskResult | None = None
        wait_payload: dict[str, Any] = {}
        done_ok = False
        done_detail = "wait did not produce worker_done"
        done_meta: dict[str, Any] = {}
        ack_delivery_id: str | None = None
        attempts = 0
        gate_answered = False
        gate_replies: dict[str, dict[str, Any]] = {}
        while time.monotonic() < deadline:
            attempts += 1
            remaining_ms = max(1_000, int((deadline - time.monotonic()) * 1000))
            wait_argv = [
                binary,
                "orchestration",
                "check",
                "--run",
                run_id,
                "--wait",
                "--types",
                "worker_done,escalation,question",
                "--timeout-ms",
                str(remaining_ms),
                "--json",
            ]
            if ack_delivery_id:
                wait_argv[3:3] = ["--ack", ack_delivery_id]
                ack_delivery_id = None
            wait_result = run_cli_task(
                binary=binary,
                argv=wait_argv,
                session=session,
                request=request,
                unavailable_detail="Orca binary unavailable",
            )
            wait_payload = _parse_orca_json(wait_result.output)
            done_ok, done_detail, done_meta = interpret_orca_wait_event(
                wait_payload, dispatch_id=dispatch_id, terminal_handle=terminal_handle
            )
            if not wait_result.ok:
                break
            event = done_meta.get("event") or {}
            if (done_meta.get("event_type") == "question"
                    and done_meta.get("event_dispatch_id") == dispatch_id
                    and (event.get("question") or event.get("body") or event.get("subject")) == "AICHESTRA_GATE:maintenance"
                    and request.gate_handler is not None):
                message_id = event.get("messageId") or event.get("id")
                if not message_id:
                    done_detail = "Maintenance question missing message id"
                    break
                try:
                    if message_id not in gate_replies:
                        gate_replies[message_id] = request.gate_handler("maintenance")
                    answer = gate_replies[message_id]
                except Exception as exc:
                    done_detail = f"Maintenance gate failed: {exc}"
                    break
                reply = run_cli_task(binary=binary,
                    argv=[binary, "orchestration", "reply", "--id", str(message_id),
                          "--body", json.dumps(answer), "--json"],
                    session=session, request=request, unavailable_detail="Orca unavailable")
                if not reply.ok or not answer.get("ok"):
                    done_detail = "Maintenance gate reply failed or gate rejected"
                    break
                gate_answered = True
                ack_delivery_id = done_meta.get("ack_delivery_id")
                continue
            if done_ok:
                break
            retryable = bool(done_meta.get("retryable_unrelated"))
            ack = done_meta.get("ack_delivery_id")
            if retryable and isinstance(ack, str) and ack.strip():
                ack_delivery_id = ack.strip()
                continue
            break
        if wait_result is None:
            wait_result = ProviderTaskResult(
                ok=False,
                failure=FailureClass.TIMEOUT,
                detail="wait timed out before any Orca receipt",
                session_id=session.session_id,
            )
        if (
            not done_ok
            and done_meta.get("retryable_unrelated")
            and time.monotonic() >= deadline
        ):
            done_detail = "timed out waiting for expected dispatch completion"
        # Prefer worktree from wait receipt when worker-start omitted it.
        wait_locator = extract_worktree_locator(wait_payload)
        if wait_locator.get("worktree_path") and not locator.get("worktree_path"):
            locator = wait_locator
        elif wait_locator.get("worktree_id") and not locator.get("worktree_id"):
            locator = {
                "worktree_path": locator.get("worktree_path")
                or wait_locator.get("worktree_path"),
                "worktree_id": wait_locator.get("worktree_id"),
            }
        steps.append(
            {
                "step": "check-wait",
                "ok": wait_result.ok and done_ok,
                "detail": done_detail,
                "attempts": attempts,
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
            "worktree": worktree,
            "worktree_path": locator.get("worktree_path"),
            "worktree_id": locator.get("worktree_id"),
            "integration_policy": "adopt_child_worktree",
            "steps": steps,
            "receipt": wait_payload or worker_payload,
            "wait_interpretation": done_meta,
            "bytes_delivered": bool(delivery.bytes_delivered and attach_flags),
            "attachment_delivery": delivery.to_dict(),
            "orca_attach_flags": len(attach_flags) // 2,
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
        if done_meta.get("event_type") == "worker_done" and done_meta.get("event_dispatch_id") == dispatch_id:
            release = run_cli_task(binary=binary,
                argv=[binary, "orchestration", "worker-release", "--dispatch", dispatch_id, "--json"],
                session=session, request=request, unavailable_detail="Orca unavailable")
            workers = run_cli_task(binary=binary,
                argv=[binary, "orchestration", "worker-list", "--run", run_id,
                      "--terminal-state", "reclaimable", "--json"],
                session=session, request=request, unavailable_detail="Orca unavailable")
            rows = _receipt_rows(_parse_orca_json(workers.output), "workers")
            meta["cleanup"] = {"release": release.to_dict(), "workers": workers.to_dict()}
            release_receipt = _parse_orca_json(release.output)
            release_state = release_receipt.get("result", release_receipt)
            released = isinstance(release_state, dict) and release_state.get("state") in {"released", "already_released"}
            if not release.ok or not released or not workers.ok or rows != []:
                done_ok, done_detail = False, "Coordinator resource cleanup failed or unverifiable"
        if request.role == "mode_c_handoff" and not gate_answered:
            done_ok, done_detail = False, "Coordinator omitted maintenance gate handshake"
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
