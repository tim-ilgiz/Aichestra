"""Exact role resolution; no task graph or agent scheduling lives here."""
from __future__ import annotations

import hashlib

from aichestra.config.roles import RoleBinding
from aichestra.execution.domain import ExecutionTarget


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


def _task_role(task):
    return task.get("role") or task.get("task_title") or task.get("title")


def validate_role_receipts(run_id, tasks, workers, receipts, role_targets,
                           *, bootstrap_target=None, quota_target=None,
                           quota_mode="manual"):
    """Audit canonical Orca receipts, never LLM summaries or prompt claims.

    Every worker must refer to a known same-Run task with a declared role.
    Unknown receipt schemas fail closed. Fallback needs a prior structured
    quota outcome for implement in the same Run, not generated output.
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
    from datetime import datetime
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
        from aichestra.execution.launch_strategies import receipt_launch

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
            return (actual["runtime"] == target.runtime.id
                    and actual["provider"] == (target.provider.id if target.provider else None)
                    and (target.model is None or actual["model"] == target.model.id)
                    and (actual["endpoint"] or "").rstrip("/") == (target.endpoint or "").rstrip("/"))

        if not matches(expected):
            prior = [r for r in receipts.values() if r is not payload]
            def quota_before(p):
                _, w = _worker_record(p)
                prior_task_id = w.get("task_id") or w.get("taskId")
                prior_task = task_map.get(prior_task_id, {})
                same_work = prior_task_id == task_id or (
                    role == "implement" and _task_role(prior_task) == "implement")
                try:
                    ordered = datetime.fromisoformat(w["completed_at"]) <= datetime.fromisoformat(row["created_at"])
                except (KeyError, TypeError, ValueError):
                    return False
                return (same_work and (w.get("run_id") or w.get("runId")) == run_id
                        and w.get("failure") == "quota" and ordered)
            if not (role == "implement" and quota_mode == "auto"
                    and quota_target is not None and matches(quota_target)
                    and any(quota_before(p) for p in prior)):
                raise ValueError(f"Dispatch {dispatch} violates binding for role {role}")
            if task.get("status") == "completed":
                for p in prior:
                    if quota_before(p):
                        _, w = _worker_record(p)
                        superseded.add(w.get("task_id") or w.get("taskId"))
        if row.get("failure") == "quota":
            quota_tasks.add(task_id)
    dispatched_tasks = {(_worker_record(p)[1].get("task_id") or _worker_record(p)[1].get("taskId"))
                        for p in receipts.values()}
    if set(task_map) - dispatched_tasks:
        raise ValueError("Task has no canonical dispatch evidence")
    if quota_mode == "manual" and quota_tasks:
        raise ValueError("Quota exhausted; manual mode requires operator action via aichestra settings")
    return {"ok": True, "dispatches_checked": len(observed), "superseded_quota_tasks": sorted(superseded)}
