"""Orca launch contracts, separate from inference discovery and scheduling.

Prefer native worker-start. Use an existing terminal or a bounded terminal bridge
only when native launch cannot express the required dimensions. Schema support
selects a strategy; every dispatch must still confirm the effective binding.
Never treat prompt text, ambient discovery, or Aichestra-process env as proof.
"""
from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from aichestra.platform_detect import OperatingSystem, detect_os
from aichestra.security.sanitize import sanitize_mapping

from .domain import ExecutionTarget, LaunchCapability, LaunchStrategy


def _commands(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    commands = schema.get("commands")
    if not isinstance(commands, list):
        return []
    return [c for c in commands if isinstance(c, dict)]


def _command(schema: Mapping[str, Any], *path: str) -> dict[str, Any] | None:
    wanted = list(path)
    for command in _commands(schema):
        if command.get("path") == wanted:
            return command
    return None


def _flags(command: dict[str, Any] | None) -> set[str]:
    if not command:
        return set()
    raw = command.get("flags", ())
    return set(raw) if isinstance(raw, (list, tuple, set)) else set()


def _effective(receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    data = receipt.get("result", receipt)
    if not isinstance(data, dict):
        return None
    launch = data.get("launch")
    if not isinstance(launch, dict):
        return None
    effective = launch.get("effective")
    return effective if isinstance(effective, dict) else None


def _normalize_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    text = endpoint.strip().rstrip("/")
    if not text:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.netloc:
        return text
    path = parts.path.rstrip("/") or ""
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def expected_binding(target: ExecutionTarget) -> dict[str, str | None]:
    """Exact dimensions the live process must attest (no substring matching)."""
    return {
        "runtime": target.runtime.id,
        "provider": target.provider.id if target.provider else None,
        "model": target.model.id if target.model else None,
        "endpoint": _normalize_endpoint(target.endpoint),
        "binary": target.runtime.binary_path or target.runtime.id,
    }


def _endpoints_equal(left: str | None, right: str | None) -> bool:
    a = _normalize_endpoint(left)
    b = _normalize_endpoint(right)
    if a is None or b is None:
        return a is None and b is None
    if a == b:
        return True
    # OpenCode baseURL may include a trailing /v1 the discovery endpoint omitted.
    a_v1 = a if a.endswith("/v1") else f"{a}/v1"
    b_v1 = b if b.endswith("/v1") else f"{b}/v1"
    return a_v1 == b_v1


@dataclass(frozen=True)
class LaunchContext:
    """Runtime inputs for multi-step launches (bridge / existing terminal)."""

    binary: str
    worktree: str = "current"
    terminal_handle: str | None = None
    run: Callable[..., Any] | None = None


@dataclass(frozen=True)
class PreparedLaunch:
    """Exact worker-start argv fragment replacing the default ``--agent`` pair."""

    arguments: list[str]
    terminal_handle: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    owns_terminal: bool = False


class LaunchAdapter(Protocol):
    strategy: LaunchStrategy

    def accepts(self, target: ExecutionTarget) -> bool: ...
    def prove(self, target: ExecutionTarget, schema: dict) -> bool: ...
    def arguments(self, target: ExecutionTarget) -> list[str]: ...
    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool: ...
    def prepare(self, target: ExecutionTarget, ctx: LaunchContext) -> PreparedLaunch: ...


def abort_prepared(prepared: PreparedLaunch, ctx: LaunchContext) -> dict[str, Any]:
    """Close only a bridge-owned exact terminal handle; never a foreign terminal."""
    handle = (prepared.terminal_handle or "").strip()
    if not prepared.owns_terminal or not handle or ctx.run is None:
        return {"skipped": True, "reason": "not_owned"}
    try:
        result = ctx.run(
            [ctx.binary, "terminal", "close", "--terminal", handle, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "skipped": False,
            "handle": handle,
            "ok": getattr(result, "returncode", 1) in (0, None),
            "returncode": getattr(result, "returncode", None),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"skipped": False, "handle": handle, "ok": False, "error": str(exc)}


def _worker_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Missing/malformed worker lists are not evidence of an unbound terminal."""
    data = payload.get("result", payload.get("value", payload))
    if isinstance(data, dict):
        data = data.get("workers")
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        return None
    return data


def _row_terminal_handles(row: Mapping[str, Any]) -> set[str]:
    handles: set[str] = set()
    for key in ("agentTerminalHandle", "terminalHandle"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            handles.add(value.strip())
    resource = row.get("resource")
    if isinstance(resource, Mapping):
        value = resource.get("terminalHandle")
        if isinstance(value, str) and value.strip():
            handles.add(value.strip())
    return handles


def _row_dispatch_id(row: Mapping[str, Any]) -> str | None:
    for key in ("dispatchId", "dispatch_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    resource = row.get("resource")
    if isinstance(resource, Mapping):
        for key in ("ownerDispatchId", "originDispatchId"):
            value = resource.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _probe_terminal_dispatch(
    handle: str,
    *,
    binary: str,
    run: Callable[..., Any],
) -> dict[str, Any]:
    """Ask Orca whether ``handle`` is bound to a Dispatch/worker.

    Fail closed: a failed or unstructured ``worker-list`` is not proof that
    the terminal is unbound.
    """
    owned = handle.strip()
    try:
        result = run(
            [binary, "orchestration", "worker-list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "bound": False, "error": str(exc)}
    if getattr(result, "returncode", 1) not in (0, None):
        return {
            "ok": False,
            "bound": False,
            "error": "orca orchestration worker-list failed",
            "returncode": getattr(result, "returncode", None),
        }
    payload = _json_payload(result)
    if payload.get("ok") is False:
        return {"ok": False, "bound": False, "error": "orca worker-list returned ok=false"}
    rows = _worker_rows(payload)
    if rows is None:
        return {"ok": False, "bound": False, "error": "orca worker-list receipt is unstructured"}
    for row in rows:
        if owned not in _row_terminal_handles(row):
            continue
        dispatch_id = _row_dispatch_id(row)
        binding = {
            "dispatchId": dispatch_id,
            "agentTerminalHandle": owned,
        }
        state = row.get("terminalState")
        if isinstance(state, str) and state.strip():
            binding["terminalState"] = state.strip()
        return {
            "ok": True,
            "bound": True,
            "dispatch_id": dispatch_id,
            "binding": binding,
        }
    return {"ok": True, "bound": False}


def abort_launch_by_token(
    token: str,
    *,
    binary: str | None = None,
    run: Callable[..., Any] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Owner-side cleanup for a prove-launch bridge that never reached Dispatch.

    ``token`` is an opaque cleanup lease issued by Aichestra after an owned
    bridge proof. Unknown or already-consumed leases fail closed without
    calling ``terminal close``. Before closing, abort asks Orca whether the
    stored handle is already bound to a Dispatch; a structured binding
    consumes the lease and leaves the terminal running. Probe or close
    failure restores the lease so abort can be retried.
    """
    from pathlib import Path

    from aichestra.providers.orca import resolve_orca_binary
    from aichestra.repo import resolve_aichestra_config_root

    from .cleanup_leases import (
        claim_cleanup_lease,
        consume_claimed_lease,
        restore_cleanup_lease,
    )
    from .serialize import LAUNCH_ABORT_OPERATION

    lease = str(token or "").strip()
    if not lease:
        return {
            "ok": False,
            "operation": LAUNCH_ABORT_OPERATION,
            "error": "cleanup lease is required",
        }
    try:
        root = resolve_aichestra_config_root(
            repo_root=Path(repo_root).resolve() if repo_root else None,
        )
    except ValueError as exc:
        return {
            "ok": False,
            "operation": LAUNCH_ABORT_OPERATION,
            "error": str(exc),
        }

    def _fail(error: str, **extra: Any) -> dict[str, Any]:
        restore_cleanup_lease(lease, repo_root=root)
        payload: dict[str, Any] = {
            "ok": False,
            "operation": LAUNCH_ABORT_OPERATION,
            "error": error,
        }
        payload.update(extra)
        return payload

    handle = claim_cleanup_lease(lease, repo_root=root)
    if not handle:
        return {
            "ok": False,
            "operation": LAUNCH_ABORT_OPERATION,
            "error": "unknown or already consumed cleanup lease",
        }
    runner = run if run is not None else subprocess.run
    orca_binary = (binary or "").strip() or resolve_orca_binary() or ""
    if not orca_binary:
        return _fail("Orca binary required for abort-launch")
    probe = _probe_terminal_dispatch(handle, binary=orca_binary, run=runner)
    if not probe.get("ok"):
        return _fail(
            "Orca dispatch probe failed; cleanup lease restored",
            probe=probe,
        )
    if probe.get("bound"):
        consume_claimed_lease(lease, repo_root=root)
        return {
            "ok": True,
            "operation": LAUNCH_ABORT_OPERATION,
            "result": {
                "skipped": True,
                "reason": "dispatched",
                "handle": handle,
                "ok": True,
            },
            "dispatch_id": probe.get("dispatch_id"),
            "binding": probe.get("binding"),
        }
    result = abort_prepared(
        PreparedLaunch(arguments=[], terminal_handle=handle, owns_terminal=True),
        LaunchContext(
            binary=orca_binary,
            worktree="current",
            run=runner,
        ),
    )
    if not (result.get("ok") and not result.get("skipped")):
        return _fail(
            "terminal close failed; cleanup lease restored",
            result=result,
        )
    consume_claimed_lease(lease, repo_root=root)
    return {
        "ok": True,
        "operation": LAUNCH_ABORT_OPERATION,
        "result": result,
    }


def _close_owned_terminal(ctx: LaunchContext, handle: str) -> None:
    abort_prepared(
        PreparedLaunch(arguments=[], terminal_handle=handle, owns_terminal=True),
        ctx,
    )


@dataclass(frozen=True)
class NativeLaunch:
    """Runtime-managed inference: agent only; no provider/model/endpoint claim."""

    runtime: str
    strategy: LaunchStrategy = LaunchStrategy.ORCA_NATIVE

    def accepts(self, target: ExecutionTarget) -> bool:
        return (
            target.runtime.id == self.runtime
            and target.provider is None
            and target.model is None
            and target.endpoint is None
        )

    def prove(self, target: ExecutionTarget, schema: dict) -> bool:
        command = _command(schema, "orchestration", "worker-start")
        return self.accepts(target) and {"agent", "task", "worktree"} <= _flags(command)

    def arguments(self, target: ExecutionTarget) -> list[str]:
        if not self.accepts(target):
            raise ValueError("Native launch cannot bind an explicit inference backend")
        return ["--agent", target.runtime.id]

    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool:
        effective = _effective(receipt)
        return (
            effective is not None
            and effective.get("agent") == target.runtime.id
            and target.provider is None
            and target.model is None
            and target.endpoint is None
        )

    def prepare(self, target: ExecutionTarget, ctx: LaunchContext) -> PreparedLaunch:
        return PreparedLaunch(arguments=self.arguments(target))


@dataclass(frozen=True)
class NativeModelLaunch:
    """Native worker-start with opaque ``--model`` when no backend/endpoint is claimed.

    Orca reports the preference under ``launch.effective``. A provider or endpoint
    dimension still requires an attested terminal strategy.
    """

    runtime: str
    strategy: LaunchStrategy = LaunchStrategy.ORCA_NATIVE

    def accepts(self, target: ExecutionTarget) -> bool:
        return (
            target.runtime.id == self.runtime
            and target.provider is None
            and target.endpoint is None
            and target.model is not None
        )

    def prove(self, target: ExecutionTarget, schema: dict) -> bool:
        command = _command(schema, "orchestration", "worker-start")
        return self.accepts(target) and {"agent", "task", "worktree", "model"} <= _flags(
            command
        )

    def arguments(self, target: ExecutionTarget) -> list[str]:
        if not self.accepts(target) or target.model is None:
            raise ValueError("Native model launch requires runtime + model only")
        return ["--agent", target.runtime.id, "--model", target.model.id]

    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool:
        effective = _effective(receipt)
        if effective is None or target.model is None:
            return False
        return (
            effective.get("agent") == target.runtime.id
            and effective.get("model") == target.model.id
            and target.provider is None
            and target.endpoint is None
        )

    def prepare(self, target: ExecutionTarget, ctx: LaunchContext) -> PreparedLaunch:
        return PreparedLaunch(arguments=self.arguments(target))


@dataclass(frozen=True)
class ExistingTerminalLaunch:
    """Attach a pre-started Orca terminal whose process already matches the target."""

    strategy: LaunchStrategy = LaunchStrategy.ORCA_EXISTING_TERMINAL

    def accepts(self, target: ExecutionTarget) -> bool:
        # Any exact binding may attach when a proven handle is supplied at launch.
        return bool(target.runtime.id)

    def prove(self, target: ExecutionTarget, schema: dict) -> bool:
        # Discovery cannot invent a live handle. Existing-terminal becomes runnable
        # only when prepare/confirms receive attested terminal evidence.
        return False

    def arguments(self, target: ExecutionTarget) -> list[str]:
        raise ValueError("Existing-terminal launch requires prepare() with a handle")

    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool:
        evidence = receipt.get("aichestra_terminal_evidence")
        if not isinstance(evidence, Mapping):
            return False
        if not structured_binding_matches(target, evidence):
            return False
        expected = evidence.get("handle")
        if not isinstance(expected, str) or not expected.strip():
            return False
        data = receipt.get("result", receipt)
        if isinstance(data, dict):
            handle = _receipt_terminal_handle(data)
            if handle and handle != expected:
                return False
        return True

    def prepare(self, target: ExecutionTarget, ctx: LaunchContext) -> PreparedLaunch:
        handle = (ctx.terminal_handle or "").strip()
        if not handle:
            raise ValueError("Existing-terminal launch requires terminal_handle")
        if ctx.run is None:
            raise ValueError("Existing-terminal launch requires an Orca runner")
        show = ctx.run(
            [ctx.binary, "terminal", "show", "--terminal", handle, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        read = ctx.run(
            [ctx.binary, "terminal", "read", "--terminal", handle, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        evidence = {**merge_process_evidence(show, read), "handle": handle}
        if not structured_binding_matches(target, evidence):
            raise ValueError("Existing terminal does not prove the requested binding")
        # Never owns a foreign terminal — cleanup must not close this handle.
        return PreparedLaunch(
            arguments=["--terminal", handle],
            terminal_handle=handle,
            evidence=evidence,
            owns_terminal=False,
        )


@dataclass(frozen=True)
class TerminalBridgeLaunch:
    """Create an Orca-managed agent terminal with an explicit binding, then attach.

    Aichestra only issues Orca CLI steps; it does not own the agent loop.
    """

    strategy: LaunchStrategy = LaunchStrategy.ORCA_TERMINAL_BRIDGE

    def accepts(self, target: ExecutionTarget) -> bool:
        return target.provider is not None and bridge_command_for(target) is not None

    def prove(self, target: ExecutionTarget, schema: dict) -> bool:
        create = _command(schema, "terminal", "create")
        wait = _command(schema, "terminal", "wait")
        start = _command(schema, "orchestration", "worker-start")
        return (
            self.accepts(target)
            and {"command", "worktree"} <= _flags(create)
            and "for" in _flags(wait)
            and {"task", "worktree", "terminal"} <= _flags(start)
        )

    def arguments(self, target: ExecutionTarget) -> list[str]:
        raise ValueError("Terminal-bridge launch requires prepare() before worker-start")

    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool:
        evidence = receipt.get("aichestra_terminal_evidence")
        if not isinstance(evidence, Mapping):
            return False
        if not structured_binding_matches(target, evidence):
            return False
        expected = evidence.get("handle")
        if not isinstance(expected, str) or not expected.strip():
            return False
        data = receipt.get("result", receipt)
        if isinstance(data, dict):
            handle = _receipt_terminal_handle(data)
            if handle and handle != expected:
                return False
        return True

    def prepare(self, target: ExecutionTarget, ctx: LaunchContext) -> PreparedLaunch:
        if ctx.run is None:
            raise ValueError("Terminal-bridge launch requires an Orca runner")
        bridge = bridge_command_for(target)
        if bridge is None:
            raise ValueError("No terminal-bridge command for this ExecutionTarget")
        handle: str | None = None
        try:
            create = ctx.run(
                [
                    ctx.binary,
                    "terminal",
                    "create",
                    "--worktree",
                    ctx.worktree,
                    "--title",
                    f"aichestra-{target.runtime.id}",
                    "--command",
                    bridge.command,
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            create_payload = _json_payload(create)
            handle = _dig_handle(create_payload)
            if not handle:
                raise ValueError("terminal create did not return a handle")
            wait = ctx.run(
                [
                    ctx.binary,
                    "terminal",
                    "wait",
                    "--terminal",
                    handle,
                    "--for",
                    "tui-idle",
                    "--timeout-ms",
                    "120000",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=130,
            )
            if getattr(wait, "returncode", 1) not in (0, None):
                # Still attempt structured show/read proof; some agents never report tui-idle.
                pass
            # Prefer terminal show for process metadata; read alone is screen stream.
            show = ctx.run(
                [ctx.binary, "terminal", "show", "--terminal", handle, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            read = ctx.run(
                [ctx.binary, "terminal", "read", "--terminal", handle, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Process proof must come from structured live observations — never from
            # the terminal-create receipt's startupCommand echo of our own argv,
            # and never from screen/tail substring matching.
            evidence = {
                **merge_process_evidence(show, read),
                "handle": handle,
                "bridge_command": bridge.command,
                "expected_binding": expected_binding(target),
            }
            if not structured_binding_matches(target, evidence):
                raise ValueError("Terminal bridge did not prove the requested binding")
            return PreparedLaunch(
                arguments=["--terminal", handle],
                terminal_handle=handle,
                evidence=evidence,
                owns_terminal=True,
            )
        except Exception:
            if handle:
                _close_owned_terminal(ctx, handle)
            raise


@dataclass(frozen=True)
class BridgeCommand:
    command: str


def bridge_command_for(target: ExecutionTarget) -> BridgeCommand | None:
    """Runtime-specific argv data for an Orca-owned terminal; not workflow routing."""
    builder = BRIDGE_COMMAND_BUILDERS.get(target.runtime.id)
    if builder is None:
        return None
    return builder(target)


def _opencode_bridge_command(
    target: ExecutionTarget,
    *,
    os_name: str | None = None,
) -> BridgeCommand | None:
    if target.provider is None or target.model is None:
        return None
    provider = target.provider.id
    model_id = target.model.id
    endpoint = _normalize_endpoint(target.endpoint)
    if not endpoint:
        return None
    model_ref = f"{provider}/{model_id}"
    binary = target.runtime.binary_path or target.runtime.id
    base = endpoint if endpoint.endswith("/v1") else f"{endpoint}/v1"
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": model_ref,
        "provider": {
            provider: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"{provider} (Aichestra ExecutionTarget)",
                "options": {"baseURL": base},
                "models": {model_id: {"name": model_id}},
            }
        },
    }
    config_json = json.dumps(config, separators=(",", ":"))
    platform = (os_name or detect_os().value).lower()
    if platform == OperatingSystem.WINDOWS.value:
        encoded = base64.b64encode(config_json.encode("utf-8")).decode("ascii")
        ps_binary = str(binary).replace("'", "''")
        # Windows-native: POSIX ``env KEY=VAL`` is not a standard command.
        # Base64 avoids JSON quoting in PowerShell; live process env carries
        # OPENCODE_CONFIG_CONTENT after this wrapper execs the runtime.
        command = (
            "powershell.exe -NoProfile -NonInteractive -Command "
            "\"$env:OPENCODE_CONFIG_CONTENT = "
            "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            f"{encoded}')); & '{ps_binary}'\""
        )
        return BridgeCommand(command=command)
    # Binding travels in the Orca-supervised process command, not Aichestra env.
    command = (
        f"env OPENCODE_CONFIG_CONTENT={shlex.quote(config_json)} "
        f"{shlex.quote(binary)}"
    )
    return BridgeCommand(command=command)


BRIDGE_COMMAND_BUILDERS: dict[str, Callable[[ExecutionTarget], BridgeCommand | None]] = {
    "opencode": _opencode_bridge_command,
}


def _json_payload(result: Any) -> dict[str, Any]:
    stdout = getattr(result, "stdout", "") or ""
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except ValueError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _observation_ok(result: Any) -> bool:
    return getattr(result, "returncode", 1) in (0, None)


def _receipt_terminal_handle(data: Mapping[str, Any]) -> str | None:
    for key in ("agentTerminalHandle", "terminalHandle"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    effects = data.get("effects")
    if isinstance(effects, dict):
        terminal = effects.get("terminal")
        if isinstance(terminal, dict):
            handle = terminal.get("handle")
            if isinstance(handle, str) and handle.strip():
                return handle.strip()
    worker = data.get("worker")
    if isinstance(worker, dict):
        handle = worker.get("terminalHandle")
        if isinstance(handle, str) and handle.strip():
            return handle.strip()
    return None


def _dig_handle(payload: Mapping[str, Any]) -> str | None:
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        return None
    terminal = result.get("terminal", result)
    if isinstance(terminal, dict):
        handle = terminal.get("handle")
        if isinstance(handle, str) and handle.strip():
            return handle.strip()
    handle = result.get("handle")
    return handle.strip() if isinstance(handle, str) and handle.strip() else None


def _as_argv(value: Any) -> list[str] | None:
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            return list(shlex.split(value))
        except ValueError:
            return [value]
    return None


# Process env is not launch evidence. Only these keys may be lifted into
# structured config for binding proof — never a full environment dump.
_BINDING_ENV_ALLOWLIST = frozenset({"OPENCODE_CONFIG_CONTENT"})


def _iter_env_pairs(env: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(env, Mapping):
        for key, value in env.items():
            if isinstance(key, str) and isinstance(value, str):
                pairs.append((key, value))
        return pairs
    if isinstance(env, (list, tuple)):
        for item in env:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                pairs.append((key, value))
    return pairs


def _apply_allowlisted_env(into: dict[str, Any], env: Any) -> None:
    """Lift binding-only env into process.config; never copy the env mapping."""
    for key, value in _iter_env_pairs(env):
        if key not in _BINDING_ENV_ALLOWLIST:
            continue
        if key == "OPENCODE_CONFIG_CONTENT" and "config" not in into:
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            if isinstance(parsed, Mapping):
                into["config"] = dict(parsed)


def _merge_process_dict(into: dict[str, Any], process: Mapping[str, Any]) -> None:
    """Accumulate structured process fields; never screen preview/tail text."""
    for key in ("pid", "argv", "command", "commandLine", "executable", "cwd"):
        if key in process and process[key] is not None and key not in into:
            into[key] = process[key]
    if "env" in process:
        _apply_allowlisted_env(into, process.get("env"))
    # Nested effective / config blocks from the live process, not create echoes.
    for key in ("effective", "effectiveConfig", "config", "binding"):
        value = process.get(key)
        if isinstance(value, Mapping) and key not in into:
            into[key] = dict(value)


def merge_process_evidence(*results: Any) -> dict[str, Any]:
    """Collect structured live process observations only.

    Ignores ``command`` / ``startupCommand`` on the terminal object when they are
    create-receipt echoes, ignores screen ``preview`` / ``tail`` text, and
    requires every observation to return success.
    """
    if not results:
        raise ValueError("terminal observation failed (no results)")
    handle = None
    process: dict[str, Any] = {}
    effective: dict[str, Any] = {}
    for result in results:
        if not _observation_ok(result):
            raise ValueError("terminal observation failed (non-zero returncode)")
        payload = _json_payload(result)
        handle = handle or _dig_handle(payload)
        data = payload.get("result", payload)
        if not isinstance(data, dict):
            continue
        terminal = data.get("terminal", data)
        if not isinstance(terminal, dict):
            continue
        # Structured process attestation from Orca (pid/argv/effective).
        for key in ("process", "proc", "runtimeProcess"):
            proc = terminal.get(key)
            if isinstance(proc, Mapping):
                _merge_process_dict(process, proc)
        launch = terminal.get("launch")
        if isinstance(launch, Mapping):
            eff = launch.get("effective")
            if isinstance(eff, Mapping):
                effective.update(dict(eff))
        for key in ("effective", "effectiveConfig", "binding"):
            value = terminal.get(key)
            if isinstance(value, Mapping):
                if key == "effective":
                    effective.update(dict(value))
                elif key not in process:
                    process[key] = dict(value)
        # Live argv may also appear as processArgv without a process wrapper.
        for key in ("processArgv", "argv"):
            if key in terminal and "argv" not in process:
                argv = _as_argv(terminal.get(key))
                if argv is not None:
                    process["argv"] = argv
        if "pid" in terminal and "pid" not in process:
            process["pid"] = terminal["pid"]
    out: dict[str, Any] = {"handle": handle}
    if process:
        out["process"] = process
    if effective:
        out["effective"] = effective
    return out


def _binding_from_opencode_config(config: Mapping[str, Any], *, binary: str | None) -> dict[str, str | None]:
    model_ref = config.get("model")
    provider = None
    model = None
    if isinstance(model_ref, str) and "/" in model_ref:
        provider, model = model_ref.split("/", 1)
    providers = config.get("provider")
    endpoint = None
    if isinstance(providers, Mapping) and provider:
        entry = providers.get(provider)
        if isinstance(entry, Mapping):
            options = entry.get("options")
            if isinstance(options, Mapping):
                base = options.get("baseURL") or options.get("base_url")
                if isinstance(base, str):
                    endpoint = _normalize_endpoint(base)
            models = entry.get("models")
            if model is None and isinstance(models, Mapping) and len(models) == 1:
                model = next(iter(models))
    return {
        "runtime": "opencode",
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "binary": binary,
    }


def _binary_from_powershell_command(command: str) -> str | None:
    """Extract ``& 'runtime'`` / ``& \"runtime\"`` from a PowerShell wrapper script."""
    import re

    match = re.search(r"&\s+'([^']+)'", command)
    if match:
        return match.group(1)
    match = re.search(r'&\s+"([^"]+)"', command)
    if match:
        return match.group(1)
    return None


def _looks_like_powershell_wrapper(argv: list[str]) -> bool:
    joined = " ".join(argv).lower()
    return "powershell" in joined and "frombase64string" in joined


def _binding_from_process_argv(argv: list[str]) -> dict[str, str | None] | None:
    """Parse machine-readable OpenCode effective config from process argv."""
    config_raw = None
    # PowerShell wrapper argv ends with the script string, not the runtime binary.
    binary: str | None = None
    if argv and not _looks_like_powershell_wrapper(argv):
        binary = argv[-1]
    for item in argv:
        if item.startswith("OPENCODE_CONFIG_CONTENT="):
            config_raw = item.split("=", 1)[1]
            break
    if config_raw is None:
        # ``env KEY=VAL binary`` form produced by the POSIX bridge builder.
        for index, item in enumerate(argv):
            if item == "OPENCODE_CONFIG_CONTENT" and index + 1 < len(argv):
                config_raw = argv[index + 1]
                break
    if config_raw is None:
        # Windows PowerShell wrapper: FromBase64String('...'); & 'runtime'
        joined = " ".join(argv)
        marker = "FromBase64String('"
        if marker in joined:
            start = joined.index(marker) + len(marker)
            end = joined.find("')", start)
            if end > start:
                try:
                    config_raw = base64.b64decode(joined[start:end]).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    config_raw = None
            binary = _binary_from_powershell_command(joined)
    if not config_raw:
        return None
    try:
        config = json.loads(config_raw)
    except ValueError:
        return None
    if not isinstance(config, Mapping):
        return None
    return _binding_from_opencode_config(config, binary=binary if isinstance(binary, str) else None)


def _binding_from_effective(effective: Mapping[str, Any]) -> dict[str, str | None]:
    runtime = effective.get("agent") or effective.get("runtime")
    provider = effective.get("provider")
    model = effective.get("model")
    endpoint = effective.get("endpoint") or effective.get("baseURL") or effective.get("base_url")
    binary = effective.get("binary") or effective.get("executable")
    return {
        "runtime": runtime if isinstance(runtime, str) else None,
        "provider": provider if isinstance(provider, str) else None,
        "model": model if isinstance(model, str) else None,
        "endpoint": _normalize_endpoint(endpoint) if isinstance(endpoint, str) else None,
        "binary": binary if isinstance(binary, str) else None,
    }


def extract_attested_binding(evidence: Mapping[str, Any]) -> dict[str, str | None] | None:
    """Derive an exact binding from structured process/launch metadata only."""
    # Explicit attested binding object (tests / future Orca process metadata).
    for key in ("binding", "attested_binding"):
        raw = evidence.get(key)
        if isinstance(raw, Mapping):
            return {
                "runtime": raw.get("runtime") if isinstance(raw.get("runtime"), str) else None,
                "provider": raw.get("provider") if isinstance(raw.get("provider"), str) else None,
                "model": raw.get("model") if isinstance(raw.get("model"), str) else None,
                "endpoint": _normalize_endpoint(raw["endpoint"])
                if isinstance(raw.get("endpoint"), str)
                else None,
                "binary": raw.get("binary") if isinstance(raw.get("binary"), str) else None,
            }

    effective = evidence.get("effective")
    if isinstance(effective, Mapping):
        attested = _binding_from_effective(effective)
        if attested.get("runtime"):
            return attested

    process = evidence.get("process")
    if isinstance(process, Mapping):
        for key in ("effective", "effectiveConfig", "config", "binding"):
            cfg = process.get(key)
            if isinstance(cfg, Mapping):
                if key in {"effective", "binding"}:
                    attested = _binding_from_effective(cfg)
                else:
                    binary = process.get("executable") or process.get("binary")
                    if not isinstance(binary, str):
                        argv = _as_argv(process.get("argv"))
                        binary = argv[-1] if argv else None
                    attested = _binding_from_opencode_config(cfg, binary=binary)
                if attested.get("runtime") or attested.get("model"):
                    return attested
        argv = _as_argv(process.get("argv") or process.get("command") or process.get("commandLine"))
        if argv:
            from_argv = _binding_from_process_argv(argv)
            if from_argv is not None:
                return from_argv
    return None


def structured_binding_matches(target: ExecutionTarget, evidence: Mapping[str, Any]) -> bool:
    """Exact structural compare — never ``token in screen_text``."""
    attested = extract_attested_binding(evidence)
    if attested is None:
        return False
    expected = expected_binding(target)

    attested_runtime = attested.get("runtime")
    if attested_runtime is not None and attested_runtime != expected["runtime"]:
        return False
    if attested_runtime is None:
        if not (
            expected["binary"]
            and attested.get("binary")
            and attested["binary"] == expected["binary"]
        ):
            return False

    if expected["provider"] is not None:
        if attested.get("provider") != expected["provider"]:
            return False
    if expected["model"] is not None:
        if attested.get("model") != expected["model"]:
            return False
    if expected["endpoint"] is not None:
        if not _endpoints_equal(attested.get("endpoint"), expected["endpoint"]):
            return False
    if expected["binary"] and attested.get("binary") not in {None, expected["binary"]}:
        return False
    return True


# Product support is adapter data, never workflow routing.
_NATIVE_RUNTIMES = ("codex", "cursor", "claude", "gemini", "opencode")
_NATIVE_ADAPTERS: list[LaunchAdapter] = [
    *[NativeLaunch(name) for name in _NATIVE_RUNTIMES],
    *[NativeModelLaunch(name) for name in ("codex", "cursor", "claude")],
]
_EXISTING_TERMINAL = ExistingTerminalLaunch()
_TERMINAL_BRIDGE = TerminalBridgeLaunch()
LAUNCH_ADAPTERS: list[LaunchAdapter] = [
    *_NATIVE_ADAPTERS,
    _EXISTING_TERMINAL,
    _TERMINAL_BRIDGE,
]


def load_orca_schema(binary: str | None = None) -> dict[str, Any]:
    if binary is None:
        from aichestra.providers.orca import resolve_orca_binary

        binary = resolve_orca_binary()
    if not binary:
        return {}
    try:
        result = subprocess.run(
            [binary, "agent-context", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        schema = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return schema if isinstance(schema, dict) and schema.get("schemaVersion") == 1 else {}


def _attest_existing_terminal(
    target: ExecutionTarget,
    *,
    binary: str,
    handle: str,
    run: Callable[..., Any],
    schema: Mapping[str, Any],
) -> LaunchCapability | None:
    """Produce a proven existing-terminal capability from a live attested handle."""
    if not _EXISTING_TERMINAL.accepts(target):
        return None
    show_cmd = _command(schema, "terminal", "show")
    read_cmd = _command(schema, "terminal", "read")
    start = _command(schema, "orchestration", "worker-start")
    if not (
        show_cmd
        and read_cmd
        and {"task", "worktree", "terminal"} <= _flags(start)
    ):
        return None
    try:
        _EXISTING_TERMINAL.prepare(
            target,
            LaunchContext(binary=binary, terminal_handle=handle, run=run),
        )
    except ValueError:
        return None
    key = target.launch_binding_key
    return LaunchCapability(
        *key.as_tuple(),
        strategy=LaunchStrategy.ORCA_EXISTING_TERMINAL,
        proven=True,
        launch_ref=handle,
    )


def discover_launches(
    targets,
    *,
    binary: str | None = None,
    terminal_handle: str | None = None,
    run: Callable[..., Any] | None = None,
) -> tuple[LaunchCapability, ...]:
    """Discover launch capabilities.

    Native schema support → proven runnable.
    Explicit live terminal handle → attested existing-terminal when process matches.
    Terminal-bridge schema + builder → provisionable only (``proven=False``).
    """
    schema = load_orca_schema(binary)
    if not schema:
        return ()
    resolved_binary = binary
    if resolved_binary is None:
        from aichestra.providers.orca import resolve_orca_binary

        resolved_binary = resolve_orca_binary()
    handle = (terminal_handle or os.environ.get("ORCA_WORKER_TERMINAL_HANDLE") or "").strip()
    runner = run or subprocess.run
    launches = []
    for target in targets:
        if not all((target.enabled, target.available, target.capable, target.allowed)):
            continue
        key = target.launch_binding_key
        chosen: LaunchCapability | None = None
        for adapter in LAUNCH_ADAPTERS:
            if adapter.strategy in {
                LaunchStrategy.ORCA_EXISTING_TERMINAL,
                LaunchStrategy.ORCA_TERMINAL_BRIDGE,
            }:
                continue
            if adapter.prove(target, schema):
                chosen = LaunchCapability(
                    *key.as_tuple(),
                    strategy=adapter.strategy,
                    proven=True,
                )
                break
        if chosen is None and handle and resolved_binary:
            chosen = _attest_existing_terminal(
                target,
                binary=resolved_binary,
                handle=handle,
                run=runner,
                schema=schema,
            )
        if chosen is None:
            for adapter in LAUNCH_ADAPTERS:
                if (
                    adapter.strategy is LaunchStrategy.ORCA_TERMINAL_BRIDGE
                    and adapter.prove(target, schema)
                ):
                    # Schema+builder prove only that a bridge attempt is possible.
                    chosen = LaunchCapability(
                        *key.as_tuple(),
                        strategy=LaunchStrategy.ORCA_TERMINAL_BRIDGE,
                        proven=False,
                    )
                    break
        if chosen is not None:
            launches.append(chosen)
    return tuple(launches)


def adapter_for(target: ExecutionTarget) -> LaunchAdapter:
    if not target.preparable:
        raise ValueError("No preparable ExecutionTarget")
    for adapter in LAUNCH_ADAPTERS:
        if adapter.strategy == target.launch_strategy and adapter.accepts(target):
            return adapter
    raise ValueError("No launch adapter proves this exact ExecutionTarget binding")


def select_bootstrap(targets) -> ExecutionTarget | None:
    """Select one proven runnable bootstrap target.

    Provisionable bridge targets are NOT selectable here. Call
    ``prove_bootstrap_launch`` first so prepare/attest happens before Run
    creation and the resulting target is runnable.
    """
    candidates = [t for t in targets if t.runnable]
    return next(
        iter(sorted(candidates, key=lambda t: (not t.preferred, t.id))),
        None,
    )


def select_bootstrap_candidate(targets) -> ExecutionTarget | None:
    """Candidate for preflight prove: runnable preferred, else provisionable bridge."""
    proven = [t for t in targets if t.runnable]
    candidates = proven or [t for t in targets if t.provisionable]
    return next(
        iter(sorted(candidates, key=lambda t: (not t.preferred, t.id))),
        None,
    )


def mark_launch_proven(
    target: ExecutionTarget,
    *,
    launch_ref: str | None = None,
) -> ExecutionTarget:
    """Advertise a target as runnable after successful prepare/attest."""
    ref = (launch_ref or target.launch_ref or "").strip() or None
    return replace(target, launch_proven=True, launch_ref=ref)


def serialize_prepared_launch(prepared: PreparedLaunch) -> dict[str, Any]:
    return {
        "arguments": list(prepared.arguments),
        "terminal_handle": prepared.terminal_handle,
        "evidence": dict(prepared.evidence),
        "owns_terminal": prepared.owns_terminal,
    }


def deserialize_prepared_launch(payload: Mapping[str, Any]) -> PreparedLaunch:
    return PreparedLaunch(
        arguments=[str(item) for item in list(payload.get("arguments") or ())],
        terminal_handle=(
            str(payload["terminal_handle"]).strip()
            if isinstance(payload.get("terminal_handle"), str)
            and str(payload["terminal_handle"]).strip()
            else None
        ),
        evidence=dict(payload.get("evidence") or {}),
        owns_terminal=bool(payload.get("owns_terminal")),
    )


def prove_launch(
    target: ExecutionTarget,
    ctx: LaunchContext,
) -> tuple[ExecutionTarget, PreparedLaunch]:
    """Deterministic launch proof: candidate/target → proven runnable + handle.

    Used for Mode C bootstrap preflight and for promoting inner-worker
    ``execution_target_candidates``. On success the returned target is
    ``launch_proven`` / runnable / dispatchable. On failure owned bridge
    terminals are closed by prepare()/abort; callers must not create an Orca
    Run (bootstrap) or Dispatch (inner worker) until this succeeds.

    This is policy/binding verification — not worker selection or DAG building.
    Invoke via ``aichestra prove-launch`` (or ``prove_launch_by_candidate_id``);
    do not treat serialized candidate fields as a DIY recipe.
    """
    if not target.preparable:
        raise ValueError("No preparable ExecutionTarget for launch proof")

    if target.runnable:
        try:
            adapter = adapter_for(target)
            prepared = adapter.prepare(target, ctx)
        except ValueError:
            # Already-proven native/existing targets may use arbitrary agent ids
            # outside the built-in adapter table (tests / project agents).
            if target.launch_strategy is LaunchStrategy.ORCA_NATIVE:
                arguments = ["--agent", target.runtime.id]
                if (
                    target.model is not None
                    and target.provider is None
                    and target.endpoint is None
                ):
                    arguments.extend(["--model", target.model.id])
                prepared = PreparedLaunch(arguments=arguments)
            elif target.launch_strategy is LaunchStrategy.ORCA_EXISTING_TERMINAL:
                handle = (
                    (ctx.terminal_handle or "").strip()
                    or (target.launch_ref or "").strip()
                )
                if not handle:
                    raise ValueError("Existing-terminal launch requires terminal_handle")
                prepared = PreparedLaunch(
                    arguments=["--terminal", handle],
                    terminal_handle=handle,
                    owns_terminal=False,
                )
            else:
                raise
        return mark_launch_proven(target, launch_ref=prepared.terminal_handle), prepared

    # Provisionable bridge: must prepare/attest before Dispatch or Run create.
    adapter = adapter_for(target)
    prepared = adapter.prepare(target, ctx)
    if not structured_binding_matches(target, prepared.evidence):
        abort_prepared(prepared, ctx)
        raise ValueError("Launch proof did not prove the requested binding")
    return mark_launch_proven(target, launch_ref=prepared.terminal_handle), prepared


def prove_bootstrap_launch(
    target: ExecutionTarget,
    ctx: LaunchContext,
) -> tuple[ExecutionTarget, PreparedLaunch]:
    """Bootstrap alias for ``prove_launch`` (preflight before Orca Run create)."""
    return prove_launch(target, ctx)


def prove_launch_by_candidate_id(
    candidate_id: str,
    *,
    project_root: str | os.PathLike[str],
    repo_root: str | os.PathLike[str] | None = None,
    worktree: str = "current",
    binary: str | None = None,
    run: Callable[..., Any] | None = None,
    config: Mapping[str, Any] | None = None,
    targets: tuple[ExecutionTarget, ...] | None = None,
) -> dict[str, Any]:
    """Trusted production surface for ``aichestra.prove_launch`` / CLI.

    Coordinator supplies only ``candidate_id`` (+ project/worktree/repo-root).
    Aichestra reloads layered config, discovery, and binding from trusted
    state — never treats caller-supplied runtime/provider/model/command as
    authoritative. The returned payload is sanitized for coordinator stdout.
    """
    from pathlib import Path

    from aichestra.config.layering import resolve_config
    from aichestra.providers.orca import resolve_orca_binary
    from aichestra.repo import resolve_aichestra_config_root, trusted_config_root

    from .serialize import (
        LAUNCH_PROOF_OPERATION,
        resolve_mode_c_execution,
        serialize_execution_target,
    )

    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        return sanitize_mapping(data)

    cid = str(candidate_id or "").strip()
    if not cid:
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "error": "candidate_id is required",
        })

    root = Path(project_root).resolve()
    if not root.is_dir():
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "candidate_id": cid,
            "error": f"project_root is not a directory: {root}",
        })

    try:
        aichestra_root = resolve_aichestra_config_root(
            repo_root=Path(repo_root).resolve() if repo_root else None,
            project_root=root,
        )
    except ValueError as exc:
        aichestra_root = None
        config_root_error = str(exc)
    else:
        config_root_error = None

    if targets is None:
        if aichestra_root is None or not trusted_config_root(aichestra_root):
            return _payload({
                "ok": False,
                "operation": LAUNCH_PROOF_OPERATION,
                "candidate_id": cid,
                "error": config_root_error
                or (
                    "Aichestra config root required for prove-launch: pass "
                    "--repo-root or set AICHESTRA_REPO_ROOT to the Aichestra "
                    "config home; refusing an unrecognized directory "
                    "as trusted config root"
                ),
            })
        cfg = (
            dict(config)
            if config is not None
            else resolve_config(repo_root=aichestra_root, project_root=root)
        )
        targets, _policy, _facts = resolve_mode_c_execution(cfg)

    match = next((t for t in targets if t.id == cid), None)
    if match is None:
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "candidate_id": cid,
            "error": f"unknown candidate_id: {cid}",
        })
    if not match.preparable:
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "candidate_id": cid,
            "error": "candidate is not preparable",
            "execution_target": serialize_execution_target(match),
        })

    orca_binary = (binary or "").strip() or resolve_orca_binary() or ""
    if not orca_binary:
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "candidate_id": cid,
            "error": "Orca binary required for launch proof",
        })

    launch_ctx = LaunchContext(
        binary=orca_binary,
        worktree=str(worktree or "current").strip() or "current",
        terminal_handle=(
            str(os.environ.get("ORCA_WORKER_TERMINAL_HANDLE") or "").strip()
            or (match.launch_ref or "").strip()
            or None
        ),
        run=run if run is not None else subprocess.run,
    )
    try:
        proven, prepared = prove_launch(match, launch_ctx)
    except ValueError as exc:
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "candidate_id": cid,
            "error": str(exc),
        })

    try:
        prepared_payload = _coordinator_prepared_launch(
            prepared, repo_root=aichestra_root
        )
    except ValueError as exc:
        abort_prepared(prepared, launch_ctx)
        return _payload({
            "ok": False,
            "operation": LAUNCH_PROOF_OPERATION,
            "candidate_id": cid,
            "error": str(exc),
        })

    return _payload({
        "ok": True,
        "operation": LAUNCH_PROOF_OPERATION,
        "candidate_id": cid,
        "execution_target": serialize_execution_target(proven),
        "prepared_launch": prepared_payload,
    })


def _coordinator_prepared_launch(
    prepared: PreparedLaunch,
    *,
    repo_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Attestation for coordinator: handle + argv fragment; no DIY command/env."""
    from pathlib import Path

    from .cleanup_leases import issue_cleanup_lease
    from .serialize import LAUNCH_ABORT_OPERATION, abort_launch_invocation

    evidence = {
        key: value
        for key, value in dict(prepared.evidence).items()
        if key not in {"bridge_command", "env"}
    }
    process = evidence.get("process")
    if isinstance(process, Mapping) and "env" in process:
        evidence = {
            **evidence,
            "process": {k: v for k, v in process.items() if k != "env"},
        }
    handle = prepared.terminal_handle
    payload: dict[str, Any] = {
        "arguments": list(prepared.arguments),
        "terminal_handle": handle,
        "evidence": evidence,
        "owns_terminal": prepared.owns_terminal,
    }
    if prepared.owns_terminal and isinstance(handle, str) and handle.strip():
        if repo_root is None:
            raise ValueError(
                "Aichestra config root required to issue an owned-bridge cleanup lease"
            )
        root = Path(repo_root).resolve()
        lease = issue_cleanup_lease(handle.strip(), repo_root=root)
        payload["cleanup"] = {
            "operation": LAUNCH_ABORT_OPERATION,
            "required_if": "owns_terminal and not dispatched",
            "lease": lease,
            "invocation": abort_launch_invocation(token=lease, repo_root=str(root)),
        }
    return sanitize_mapping(payload)
