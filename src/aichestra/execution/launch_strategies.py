"""Orca launch contracts, separate from inference discovery and scheduling.

Prefer native worker-start. Use an existing terminal or a bounded terminal bridge
only when native launch cannot express the required dimensions. Schema support
selects a strategy; every dispatch must still confirm the effective binding.
Never treat prompt text, ambient discovery, or Aichestra-process env as proof.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

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
    return endpoint.rstrip("/")


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


class LaunchAdapter(Protocol):
    strategy: LaunchStrategy

    def accepts(self, target: ExecutionTarget) -> bool: ...
    def prove(self, target: ExecutionTarget, schema: dict) -> bool: ...
    def arguments(self, target: ExecutionTarget) -> list[str]: ...
    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool: ...
    def prepare(self, target: ExecutionTarget, ctx: LaunchContext) -> PreparedLaunch: ...


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
        if not _terminal_evidence_matches(target, evidence):
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
        evidence = {**_merge_terminal_evidence(show, read), "handle": handle}
        if not _terminal_evidence_matches(target, evidence):
            raise ValueError("Existing terminal does not prove the requested binding")
        return PreparedLaunch(
            arguments=["--terminal", handle],
            terminal_handle=handle,
            evidence=evidence,
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
        if not _terminal_evidence_matches(target, evidence):
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
            # Still attempt read-based proof; some agents never report tui-idle.
            pass
        read = ctx.run(
            [ctx.binary, "terminal", "read", "--terminal", handle, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        evidence = {
            **_merge_terminal_evidence(create, read),
            "handle": handle,
            "bridge_command": bridge.command,
            "proof_tokens": list(bridge.proof_tokens),
        }
        if not _terminal_evidence_matches(target, evidence):
            raise ValueError("Terminal bridge did not prove the requested binding")
        return PreparedLaunch(
            arguments=["--terminal", handle],
            terminal_handle=handle,
            evidence=evidence,
        )


@dataclass(frozen=True)
class BridgeCommand:
    command: str
    proof_tokens: tuple[str, ...]


def bridge_command_for(target: ExecutionTarget) -> BridgeCommand | None:
    """Runtime-specific argv data for an Orca-owned terminal; not workflow routing."""
    builder = BRIDGE_COMMAND_BUILDERS.get(target.runtime.id)
    if builder is None:
        return None
    return builder(target)


def _opencode_bridge_command(target: ExecutionTarget) -> BridgeCommand | None:
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
    # Binding travels in the Orca-supervised process command, not Aichestra env.
    command = (
        f"env OPENCODE_CONFIG_CONTENT={shlex.quote(json.dumps(config, separators=(',', ':')))} "
        f"{shlex.quote(binary)}"
    )
    return BridgeCommand(
        command=command,
        proof_tokens=(model_ref, base, binary, "OPENCODE_CONFIG_CONTENT"),
    )


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


def _merge_terminal_evidence(*results: Any) -> dict[str, Any]:
    text_parts: list[str] = []
    handle = None
    for result in results:
        payload = _json_payload(result)
        handle = handle or _dig_handle(payload)
        data = payload.get("result", payload)
        if not isinstance(data, dict):
            continue
        terminal = data.get("terminal", data)
        if not isinstance(terminal, dict):
            continue
        for key in ("preview", "title", "command", "startupCommand"):
            value = terminal.get(key)
            if isinstance(value, str):
                text_parts.append(value)
        tail = terminal.get("tail")
        if isinstance(tail, list):
            text_parts.extend(str(item) for item in tail)
    return {"handle": handle, "text": "\n".join(text_parts)}


def _terminal_evidence_matches(target: ExecutionTarget, evidence: Mapping[str, Any]) -> bool:
    text = str(evidence.get("text") or "")
    tokens = list(evidence.get("proof_tokens") or ())
    if not tokens:
        tokens = [target.runtime.id]
        if target.model is not None:
            tokens.append(target.model.id)
            if target.provider is not None:
                tokens.append(f"{target.provider.id}/{target.model.id}")
        if target.endpoint:
            tokens.append(_normalize_endpoint(target.endpoint) or target.endpoint)
            if not str(tokens[-1]).endswith("/v1"):
                tokens.append(f"{_normalize_endpoint(target.endpoint)}/v1")
    if target.runtime.binary_path:
        tokens.append(target.runtime.binary_path)
    return all(token and token in text for token in tokens)


# Product support is adapter data, never workflow routing.
_NATIVE_RUNTIMES = ("codex", "cursor", "claude", "gemini", "opencode")
LAUNCH_ADAPTERS: list[LaunchAdapter] = [
    *[NativeLaunch(name) for name in _NATIVE_RUNTIMES],
    *[NativeModelLaunch(name) for name in ("codex", "cursor", "claude")],
    ExistingTerminalLaunch(),
    TerminalBridgeLaunch(),
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


def discover_launches(targets, *, binary: str | None = None) -> tuple[LaunchCapability, ...]:
    schema = load_orca_schema(binary)
    if not schema:
        return ()
    launches = []
    for target in targets:
        if not all((target.enabled, target.available, target.capable, target.allowed)):
            continue
        for adapter in LAUNCH_ADAPTERS:
            if adapter.prove(target, schema):
                key = target.launch_binding_key
                launches.append(LaunchCapability(*key.as_tuple(), strategy=adapter.strategy))
                break
    return tuple(launches)


def adapter_for(target: ExecutionTarget) -> LaunchAdapter:
    if not target.runnable:
        raise ValueError("No runnable bootstrap ExecutionTarget")
    for adapter in LAUNCH_ADAPTERS:
        if adapter.strategy == target.launch_strategy and adapter.accepts(target):
            return adapter
    raise ValueError("No launch adapter proves this exact ExecutionTarget binding")


def select_bootstrap(targets) -> ExecutionTarget | None:
    """Select one bootstrap only; the coordinator selects every inner worker."""
    return next(
        iter(
            sorted(
                (t for t in targets if t.runnable),
                key=lambda t: (not t.preferred, t.id),
            )
        ),
        None,
    )
