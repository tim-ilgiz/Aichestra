"""Immutable Run policy storage and canonical pre-dispatch authorization.

This stores policy only. Orca remains responsible for tasks and workers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from aichestra.execution.launch_strategies import extract_attested_binding, receipt_launch
from aichestra.execution.roles import (
    _parse_object,
    _task_role,
    _worker_record,
    contract_endpoint_matches,
    last_failure_message_id,
    receipt_failure_code,
    receipt_timestamp,
)
from aichestra.providers.orca import _receipt_rows


def _path(home, run_id):
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return Path(home) / ".local" / "run-contracts" / f"{digest}.json"


def save_contract(home, run_id, project_root, contract):
    payload = {"run_id": run_id, "project_root": str(Path(project_root).resolve()),
               "contract": contract}
    path = _path(home, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
    except FileExistsError:
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError("Run contract already exists with different policy")


def load_contract(home, run_id, project_root):
    try:
        payload = json.loads(_path(home, run_id).read_text(encoding="utf-8"))
        if (payload["run_id"] != run_id
                or payload["project_root"] != str(Path(project_root).resolve())):
            raise ValueError("Run contract project/identity mismatch")
        contract = payload["contract"]
        if not isinstance(contract, dict) or not isinstance(contract.get("bindings"), dict):
            raise ValueError("Malformed Run contract")
        return contract
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Run contract missing or unreadable; start a new Mode C Run") from exc


def _coordinator_terminal(call, run_id: str, from_handle: str | None) -> str | None:
    sender = (from_handle or "").strip()
    if sender:
        return sender
    try:
        payload = call("run-show", "--id", run_id)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    data = payload.get("result", payload)
    run = data.get("run") if isinstance(data, Mapping) else None
    if not isinstance(run, Mapping):
        return None
    handle = run.get("coordinator_handle") or run.get("coordinatorHandle")
    return handle.strip() if isinstance(handle, str) and handle.strip() else None


def _worker_done_payload_failure(
    call,
    run_id: str,
    *,
    dispatch_id: str,
    message_id: str | None,
    from_handle: str | None,
) -> str | None:
    """Typed failure from durable worker_done mail payload (read-only).

    Orca 1.4.200 persists ``payload.failure`` on the worker_done message but
    omits it from ``dispatch.lastFailure``. Never call run-use; use the Run
    coordinator terminal with ``check --all``.
    """
    terminal = _coordinator_terminal(call, run_id, from_handle)
    if not terminal:
        return None
    try:
        payload = call(
            "check",
            "--terminal",
            terminal,
            "--run",
            run_id,
            "--all",
            "--types",
            "worker_done",
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if payload.get("ok") is False:
        return None
    data = payload.get("result", payload)
    messages = data.get("messages") if isinstance(data, Mapping) else None
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        msg_id = message.get("id") or message.get("messageId")
        body = _parse_object(message.get("payload"))
        if not isinstance(body, Mapping):
            continue
        body_dispatch = body.get("dispatchId") or body.get("dispatch_id")
        matched = (
            (isinstance(message_id, str) and msg_id == message_id)
            or body_dispatch == dispatch_id
        )
        if not matched:
            continue
        code = receipt_failure_code(body) or receipt_failure_code(
            {"payload": message.get("payload")}
        )
        if code:
            return code
    return None


def authorize_task(
    binary,
    run,
    run_id,
    task_id,
    role,
    contract,
    reason,
    *,
    from_handle: str | None = None,
):
    def call(*args):
        result = run([binary, "orchestration", *args, "--json"],
                     check=False, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError("Cannot read canonical Orca state")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("Malformed canonical Orca state")
        if payload.get("ok") is False and args and args[0] != "check":
            # check may return ok:false when fenced; handled by caller.
            err = payload.get("error")
            detail = err.get("message") if isinstance(err, Mapping) else None
            raise ValueError(detail or "Cannot read canonical Orca state")
        return payload

    tasks = _receipt_rows(call("task-list", "--run", run_id), "tasks")
    if tasks is None:
        raise ValueError("Cannot enumerate canonical Run tasks")
    task_map = {t.get("id") or t.get("taskId"): t for t in tasks}
    if None in task_map or len(task_map) != len(tasks):
        raise ValueError("Missing or duplicate canonical task ids")
    task = task_map.get(task_id, {})
    if (task.get("run_id") or task.get("runId")) != run_id or _task_role(task) != role:
        raise ValueError("Task role or Run association does not match contract")
    if reason != "quota-fallback":
        return

    workers = _receipt_rows(call("worker-list", "--run", run_id), "workers")
    if workers is None:
        raise ValueError("Cannot enumerate quota evidence")
    primary = contract["bindings"]["implement"]
    for worker in workers:
        dispatch = worker.get("dispatch_id") or worker.get("dispatchId")
        if not isinstance(dispatch, str) or not dispatch:
            raise ValueError("Missing canonical dispatch id")
        payload = call("worker-show", "--dispatch", dispatch)
        data, row = _worker_record(payload)
        prior_task = task_map.get(row.get("task_id") or row.get("taskId"), {})
        # Same nesting as validate_role_receipts / Orca 1.4+ startOptions.launch.
        launch = (
            receipt_launch(payload)
            or receipt_launch(data)
            or receipt_launch({"worker": row})
            or {}
        )
        actual = extract_attested_binding({"effective": launch.get("effective", {})})
        completed = receipt_timestamp(row, "completed_at", "completedAt")
        ordered = bool(completed and completed <= datetime.now(timezone.utc))
        failure = receipt_failure_code(row)
        if failure is None:
            failure = _worker_done_payload_failure(
                call,
                run_id,
                dispatch_id=dispatch,
                message_id=last_failure_message_id(row),
                from_handle=from_handle,
            )
        if ((row.get("dispatch_id") or row.get("dispatchId") or data.get("dispatchId")) == dispatch
                and (row.get("run_id") or row.get("runId")) == run_id
                and (prior_task.get("run_id") or prior_task.get("runId")) == run_id
                and _task_role(prior_task) == "implement"
                and failure == "quota" and ordered and actual
                and all(actual.get(k) == primary.get(k) for k in ("runtime", "provider", "model"))
                and contract_endpoint_matches(primary, actual.get("endpoint"))):
            return
    raise ValueError("Quota fallback requires a completed primary implement quota receipt in this Run")
