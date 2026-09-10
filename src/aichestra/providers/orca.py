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
import shlex
import os
import subprocess
from dataclasses import replace
import time
from pathlib import Path
from typing import Any, Mapping

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


def _release_recovery_args(action, dispatch_id: str, binary: str) -> list[str] | None:
    if isinstance(action, dict):
        action = action.get("args", action.get("argv", action.get("command")))
    if isinstance(action, str):
        if action.startswith("Inspect with: "):
            action = action[len("Inspect with: "):]
        try:
            action = shlex.split(action)
        except ValueError:
            return None
    if not isinstance(action, list) or not all(isinstance(x, str) for x in action):
        return None
    args = list(action)
    if args and args[0] in {binary, "orca", "orca-cli", "orca-ide"}:
        args.pop(0)
    if len(args) < 5 or args[:2] not in (["orchestration", "worker-release"], ["orchestration", "worker-show"]):
        return None
    # Exact grammar prevents shell injection, scope broadening and duplicate flags.
    tail = args[2:]
    if tail == ["--dispatch", dispatch_id, "--json"]:
        return args
    if (len(tail) == 5 and tail[:2] == ["--dispatch", dispatch_id]
            and tail[2] == "--retry-request" and tail[3] and not tail[3].startswith("-")
            and tail[4] == "--json"):
        return args
    return None


def resolve_orca_binary() -> str | None:
    """Resolve a usable Orca CLI without assuming one operating-system path."""
    found = which_binary(_ORCA_BINARIES)
    if found and _usable_cli_candidate(Path(found)):
        return found
    for candidate in _platform_cli_candidates():
        if _usable_cli_candidate(candidate):
            return str(candidate)
    return None


def _usable_cli_candidate(candidate: Path) -> bool:
    """Reject stale or unreadable PATH links before trying OS-specific fallbacks."""
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if not resolved.is_file():
        return False
    if detect_os() == OperatingSystem.WINDOWS:
        return True
    return os.access(resolved, os.X_OK)


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


def _publish_worktree_locator(
    request: ProviderTaskRequest, locator: Mapping[str, Any]
) -> None:
    """Expose the live worktree to in-flight Aichestra gates before worker_done."""
    ctx = request.context
    if not isinstance(ctx, dict):
        return
    path = locator.get("worktree_path")
    worktree_id = locator.get("worktree_id")
    if isinstance(path, str) and path.strip():
        ctx["worktree_path"] = path.strip()
        ctx.setdefault("integration_policy", "adopt_child_worktree")
    if isinstance(worktree_id, str) and worktree_id.strip():
        ctx["worktree_id"] = worktree_id.strip()


def _parse_orca_json(output: str) -> dict[str, Any]:
    """Parse Orca ``--json`` stdout; tolerate trailing/leading noise."""
    text = (output or "").strip()
    if not text:
        return {}

    # ``orchestration check --wait --json`` writes keepalive JSON lines to
    # stderr. ``run_cli_task`` preserves stderr after stdout for diagnostics,
    # so remove only those documented transport records before parsing the
    # authoritative stdout receipt. A keepalive is never lifecycle evidence.
    meaningful_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("{"):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                candidate = None
            if isinstance(candidate, dict) and candidate.get("_keepalive") is True:
                continue
        meaningful_lines.append(raw_line)
    text = "\n".join(meaningful_lines).strip()
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


def _extract_agent_terminal_handle(payload: dict[str, Any]) -> str | None:
    """Read the documented agent handle across create/show receipt envelopes."""
    paths = (
        ("result", "agentTerminalHandle"),
        ("result", "terminalHandle"),
        ("result", "effects", "terminal", "handle"),
        ("result", "worker", "terminalHandle"),
        ("result", "worker", "agentTerminalHandle"),
        ("result", "worker", "agent_terminal_handle"),
        ("result", "dispatch", "assignee_handle"),
        ("result", "terminal", "handle"),
        ("agentTerminalHandle",),
        ("terminalHandle",),
        ("worker", "terminalHandle"),
        ("worker", "agentTerminalHandle"),
        ("worker", "agent_terminal_handle"),
        ("dispatch", "assignee_handle"),
        ("terminal", "handle"),
    )
    for path in paths:
        handle = _dig_id(payload, *path)
        if handle:
            return handle
    return None


