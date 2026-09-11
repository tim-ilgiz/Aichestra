"""Policy-enforced role Dispatch — not a second Mode C orchestrator.

Coordinator under Orca still owns the DAG (which Tasks to create and when).
When a Task for a configured worker role is ready, call this entrypoint instead
of inventing a worker-start agent. Aichestra resolves the immutable Run binding
to one exact ExecutionTarget, proves launch when required, then starts that
target on the existing Orca Task.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from aichestra.config.layering import resolve_config
from aichestra.config.roles import ROLE_KEYS, RoleBinding

from aichestra.execution.launch_strategies import (
    LaunchContext,
    adapter_for,
    abort_prepared,
    prove_launch,
    receipt_launch,
    serialize_prepared_launch,
)
from aichestra.execution.run_contract import (
    authorize_task,
    load_contract,
    save_dispatch_role_receipt,
)
from aichestra.execution.roles import contract_endpoint_matches
from aichestra.execution.serialize import (
    ROLE_DISPATCH_OPERATION,
    resolve_mode_c_execution,
    serialize_execution_target,
)
from aichestra.providers.orca import resolve_orca_binary
from aichestra.repo import trusted_config_root, resolve_aichestra_config_root

RunFn = Callable[..., subprocess.CompletedProcess[str]]


def _payload(data: Mapping[str, Any]) -> dict[str, Any]:
    return dict(data)


def _parse_json(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dig_id(payload: Mapping[str, Any], *keys: str) -> str | None:
    cur: Any = payload
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    if isinstance(cur, str) and cur.strip():
        return cur.strip()
    return None


def _snapshot_launch_effective(run_fn: RunFn, binary: str, dispatch_id: str) -> dict[str, Any] | None:
    """Best-effort worker-show snapshot. Settle audit still reads Orca."""
    try:
        show = run_fn(
            [binary, "orchestration", "worker-show", "--dispatch", dispatch_id, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, TypeError, KeyError, IndexError, ValueError):
        return None
    shown = _parse_json(getattr(show, "stdout", "") or "")
    if getattr(show, "returncode", 1) != 0:
        return None
    data = shown.get("result", shown) if isinstance(shown.get("result"), dict) else shown
    worker = data.get("worker") if isinstance(data, dict) else None
    launch = (
        receipt_launch(shown)
        or receipt_launch(data if isinstance(data, dict) else {})
        or receipt_launch({"worker": worker} if isinstance(worker, dict) else {})
        or {}
    )
    effective = launch.get("effective") if isinstance(launch, dict) else None
    return dict(effective) if isinstance(effective, dict) else None


def dispatch_role(
    *,
    role: str,
    run_id: str,
    task_id: str,
    project_root: str | Path,
    repo_root: str | Path | None = None,
    reason: str = "primary",
    worktree: str = "current",
    from_handle: str | None = None,
    binary: str | None = None,
    config: Mapping[str, Any] | None = None,
    targets=None,
    run: RunFn | None = None,
) -> dict[str, Any]:
    """Resolve saved Run role → exact target → prove → Orca worker-start.

    Does not create Tasks or own the DAG. Fail closed if the role is unknown,
    the binding cannot be resolved, or Orca refuses same-Run worker-start.
    """
    role_key = str(role or "").strip().lower()
    if role_key not in ROLE_KEYS:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "error": (
                f"unknown worker role {role!r}; expected one of {list(ROLE_KEYS)}"
            ),
        })

    rid = str(run_id or "").strip()
    tid = str(task_id or "").strip()
    if not rid or not tid:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": "--run and --task are required",
        })

    root = Path(project_root).resolve()
    if not root.is_dir():
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": f"project_root is not a directory: {root}",
        })

    try:
        aichestra_root = resolve_aichestra_config_root(
            repo_root=Path(repo_root).resolve() if repo_root else None,
            project_root=root,
        )
    except ValueError as exc:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": str(exc),
        })

    trusted = trusted_config_root(aichestra_root)
    if not trusted:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": (
                "Aichestra config root required for dispatch-role: pass "
                "--repo-root or set AICHESTRA_REPO_ROOT"
            ),
        })

    cfg = (
        dict(config)
        if config is not None
        else resolve_config(repo_root=aichestra_root, project_root=root)
    )
    try:
        contract = load_contract(aichestra_root, rid, root)
        if reason not in {"primary", "quota-fallback"}:
            raise ValueError("unknown dispatch reason")
        entry = contract["bindings"].get(role_key)
        if reason == "quota-fallback":
            if role_key != "implement" or contract["quota"]["mode"] != "auto":
                raise ValueError("quota fallback requires implement and Run quota.mode=auto")
            entry = contract["quota"]["roles"].get("implement")
        if not entry:
            raise ValueError(f"unknown role {role_key} in Run contract")
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "operation": ROLE_DISPATCH_OPERATION, "error": str(exc)}

    if targets is None:
        try:
            targets, _policy, _facts = resolve_mode_c_execution(
                cfg, required_bindings=(RoleBinding(
                    runtime=entry["runtime"], provider=entry["provider"],
                    model=entry["model"],
                ),),
            )
        except ValueError as exc:
            return _payload({
                "ok": False,
                "operation": ROLE_DISPATCH_OPERATION,
                "role": role_key,
                "error": str(exc),
            })

    try:
        matches = [t for t in targets if t.id == entry["execution_target_id"]
                   and t.runtime.id == entry["runtime"]
                   and (t.provider.id if t.provider else None) == entry["provider"]
                   and (t.model.id if t.model else None) == entry["model"]
                   and contract_endpoint_matches(entry, t.endpoint)
                   and t.preparable]
        if len(matches) != 1:
            raise ValueError("Run contract target unavailable or disabled; refusing substitution")
        target = matches[0]
    except (ValueError, KeyError, TypeError) as exc:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": str(exc),
        })

    orca_binary = (binary or "").strip() or resolve_orca_binary() or ""
    if not orca_binary:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": "Orca binary required for dispatch-role",
            "execution_target": serialize_execution_target(target),
        })

    run_fn = run if run is not None else subprocess.run
    try:
        # Authorize against canonical Orca task/run state only. Do NOT call
        # run-use here: Orca run-use transfers Run ownership to the calling
        # terminal and can fence the coordinator that still owns the DAG.
        # worker-start --task is sufficient for an already-associated Task.
        authorize_task(
            orca_binary,
            run_fn,
            rid,
            tid,
            role_key,
            contract,
            reason,
            from_handle=from_handle,
        )
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "operation": ROLE_DISPATCH_OPERATION, "error": str(exc)}

    wt = str(worktree or "current").strip() or "current"
    launch_ctx = LaunchContext(
        binary=orca_binary,
        worktree=wt,
        terminal_handle=target.launch_ref,
        run=run_fn,
    )
    proved = False
    prepared = None
    try:
        if not target.dispatchable:
            target, prepared = prove_launch(target, launch_ctx)
            proved = True
        else:
            try:
                prepared = adapter_for(target).prepare(target, launch_ctx)
            except ValueError:
                # Already-proven native targets may use project agent ids.
                from aichestra.execution.domain import LaunchStrategy
                if (target.launch_strategy != LaunchStrategy.ORCA_NATIVE
                        or target.provider is not None or target.model is not None
                        or target.endpoint is not None):
                    raise
                from aichestra.execution.launch_strategies import PreparedLaunch

                prepared = PreparedLaunch(
                    arguments=["--agent", target.runtime.id],
                    terminal_handle=target.launch_ref,
                    owns_terminal=False,
                )
    except ValueError as exc:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": str(exc),
            "execution_target": serialize_execution_target(target),
        })

    if not target.dispatchable:
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "error": "ExecutionTarget is not dispatchable after launch proof",
            "execution_target": serialize_execution_target(target),
        })

    name = f"aichestra-{role_key}"
    worker_argv = [
        orca_binary,
        "orchestration",
        "worker-start",
        "--task",
        tid,
        "--run",
        rid,
        "--worktree",
        wt,
        "--name",
        name,
        "--agent",
        target.runtime.id,
        "--setup",
        "skip",
        "--json",
    ]
    sender = (from_handle or "").strip()
    if sender:
        worker_argv.extend(["--from", sender])
    index = worker_argv.index("--agent")
    worker_argv[index : index + 2] = list(prepared.arguments)
    if wt not in {"new-child", "new-top-level"}:
        for flag in ("--name", "--setup"):
            if flag in worker_argv:
                i = worker_argv.index(flag)
                del worker_argv[i : i + 2]

    start = run_fn(
        worker_argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    start_payload = _parse_json(start.stdout or "")
    # Never treat Orca envelope id ("local"/UUID) as a Dispatch id.
    dispatch_id = (
        _dig_id(start_payload, "result", "dispatchId")
        or _dig_id(start_payload, "result", "dispatch_id")
        or _dig_id(start_payload, "result", "dispatch", "id")
        or _dig_id(start_payload, "dispatchId")
        or _dig_id(start_payload, "dispatch_id")
    )
    ok = start.returncode == 0 and bool(dispatch_id)
    if not ok:
        abort_prepared(prepared, launch_ctx)
        return _payload({
            "ok": False,
            "operation": ROLE_DISPATCH_OPERATION,
            "role": role_key,
            "run_id": rid,
            "task_id": tid,
            "dispatch_id": dispatch_id,
            "execution_target_id": target.id,
            "execution_target": serialize_execution_target(target),
            "launch_proved": proved,
            "prepared_launch": serialize_prepared_launch(prepared),
            "worker_argv": worker_argv[1:],
            "orca_exit_code": start.returncode,
            "orca_stdout": (start.stdout or "")[:4000],
            "orca_stderr": (start.stderr or "")[:2000],
            "error": f"orca worker-start exited {start.returncode}",
        })

    launch_effective = _snapshot_launch_effective(run_fn, orca_binary, dispatch_id)
    adoption = {
        "operation": ROLE_DISPATCH_OPERATION,
        "run_id": rid,
        "task_id": tid,
        "role": role_key,
        "dispatch_id": dispatch_id,
        "execution_target_id": target.id,
        "reason": reason,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **({"launch_effective": launch_effective} if launch_effective else {}),
    }
    persisted = False
    persist_error = None
    try:
        save_dispatch_role_receipt(aichestra_root, adoption)
        persisted = True
    except (OSError, ValueError, TypeError) as exc:
        persist_error = str(exc)
    return _payload({
        "ok": True,
        "operation": ROLE_DISPATCH_OPERATION,
        "role": role_key,
        "run_id": rid,
        "task_id": tid,
        "dispatch_id": dispatch_id,
        "execution_target_id": target.id,
        "execution_target": serialize_execution_target(target),
        "launch_proved": proved,
        "launch_effective": launch_effective,
        "adoption_receipt_persisted": persisted,
        "prepared_launch": serialize_prepared_launch(prepared),
        "worker_argv": worker_argv[1:],
        "orca_exit_code": start.returncode,
        "orca_stdout": (start.stdout or "")[:4000],
        "orca_stderr": (start.stderr or "")[:2000],
        **({"error": persist_error} if persist_error else {}),
    })
