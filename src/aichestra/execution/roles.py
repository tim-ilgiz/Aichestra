"""Exact role resolution; no task graph or agent scheduling lives here."""
from __future__ import annotations

import hashlib
from typing import Mapping

from aichestra.config.roles import ROLE_KEYS, RoleBinding
from aichestra.execution.domain import ExecutionTarget
from aichestra.execution.serialize import ROLE_DISPATCH_OPERATION


class RoleBindingResolver:
    def __init__(self, targets):
        self.targets = tuple(targets)

    def resolve(self, role: str, binding: RoleBinding) -> ExecutionTarget:
        matches = [t for t in self.targets if
                   (t.runtime.id, t.provider.id if t.provider else None,
                    t.model.id if t.model else None) ==
                   (binding.runtime, binding.provider, binding.model)]
        field = role if "." in role else f"roles.{role}"
        if len(matches) != 1 or not matches[0].preparable:
            raise ValueError(
                f"{field}: exact target {binding.to_dict()} is unavailable, "
                "disabled, incompatible or has no supported Orca launch; configure "
                "execution.runtimes/model_providers and launch support before running"
            )
        return matches[0]

    def resolve_all(self, bindings):
        return {role: self.resolve(role, binding) for role, binding in bindings.items()}


def endpoint_fingerprint(endpoint: str | None) -> str:
    from .launch_strategies import _normalize_endpoint
    return hashlib.sha256((_normalize_endpoint(endpoint) or "").encode("utf-8")).hexdigest()


def contract_endpoint_matches(entry: dict, endpoint: str | None) -> bool:
    fingerprint = entry.get("endpoint_fingerprint")
    if fingerprint is not None:
        return fingerprint == endpoint_fingerprint(endpoint)
    # Legacy contracts only prove the endpoint they actually stored. Never
    # equate a sanitized URL with an unseen tenant query or credentials.
    return (entry.get("endpoint") or "").rstrip("/") == (endpoint or "").strip().rstrip("/")


def target_contract_entry(target: ExecutionTarget) -> dict:
    """Typed role→target entry for POLICY_PACKAGE.role_dispatch_contract.

    Runnable targets may be Dispatched immediately and MUST appear in
    ``execution_targets``. Provisionable targets are candidates only: the
    contract records ``requires_launch_proof`` so the coordinator (or
    ``aichestra dispatch-role``) must prove-launch before Dispatch. That keeps
    the package internally consistent without forcing every worker prove up
    front.
    """
    from aichestra.execution.serialize import safe_endpoint_for_context
    entry = {
        "execution_target_id": target.id,
        "runtime": target.runtime.id,
        "endpoint": safe_endpoint_for_context(target.endpoint),
        "endpoint_fingerprint": endpoint_fingerprint(target.endpoint),
        "provider": target.provider.id if target.provider else None,
        "model": target.model.id if target.model else None,
    }
    if target.runnable:
        entry["state"] = "runnable"
        entry["requires_launch_proof"] = False
    elif target.provisionable:
        entry["state"] = "provisionable"
        entry["candidate_id"] = target.id
        entry["requires_launch_proof"] = True
    else:
        entry["state"] = "unavailable"
        entry["requires_launch_proof"] = False
    return entry


def assert_role_dispatch_package_consistent(package: dict) -> None:
    """Fail closed when contract targets disagree with runnable/candidate sets."""
    runnable_ids = {
        row.get("id") for row in list(package.get("execution_targets") or [])
        if isinstance(row, dict)
    }
    candidate_ids = {
        row.get("id") for row in list(package.get("execution_target_candidates") or [])
        if isinstance(row, dict)
    }
    contract = package.get("role_dispatch_contract") or {}
    bindings = contract.get("bindings") or {}
    for role, entry in bindings.items():
        if not isinstance(entry, dict):
            raise ValueError(f"role_dispatch_contract.bindings.{role} must be an object")
        target_id = entry.get("execution_target_id")
        if not target_id:
            raise ValueError(f"role_dispatch_contract.bindings.{role} missing execution_target_id")
        needs_proof = bool(entry.get("requires_launch_proof"))
        state = entry.get("state")
        if needs_proof or state == "provisionable":
            if target_id not in candidate_ids:
                raise ValueError(
                    f"role_dispatch_contract.bindings.{role} is provisionable but "
                    f"{target_id!r} is missing from execution_target_candidates"
                )
            if target_id in runnable_ids:
                raise ValueError(
                    f"role_dispatch_contract.bindings.{role} requires launch proof "
                    f"but {target_id!r} also appears in execution_targets"
                )
        else:
            if target_id not in runnable_ids:
                raise ValueError(
                    f"role_dispatch_contract.bindings.{role} is runnable but "
                    f"{target_id!r} is missing from execution_targets"
                )


