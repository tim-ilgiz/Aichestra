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
    _task_role,
    _worker_record,
    contract_endpoint_matches,
    receipt_timestamp,
    resolve_worker_failure,
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


def _dispatch_role_receipts_dir(home, run_id):
    """Immutable per-dispatch receipt directory for one Run."""
    contract_path = _path(home, run_id)
    return contract_path.with_name(f"{contract_path.stem}.dispatch-role")


def _dispatch_role_receipts_legacy_path(home, run_id):
    """Pre-directory shared JSON array (read-only migration)."""
    contract_path = _path(home, run_id)
    return contract_path.with_name(f"{contract_path.stem}.dispatch-role.json")


def _dispatch_role_receipt_path(home, run_id, dispatch_id: str) -> Path:
    digest = hashlib.sha256(dispatch_id.encode("utf-8")).hexdigest()
    return _dispatch_role_receipts_dir(home, run_id) / f"{digest}.json"


def save_dispatch_role_receipt(home, receipt: Mapping) -> dict:
    """Persist one immutable coordinator-adoption receipt for an inner Dispatch.

    Each ``dispatch_id`` owns its own file created with exclusive create (``"x"``).
    Parallel workers cannot clobber each other; duplicate saves are idempotent.
    """
    required = ("operation", "run_id", "task_id", "role", "dispatch_id", "execution_target_id")
    row = {key: receipt.get(key) for key in required}
    for key, value in row.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"dispatch-role receipt missing {key}")
        row[key] = value.strip()
    reason = str(receipt.get("reason") or "primary").strip() or "primary"
    if reason not in {"primary", "quota-fallback"}:
        raise ValueError("unknown dispatch-role receipt reason")
    row["reason"] = reason
    recorded = receipt.get("recorded_at")
    if isinstance(recorded, str) and recorded.strip():
        row["recorded_at"] = recorded.strip()
    else:
        row["recorded_at"] = datetime.now(timezone.utc).isoformat()
    effective = receipt.get("launch_effective")
    if isinstance(effective, Mapping):
        row["launch_effective"] = dict(effective)
    path = _dispatch_role_receipt_path(home, row["run_id"], row["dispatch_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize fully before exclusive create so a mid-write OSError cannot leave
    # an empty/partial *.json that poisons load_dispatch_role_receipts forever.
    payload = json.dumps(row, sort_keys=True).encode("utf-8")
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(payload)
        return row
    except FileExistsError:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Malformed dispatch-role receipt; refusing overwrite") from exc
        if not isinstance(existing, Mapping):
            raise ValueError("Malformed dispatch-role receipt; refusing overwrite")
        for key in required:
            if existing.get(key) != row[key]:
                raise ValueError(
                    f"dispatch-role receipt for {row['dispatch_id']} already exists "
                    "with different evidence"
                )
        if existing.get("reason") != row["reason"]:
            raise ValueError(
                f"dispatch-role receipt for {row['dispatch_id']} already exists "
                "with different evidence"
            )
        return dict(existing)
    except OSError:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def load_dispatch_role_receipts(home, run_id) -> list[dict]:
    """Load Aichestra dispatch-role adoption receipts for a Run.

    Prefer per-dispatch files under ``<run-hash>.dispatch-role/``. A legacy
    shared JSON array is still accepted read-only for in-flight Runs.
    Missing storage means no coordinator adoption yet (empty list), not a soft-pass.
    """
    rows: list[dict] = []
    directory = _dispatch_role_receipts_dir(home, run_id)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("dispatch-role receipts unreadable") from exc
            if not isinstance(loaded, Mapping):
                raise ValueError("Malformed dispatch-role receipt row")
            rows.append(dict(loaded))
    legacy = _dispatch_role_receipts_legacy_path(home, run_id)
    if legacy.exists():
        try:
            loaded = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("dispatch-role receipts unreadable") from exc
        if not isinstance(loaded, list):
            raise ValueError("Malformed dispatch-role receipts")
        seen = {
            item.get("dispatch_id")
            for item in rows
            if isinstance(item.get("dispatch_id"), str)
        }
        for item in loaded:
            if not isinstance(item, Mapping):
                raise ValueError("Malformed dispatch-role receipt row")
            dispatch_id = item.get("dispatch_id")
            if isinstance(dispatch_id, str) and dispatch_id in seen:
                continue
            rows.append(dict(item))
            if isinstance(dispatch_id, str):
                seen.add(dispatch_id)
    return rows


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


def _load_worker_done_messages(
    call,
    run_id: str,
    *,
    from_handle: str | None,
) -> list | None:
    """Read durable worker_done mail via check --all (never run-use)."""
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
    return messages if isinstance(messages, list) else None


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
        result = run(
            [binary, "orchestration", *args, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
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
    durable_messages = None
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
        failure = resolve_worker_failure(row)
        if failure is None:
            if durable_messages is None:
                durable_messages = _load_worker_done_messages(
                    call, run_id, from_handle=from_handle
                ) or []
            failure = resolve_worker_failure(row, durable_messages=durable_messages)
        if ((row.get("dispatch_id") or row.get("dispatchId") or data.get("dispatchId")) == dispatch
                and (row.get("run_id") or row.get("runId")) == run_id
                and (prior_task.get("run_id") or prior_task.get("runId")) == run_id
                and _task_role(prior_task) == "implement"
                and failure == "quota" and ordered and actual
                and all(actual.get(k) == primary.get(k) for k in ("runtime", "provider", "model"))
                and contract_endpoint_matches(primary, actual.get("endpoint"))):
            return
    raise ValueError("Quota fallback requires a completed primary implement quota receipt in this Run")