def _empty_wait_poll_meta(
    data: dict[str, Any],
    *,
    dispatch_id: str | None,
    ack_delivery_id: str | None,
) -> dict[str, Any]:
    """Metadata for a bounded poll that observed no lifecycle events yet."""
    envelope = data.get("result", data)
    timed_out = bool(isinstance(envelope, dict) and envelope.get("timedOut"))
    return {
        "event_type": None,
        "event_dispatch_id": None,
        "expected_dispatch_id": dispatch_id,
        "event": envelope if isinstance(envelope, dict) else data,
        "ack_delivery_id": ack_delivery_id,
        "retryable_unrelated": False,
        "retryable_poll": True,
        "empty_poll": True,
        "timed_out": timed_out,
    }


def interpret_orca_wait_event(
    payload: dict[str, Any] | None,
    *,
    dispatch_id: str | None,
    terminal_handle: str | None = None,
    ignore_message_ids: frozenset[str] | set[str] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Interpret ``orca orchestration check --wait`` receipt.

    Only ``worker_done`` for the launched dispatch counts as success.
    ``question`` / ``escalation`` and mismatched dispatch ids leave work incomplete.
    Empty / timed-out polls are retryable until the caller's overall deadline.
    Already-answered question ids (non-consuming peek) are skipped so a later
    ``worker_done`` in the same batch remains visible.
    """
    data = payload if isinstance(payload, dict) else {}
    ignored = {
        str(item).strip()
        for item in (ignore_message_ids or ())
        if isinstance(item, str) and item.strip()
    }
    ack_delivery_id = (
        _dig_id(data, "deliveryId")
        or _dig_id(data, "delivery_id")
        or _dig_id(data, "result", "deliveryId")
        or _dig_id(data, "result", "delivery_id")
    )
    events = _extract_wait_events(data)
    if not events:
        return (
            False,
            "wait poll empty",
            _empty_wait_poll_meta(
                data, dispatch_id=dispatch_id, ack_delivery_id=ack_delivery_id
            ),
        )
    unrelated: list[dict[str, Any]] = []
    skipped_answered = 0
    for event in events:
        message_id = event.get("messageId") or event.get("id")
        if (
            isinstance(message_id, str)
            and message_id.strip() in ignored
        ):
            skipped_answered += 1
            continue
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
        elif (
            not event_dispatch
            and event_type == "question"
            and terminal_handle
            and event.get("from_handle")
        ):
            unrelated.append(
                {
                    "event_type": event_type,
                    "event_dispatch_id": None,
                    "from_handle": event.get("from_handle"),
                }
            )
            continue
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
            "skipped_answered_questions": skipped_answered,
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

    if skipped_answered and not unrelated:
        # Peek still shows only already-answered questions. Keep polling until
        # worker_done appears or the overall deadline expires.
        meta = _empty_wait_poll_meta(
            data, dispatch_id=dispatch_id, ack_delivery_id=ack_delivery_id
        )
        meta["skipped_answered_questions"] = skipped_answered
        return False, "wait poll has only already-answered questions", meta

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
            "skipped_answered_questions": skipped_answered,
        },
    )


def _is_check_envelope(payload: dict[str, Any]) -> bool:
    """True for ``orchestration check`` receipts, not lifecycle event bodies."""
    return any(key in payload for key in ("messages", "events", "timedOut", "count"))


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
            # Check envelopes often carry dispatchId/runId without being events.
            # Only treat a nested object as an event when it names an event type.
            if any(k in nested for k in ("type", "eventType", "kind")):
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
    # Legacy single-event receipts place type/kind at the top level. Empty
    # check polls must remain empty so the wait loop can reopen until deadline.
    if _is_check_envelope(payload):
        return []
    if any(k in payload for k in ("type", "eventType", "kind")):
        return [payload]
    return []


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
            target = request.execution_target
            if target is None or not target.runnable:
                return ProviderTaskResult(
                    ok=False,
                    failure=FailureClass.UNAVAILABLE,
                    detail="No proven coordinator ExecutionTarget",
                    session_id=session.session_id,
                )
            agent = target.runtime.id
            from aichestra.execution.serialize import (
                prove_launch_invocation,
                render_cli_invocation,
            )

            prove_invocation = prove_launch_invocation(
                project_root=str(request.context.get("project_root") or "<root>"),
                repo_root=(
                    str(request.context.get("aichestra_repo_root")).strip()
                    if isinstance(request.context.get("aichestra_repo_root"), str)
                    and str(request.context.get("aichestra_repo_root")).strip()
                    else None
                ),
            )
            prove_display = render_cli_invocation(prove_invocation)
            # Preserve the complete policy; generic bounded_prompt truncates context.
            contract = (
                "Target:\nYou are the explicit Mode C coordinator for project_root in ProjectContext. "
                "Read applicable project instructions; nested AGENTS.md applies to its subtree.\n"
                "Change:\nComplete the original objective below.\n"
                "Constraints:\nLoad the Orca orchestration skill using " + binary + ". "
                "Bind the supplied existing Run exactly once with run-use; NEVER call "
                "run-create. Aichestra observes this coordinator terminal's mailbox "
                "non-consumingly for deterministic ask/reply gates. "
                "Honor project instruction precedence and security constraints.\n"
                "ExecutionTargets:\n"
                "The coordinator owns inner worker selection. "
                "The coordinator package includes canonical execution_targets "
                "(runnable only), execution_target_candidates (non-dispatchable), "
                "and execution_policy (execution_target_contract_version). "
                "For inner workers, Dispatch only targets listed in execution_targets "
                "(enabled, available, capable, allowed, and runnable). "
                "orca-existing-terminal targets include launch_ref; Dispatch that "
                "handle — do not invent a terminal. "
                "Candidates require aichestra.prove_launch via "
                f"structured launch_proof_invocation `{prove_display}` "
                "(argv contract — never shell-concatenate paths or JSON "
                "candidate ids; deterministic structured process "
                "attestation; Aichestra re-resolves binding from trusted config "
                "using --repo-root) "
                "before they become runnable — never DIY terminal show/tail "
                "recipes, embedded shell launch strings, or screen substring matching. "
                "Invoke aichestra prove-launch --repo-root <aichestra_repo_root> "
                "--project-root <root> --candidate-id <id> --json. "
                "Use the returned prepared_launch.terminal_handle exactly; "
                "do not create a second bridge terminal. "
                "If prove-launch succeeds but Dispatch does not start, call "
                "aichestra abort-launch --launch-ref <terminal_handle>. "
                "Target locality may be local, remote, or cloud according to ExecutionPolicy. "
                "Do not infer workers from raw providers. "
                "Do not impose product-name phase routing. "
                "Unsupported targets MUST NOT be dispatched. "
                "Legacy preferred_lead/local_* fields are secondary compatibility seams only. "
                "Ownership:\nYou own the dynamic DAG: create arbitrary Tasks/Dispatches through Orca. "
                "Orca owns child workers and worktrees. Aichestra owns deterministic gates. "
                "At the implementation boundary, converge edits into the coordinator checkout, "
                "settle implementation workers, then call orchestration ask --question "
                "AICHESTRA_GATE:maintenance --json. Wait for the authoritative reply before "
                "dispatching test/doc/spec/ADR writers. Aichestra replies only to exact "
                "coordinator AICHESTRA_GATE questions. Child workers must not ask those "
                "questions; only this coordinator crosses that boundary after their "
                "implementation Dispatches are settled. State that prohibition explicitly "
                "in child Task specs. For any other launch, policy, or capability blocker, "
                "settle affected Tasks and report this coordinator Dispatch with worker_done "
                "outcome failed; do not leave an operator question pending. Required writer "
                "work MUST be dispatched through Orca in this Run. If implementation changes "
                "again, repeat the maintenance gate. "
                "Even a no-implementation task must request the maintenance gate before "
                "verification.\n"
                "Observable acceptance:\nAll required gate handshakes completed; required writer "
                "Dispatches completed; all child Dispatches settled; changes converged into "
                "coordinator checkout. After each child worker_done reuse or worker-release it; "
                "worker-list --run <run_id> --terminal-state reclaimable must be empty. "
                "Then call orchestration ask --question AICHESTRA_GATE:verification --json "
                "and wait for the authoritative result. Do not send worker_done outcome "
                "succeeded until that reply has ok=true. If verification ok=false, finish "
                "this coordinator Dispatch with worker_done outcome failed so the Orca Run "
                "and Aichestra agree. Unresolved work requires outcome failed.\n"
            )
            package = request.context.get("policy_package")
            if isinstance(package, Mapping):
                coordinator_package = dict(package)
            else:
                # Compatibility for direct adapter callers. Keep one bounded,
                # canonical copy instead of recursively echoing the entire
                # operational context into the agent prompt.
                coordinator_package = {
                    key: request.context.get(key)
                    for key in (
                        "run_id",
                        "task_prompt",
                        "project_root",
                        "project_context",
                        "execution_targets",
                        "execution_target_candidates",
                        "execution_policy",
                        "execution_target_contract_version",
                        "launch_proof_operation",
                        "launch_proof_invocation",
                        "aichestra_repo_root",
                        "attachments",
                        "classify",
                        "speckit_scale",
                        "speckit_steps",
                        "provider_policy",
                    )
                    if request.context.get(key) is not None
                }
            objective = str(
                coordinator_package.get("task_prompt") or request.prompt
            ).strip()
            full_prompt = (
                contract
                + "POLICY_PACKAGE: "
                + json.dumps(coordinator_package)
                + "\nObjective:\n"
                + objective
            )
            operational_context = {
                key: request.context.get(key)
                for key in (
                    "run_id",
                    "terminal_handle",
                    "prepared_bootstrap_launch",
                    "prepared_launch",
                )
                if request.context.get(key) is not None
            }
            operational_context["worktree"] = "current"
            request = replace(
                request,
                prompt=full_prompt,
                context=operational_context,
                max_prompt_chars=len(full_prompt) + 2_000,
            )

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

    def _release_worker(self, binary, session, request, dispatch_id, run_id):
        """Bounded exact-dispatch recovery; never execute arbitrary receipt text."""
        history = []
        def call(args):
            result = run_cli_task(binary=binary, argv=[binary, *args],
                session=session, request=request, unavailable_detail="Orca unavailable")
            history.append({"argv": args, "result": result.to_dict()})
            payload = _parse_orca_json(result.output)
            data = payload.get("result", payload)
            return result, data if isinstance(data, dict) else {}

        result, data = call(["orchestration", "worker-release", "--dispatch", dispatch_id, "--json"])
        state = data.get("state", "release_unknown") if data.get("dispatchId", dispatch_id) == dispatch_id else "release_unknown"
        for _ in range(3):
            if state not in {"release_pending", "release_unknown"}:
                break
            projection = data.get("projection")
            action = projection.get("nextAction") if isinstance(projection, dict) else None
            if action is None:
                action = data.get("recovery")
            args = _release_recovery_args(action, dispatch_id, binary)
            if args is not None:
                result, data = call(args)
            # Recovery results alone cannot establish that this worker exited.
            inspection, exact = call(["orchestration", "worker-show", "--dispatch", dispatch_id, "--json"])
            worker = exact.get("worker") or {}
            exact_id = exact.get("dispatchId") or worker.get("dispatch_id")
            if not inspection.ok or exact_id != dispatch_id:
                state = "release_unknown"
                break
            state = data.get("state", "release_unknown")
            if data.get("dispatchId", dispatch_id) != dispatch_id:
                state = "release_unknown"
                break
            if state not in {"released", "already_released"}:
                data = exact
                projection = exact.get("projection") or {}
                if not isinstance(projection, dict):
                    projection = {}
                resource = exact.get("terminalResource") or {}
                observed = projection.get("terminalState", exact.get("terminalState"))
                if observed is None and isinstance(resource, dict) and resource.get("releaseState") == "released":
                    observed = "released"
                state = observed or state
                if state == "released":
                    result = inspection
            if args is None:
                # Automatic Orca recovery may have completed during inspection.
                # Unknown instructions never authorize a mutation or blind retry.
                break

        # Coordinator contract: reclaimable resources for this Run must be empty.
        workers, listing = call([
            "orchestration", "worker-list", "--run", run_id,
            "--terminal-state", "reclaimable", "--json",
        ])
        rows = _receipt_rows(listing, "workers")
        unresolved = rows is None or len(rows) > 0
        released = state in {"released", "already_released"}
        ok = bool(result.ok and released and workers.ok and not unresolved)
        return ok, {"state": state, "history": history, "unresolved_resources": unresolved}

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
        launch_adapter = None
        launch_evidence: dict[str, Any] = {}
        prepared = None
        launch_ctx = None
        if request.execution_target is not None:
            from aichestra.execution.launch_strategies import (
                LaunchContext,
                abort_prepared,
                adapter_for,
                deserialize_prepared_launch,
            )

            # worker-start / Dispatch only for proven runnable targets.
            if not request.execution_target.dispatchable:
                return ProviderTaskResult(
                    ok=False,
                    failure=FailureClass.ERROR,
                    detail=(
                        "ExecutionTarget is not dispatchable; promote via "
                        "aichestra prove-launch first"
                    ),
                    session_id=session.session_id,
                    metadata={"steps": steps},
                )

            prepared_ctx = request.context.get("prepared_launch")
            prepared_handle = None
            if isinstance(prepared_ctx, Mapping):
                raw_handle = prepared_ctx.get("terminal_handle")
                if isinstance(raw_handle, str) and raw_handle.strip():
                    prepared_handle = raw_handle.strip()
            handle = (
                str(request.context.get("terminal_handle") or "").strip()
                or prepared_handle
                or str(getattr(request.execution_target, "launch_ref", None) or "").strip()
                or str(os.environ.get("ORCA_WORKER_TERMINAL_HANDLE") or "").strip()
                or None
            )
            launch_ctx = LaunchContext(
                binary=binary,
                worktree=worktree,
                terminal_handle=handle,
                run=subprocess.run,
            )
            preflight = (
                request.context.get("prepared_bootstrap_launch")
                or request.context.get("prepared_launch")
            )
            try:
                if isinstance(preflight, Mapping) and preflight.get("arguments") is not None:
                    # Already prepared/attested (bootstrap or prove-launch) —
                    # reuse exact launch; do not create a second bridge terminal.
                    prepared = deserialize_prepared_launch(preflight)
                    try:
                        launch_adapter = adapter_for(request.execution_target)
                    except ValueError:
                        launch_adapter = None
                else:
                    launch_adapter = adapter_for(request.execution_target)
                    prepared = launch_adapter.prepare(
                        request.execution_target,
                        launch_ctx,
                    )
            except ValueError as exc:
                return ProviderTaskResult(
                    ok=False,
                    failure=FailureClass.ERROR,
                    detail=f"ExecutionTarget launch prepare failed: {exc}",
                    session_id=session.session_id,
                    metadata={"steps": steps},
                )
            launch_evidence = dict(prepared.evidence)
            index = worker_argv.index("--agent")
            worker_argv[index : index + 2] = list(prepared.arguments)
        if worktree not in {"new-child", "new-top-level"}:
            for flag in ("--name", "--setup"):
                index = worker_argv.index(flag)
                del worker_argv[index : index + 2]
        worker_result = run_cli_task(
            binary=binary,
            argv=worker_argv,
            session=session,
            request=request,
            unavailable_detail="Orca binary unavailable",
        )
        worker_payload = _parse_orca_json(worker_result.output)
        if launch_evidence:
            worker_payload = {
                **worker_payload,
                "aichestra_terminal_evidence": launch_evidence,
            }
        dispatch_id = (
            _dig_id(worker_payload, "result", "dispatchId")
            or _dig_id(worker_payload, "result", "id")
            or _dig_id(worker_payload, "dispatchId")
        )
        terminal_handle = _extract_agent_terminal_handle(worker_payload) or (
            launch_evidence.get("handle")
            if isinstance(launch_evidence.get("handle"), str)
            else None
        )
        locator = extract_worktree_locator(worker_payload)
        _publish_worktree_locator(request, locator)
        steps.append(
            {
                "step": "worker-start",
                "ok": worker_result.ok,
                "dispatch_id": dispatch_id,
                **locator,
            }
        )
        if not worker_result.ok or not dispatch_id:
            data = worker_payload.get("result", worker_payload)
            detail = data.get("lastError") or worker_result.detail or "Missing coordinator dispatch id"
            failed = self._failed_dispatch(replace(worker_result, ok=False,
                failure=FailureClass.ERROR, detail=detail), steps, session.session_id)
            cleanup_meta: dict[str, Any] = {}
            # Exact dispatch cleanup when Orca already allocated a worker resource.
            if dispatch_id:
                _, cleanup = self._release_worker(binary, session, request, dispatch_id, run_id)
                cleanup_meta["cleanup"] = cleanup
            elif prepared is not None and launch_ctx is not None and prepared.owns_terminal:
                # Bridge created a terminal but worker-start never bound a dispatch.
                cleanup_meta["bridge_terminal_cleanup"] = abort_prepared(prepared, launch_ctx)
            if cleanup_meta:
                failed = replace(failed, metadata={**failed.metadata, **cleanup_meta})
            return failed

        if request.execution_target is not None and launch_adapter is not None and not launch_adapter.confirms(
            request.execution_target, worker_payload
        ):
            # Worker already started under a mismatched effective launch —
            # release this exact dispatch before returning FAIL.
            _, cleanup = self._release_worker(binary, session, request, dispatch_id, run_id)
            failed = self._failed_dispatch(replace(worker_result, ok=False,
                failure=FailureClass.ERROR,
                detail=f"Orca launch binding unverified for dispatch {dispatch_id}; inspect worker-show"),
                steps, session.session_id)
            return replace(failed, metadata={**failed.metadata, "cleanup": cleanup,
                "dispatch_id": dispatch_id})

        if not terminal_handle:
            show_result = run_cli_task(
                binary=binary,
                argv=[
                    binary,
                    "orchestration",
                    "worker-show",
                    "--dispatch",
                    dispatch_id,
                    "--json",
                ],
                session=session,
                request=request,
                unavailable_detail="Orca unavailable",
            )
            show_payload = _parse_orca_json(show_result.output)
            terminal_handle = _extract_agent_terminal_handle(show_payload)
            steps.append(
                {
                    "step": "worker-handle-resolve",
                    "ok": bool(show_result.ok and terminal_handle),
                    "dispatch_id": dispatch_id,
                    "terminal_handle": terminal_handle,
                }
            )
        if request.role == "mode_c_handoff" and not terminal_handle:
            failed = self._failed_dispatch(
                replace(
                    worker_result,
                    ok=False,
                    failure=FailureClass.ERROR,
                    detail=(
                        "Orca coordinator terminal handle unavailable after exact "
                        f"worker-show for dispatch {dispatch_id}; refusing Run-scoped wait"
                    ),
                ),
                steps,
                session.session_id,
            )
            return replace(
                failed,
                metadata={**failed.metadata, "dispatch_id": dispatch_id},
            )

        timeout_ms = max(1_000, int(float(request.timeout_seconds) * 1000))
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        from aichestra.execution.serialize import (
            COORDINATOR_GATES,
            coordinator_gate_from_event,
        )
        wait_result: ProviderTaskResult | None = None
        wait_payload: dict[str, Any] = {}
        done_ok = False
        done_detail = "wait did not produce worker_done"
        done_meta: dict[str, Any] = {}
        ack_delivery_id: str | None = None
        attempts = 0
        gates_answered: set[str] = set()
        verification_ok = True
        gate_replies: dict[str, dict[str, Any]] = {}
        monitor_coordinator = bool(
            request.role == "mode_c_handoff" and terminal_handle
        )
        while time.monotonic() < deadline:
            attempts += 1
            remaining_ms = max(1_000, int((deadline - time.monotonic()) * 1000))
            # Orca may not wake an already-open terminal-scoped peek when the
            # coordinator later asks a Run-addressed question. Keep the global
            # deadline, but periodically reopen the non-consuming observation.
            poll_timeout_ms = min(remaining_ms, 15_000)
            wait_argv = [binary, "orchestration", "check"]
            if monitor_coordinator:
                # The generic coordinator must bind the Run to own its DAG.
                # Observe its mailbox by exact terminal without replacing that
                # consumer or consuming child lifecycle mail before it does.
                wait_argv.extend(["--terminal", str(terminal_handle), "--peek"])
            else:
                wait_argv.extend(["--run", run_id])
            wait_argv.extend(
                [
                    "--wait",
                    "--types",
                    "worker_done,escalation,question",
                    "--timeout-ms",
                    str(poll_timeout_ms),
                    "--json",
                ]
            )
            if ack_delivery_id and not monitor_coordinator:
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
                wait_payload,
                dispatch_id=dispatch_id,
                terminal_handle=terminal_handle,
                ignore_message_ids=frozenset(gate_replies),
            )
            if not wait_result.ok:
                break
            event = done_meta.get("event") or {}
            gate_name = coordinator_gate_from_event(event)
            if (done_meta.get("event_type") == "question"
                    and done_meta.get("event_dispatch_id") == dispatch_id
                    and gate_name is not None
                    and request.gate_handler is not None):
                message_id = event.get("messageId") or event.get("id")
                if not message_id:
                    done_detail = f"{gate_name} question missing message id"
                    break
                try:
                    gate_replies[message_id] = request.gate_handler(gate_name)
                    answer = gate_replies[message_id]
                except Exception as exc:
                    done_detail = f"{gate_name} gate failed: {exc}"
                    break
                reply_argv = [
                    binary,
                    "orchestration",
                    "reply",
                    "--id",
                    str(message_id),
                ]
                if monitor_coordinator:
                    # run-use intentionally transfers Run ownership to the
                    # generic coordinator. Orca's supported --from route lets
                    # the deterministic gate answer that exact coordinator's
                    # question without taking the Run back and fencing it.
                    reply_argv.extend(["--from", str(terminal_handle)])
                reply_argv.extend(["--body", json.dumps(answer), "--json"])
                reply = run_cli_task(binary=binary,
                    argv=reply_argv,
                    session=session, request=request, unavailable_detail="Orca unavailable")
                reply_payload = _parse_orca_json(reply.output)
                already_answered = (
                    isinstance(reply_payload.get("error"), dict)
                    and str(reply_payload["error"].get("code") or "") == "answer_conflict"
                )
                if not reply.ok and not already_answered:
                    done_detail = f"{gate_name} gate reply failed"
                    break
                if gate_name == "maintenance" and not answer.get("ok"):
                    done_detail = "Maintenance gate reply failed or gate rejected"
                    break
                if gate_name == "verification":
                    verification_ok = bool(answer.get("ok"))
                gates_answered.add(gate_name)
                if not monitor_coordinator:
                    ack_delivery_id = done_meta.get("ack_delivery_id")
                continue
            if done_ok:
                break
            retryable = bool(
                done_meta.get("retryable_unrelated") or done_meta.get("retryable_poll")
            )
            if retryable:
                ack = done_meta.get("ack_delivery_id")
                if monitor_coordinator:
                    # The coordinator owns this Run's FIFO mailbox. Peeking
                    # must never acknowledge a child lifecycle event on its
                    # behalf; it will consume that event itself. Empty polls
                    # also reopen under the same non-consuming observation.
                    time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                elif isinstance(ack, str) and ack.strip():
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
            and (
                done_meta.get("retryable_unrelated")
                or done_meta.get("retryable_poll")
            )
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
            _, cleanup = self._release_worker(binary, session, request, dispatch_id, run_id)
            meta["cleanup"] = cleanup
            return ProviderTaskResult(
                ok=False,
                output=wait_result.output,
                failure=wait_result.failure,
                detail=wait_result.detail,
                session_id=session.session_id,
                metadata=meta,
            )
        if done_meta.get("event_type") == "worker_done" and done_meta.get("event_dispatch_id") == dispatch_id:
            cleaned, cleanup = self._release_worker(binary, session, request, dispatch_id, run_id)
            meta["cleanup"] = cleanup
            if not cleaned:
                done_ok, done_detail = False, "Coordinator resource cleanup failed or unverifiable"
        if request.role == "mode_c_handoff":
            missing = [name for name in COORDINATOR_GATES if name not in gates_answered]
            if missing:
                done_ok, done_detail = False, f"Coordinator omitted {missing[0]} gate handshake"
            elif done_ok and not verification_ok:
                done_ok, done_detail = (
                    False,
                    "Coordinator reported succeeded after failed verification",
                )
        if not done_ok:
            if "cleanup" not in meta:
                _, cleanup = self._release_worker(binary, session, request, dispatch_id, run_id)
                meta["cleanup"] = cleanup
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