def _worker_record(payload):
    """Normalize worker-show envelopes, including Orca's separate dispatch row."""
    data = payload.get("result", payload)
    worker = data.get("worker", data)
    dispatch = data.get("dispatch")
    if isinstance(dispatch, dict):
        worker = {**dispatch, **worker}
        worker.setdefault("dispatch_id", dispatch.get("id"))
    return data, worker


def _parse_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            import json
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def receipt_timestamp(row, *keys):
    """Parse canonical receipt timestamps (snake_case or Orca camelCase)."""
    from datetime import datetime

    for key in keys:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip().replace("Z", "+00:00")
        if "T" not in text and " " in text:
            text = text.replace(" ", "T", 1)
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            continue
    return None


def receipt_failure_code(row):
    """Typed failure class from a worker receipt — never body/subject prose.

    Accepts top-level ``failure``, Orca ``lastFailure`` JSON when that object
    carries an explicit failure/code field, and durable ``worker_done`` message
    ``payload.failure`` (Orca 1.4.200 stores the typed class on the message even
    when ``lastFailure`` omits it). ``rate_limit`` / ``quota_exhausted``
    normalize to ``quota`` for the same Mode C policy gate.
    """
    candidates = []
    direct = row.get("failure")
    if isinstance(direct, str):
        candidates.append(direct)
    elif isinstance(direct, dict):
        for key in ("code", "failure"):
            value = direct.get(key)
            if isinstance(value, str):
                candidates.append(value)
    for blob in (
        row.get("lastFailure"),
        row.get("last_failure"),
        row.get("payload"),
    ):
        parsed = _parse_object(blob)
        if not parsed:
            continue
        for key in ("failure", "failureCode", "code"):
            value = parsed.get(key)
            if isinstance(value, str):
                candidates.append(value)
    for raw in candidates:
        code = raw.strip().lower()
        if code in {"quota", "rate_limit", "quota_exhausted"}:
            return "quota"
        if code:
            return code
    return None


def last_failure_message_id(row):
    """Return durable worker_done message id from lastFailure, if present."""
    parsed = _parse_object(row.get("lastFailure") or row.get("last_failure"))
    if not parsed:
        return None
    for key in ("messageId", "message_id"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def failure_code_from_durable_messages(
    dispatch_id,
    messages,
    *,
    message_id: str | None = None,
) -> str | None:
    """Typed failure from durable worker_done mail payloads (Orca 1.4.200).

    ``lastFailure`` may omit ``failure`` while the matching ``worker_done``
    message still carries ``payload.failure``. Match by message id and/or
    ``payload.dispatchId``. When both identifiers are available they MUST
    agree; conflicting canonical evidence is ignored (fail closed).
    """
    if not isinstance(dispatch_id, str) or not dispatch_id:
        return None
    if not isinstance(messages, list):
        return None
    want_message = (
        message_id.strip()
        if isinstance(message_id, str) and message_id.strip()
        else None
    )
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        if message.get("type") != "worker_done":
            continue
        msg_id = message.get("id") or message.get("messageId")
        if isinstance(msg_id, str):
            msg_id = msg_id.strip() or None
        else:
            msg_id = None
        body = _parse_object(message.get("payload"))
        if not isinstance(body, Mapping):
            continue
        body_dispatch = body.get("dispatchId") or body.get("dispatch_id")
        if isinstance(body_dispatch, str):
            body_dispatch = body_dispatch.strip() or None
        else:
            body_dispatch = None
        id_match = want_message is not None and msg_id == want_message
        dispatch_match = body_dispatch == dispatch_id
        if want_message is not None and body_dispatch is not None:
            # Both sides of the evidence are present: require agreement.
            matched = id_match and dispatch_match
        else:
            matched = id_match or dispatch_match
        if not matched:
            continue
        code = receipt_failure_code(body) or receipt_failure_code(
            {"payload": message.get("payload")}
        )
        if code:
            return code
    return None


def resolve_worker_failure(worker_receipt, *, durable_messages=None) -> str | None:
    """Canonical typed failure for pre-dispatch authorize and settle audit.

    Prefer explicit fields on the worker/dispatch receipt; when those omit the
    class (Orca 1.4.200 ``lastFailure``), fall back to durable ``worker_done``
    message payloads passed by the caller.
    """
    if not isinstance(worker_receipt, Mapping):
        return None
    code = receipt_failure_code(worker_receipt)
    if code:
        return code
    if durable_messages is None:
        return None
    dispatch = worker_receipt.get("dispatch_id") or worker_receipt.get("dispatchId")
    return failure_code_from_durable_messages(
        dispatch,
        durable_messages,
        message_id=last_failure_message_id(worker_receipt),
    )


def _task_role(task):
    return task.get("role") or task.get("task_title") or task.get("title")


def _require_dispatch_role_adoption(
    *,
    run_id,
    task_id,
    role,
    dispatch_id,
    expected,
    quota_target=None,
    quota_mode="manual",
    dispatch_role_receipts=(),
):
    """Inner workers MUST have been started via ``aichestra dispatch-role``.

    Coordinator bootstrap (``mode_c_handoff``) is started by the Orca adapter,
    not this entrypoint. A prompt that merely prefers dispatch-role is not
    evidence; missing or mismatched receipts fail closed.
    """
    if role not in ROLE_KEYS:
        return
    match = None
    for rec in dispatch_role_receipts or ():
        if not isinstance(rec, dict):
            continue
        if (
            rec.get("operation") == ROLE_DISPATCH_OPERATION
            and rec.get("run_id") == run_id
            and rec.get("task_id") == task_id
            and rec.get("role") == role
            and rec.get("dispatch_id") == dispatch_id
        ):
            match = rec
            break
    if match is None:
        raise ValueError(
            f"Dispatch {dispatch_id} has no dispatch-role adoption receipt"
        )
    recorded_target = match.get("execution_target_id")
    expected_id = getattr(expected, "id", None)
    quota_id = getattr(quota_target, "id", None) if quota_target is not None else None
    reason = match.get("reason") or "primary"
    if reason == "quota-fallback":
        if not (
            role == "implement"
            and quota_mode == "auto"
            and quota_id
            and recorded_target == quota_id
        ):
            raise ValueError(
                f"Dispatch {dispatch_id} adoption receipt target mismatch"
            )
        return
    if expected_id and recorded_target != expected_id:
        raise ValueError(
            f"Dispatch {dispatch_id} adoption receipt target mismatch"
        )


def validate_role_receipts(run_id, tasks, workers, receipts, role_targets,
                           *, bootstrap_target=None, quota_target=None,
                           quota_mode="manual", dispatch_role_receipts=None,
                           durable_messages=None):
    """Audit canonical Orca receipts, never LLM summaries or prompt claims.

    Every worker must refer to a known same-Run task with a declared role.
    Unknown receipt schemas fail closed. Fallback needs a prior structured
    quota outcome for implement in the same Run, not generated output.
    Inner worker Dispatches also require a matching Aichestra dispatch-role
    adoption receipt; coordinator bootstrap does not.

    ``durable_messages`` is the same worker_done mail evidence
    ``authorize_task`` reads via ``check --all`` when ``lastFailure`` omits
    a typed failure class (Orca 1.4.200).
    """
    from aichestra.execution.launch_strategies import extract_attested_binding
    task_map = {t.get("id") or t.get("taskId"): t for t in tasks}
    if not task_map or None in task_map or len(task_map) != len(tasks):
        raise ValueError("Missing or duplicate canonical task ids")
    if not workers:
        raise ValueError("No canonical dispatch receipts")
    observed = set()
    superseded = set()
    quota_tasks = set()
    # Receipt order must be canonical; fallback is accepted only with a prior
    # failed attempt whose completion precedes the replacement's creation.
    for worker in workers:
        dispatch = worker.get("dispatch_id") or worker.get("dispatchId")
        if not dispatch or dispatch in observed or dispatch not in receipts:
            raise ValueError("Missing or duplicate dispatch receipt")
        observed.add(dispatch)
        payload = receipts[dispatch]
        data, row = _worker_record(payload)
        actual_id = row.get("dispatch_id") or row.get("dispatchId") or data.get("dispatchId")
        task_id = row.get("task_id") or row.get("taskId")
        task = task_map.get(task_id)
        if actual_id != dispatch or task is None:
            raise ValueError(f"Unbound dispatch {dispatch}")
        if (row.get("run_id") or row.get("runId")) != run_id or (task.get("run_id") or task.get("runId")) != run_id:
            raise ValueError(f"Cross-Run dispatch {dispatch}")
        role = _task_role(task)
        expected = bootstrap_target if role == "mode_c_handoff" else role_targets.get(role)
        if expected is None:
            raise ValueError(f"Task {task_id} has undeclared role {role!r}")
        _require_dispatch_role_adoption(
            run_id=run_id,
            task_id=task_id,
            role=role,
            dispatch_id=dispatch,
            expected=expected,
            quota_target=quota_target,
            quota_mode=quota_mode,
            dispatch_role_receipts=dispatch_role_receipts,
        )
        from aichestra.execution.launch_strategies import (
            binding_matches_target,
            receipt_launch,
        )

        # Prefer top-level launch; accept Orca startOptions.launch.effective.
        # Bare startOptions.agent is requested preference only — not evidence.
        launch = (
            receipt_launch(payload)
            or receipt_launch(data)
            or receipt_launch({"worker": row})
            or {}
        )
        actual = extract_attested_binding({"effective": launch.get("effective", {})})
        if not actual:
            raise ValueError(f"Dispatch {dispatch} has no effective launch binding")

        def matches(target):
            # Same comparator as launch proof — never a divergent endpoint check.
            return binding_matches_target(actual, target)

        if not matches(expected):
            prior = [r for r in receipts.values() if r is not payload]
            def quota_before(p):
                _, w = _worker_record(p)
                prior_task_id = w.get("task_id") or w.get("taskId")
                prior_task = task_map.get(prior_task_id, {})
                same_work = prior_task_id == task_id or (
                    role == "implement" and _task_role(prior_task) == "implement")
                try:
                    prior_done = receipt_timestamp(w, "completed_at", "completedAt")
                    current_created = receipt_timestamp(row, "created_at", "createdAt")
                    ordered = bool(prior_done and current_created and prior_done <= current_created)
                except (TypeError, ValueError):
                    return False
                return (same_work and (w.get("run_id") or w.get("runId")) == run_id
                        and resolve_worker_failure(w, durable_messages=durable_messages) == "quota"
                        and ordered)
            if not (role == "implement" and quota_mode == "auto"
                    and quota_target is not None and matches(quota_target)
                    and any(quota_before(p) for p in prior)):
                raise ValueError(f"Dispatch {dispatch} violates binding for role {role}")
            if task.get("status") == "completed":
                for p in prior:
                    if quota_before(p):
                        _, w = _worker_record(p)
                        superseded.add(w.get("task_id") or w.get("taskId"))
        if resolve_worker_failure(row, durable_messages=durable_messages) == "quota":
            quota_tasks.add(task_id)
    dispatched_tasks = {(_worker_record(p)[1].get("task_id") or _worker_record(p)[1].get("taskId"))
                        for p in receipts.values()}
    if set(task_map) - dispatched_tasks:
        raise ValueError("Task has no canonical dispatch evidence")
    if quota_mode == "manual" and quota_tasks:
        raise ValueError("Quota exhausted; manual mode requires operator action via aichestra settings")
    return {"ok": True, "dispatches_checked": len(observed), "superseded_quota_tasks": sorted(superseded)}
