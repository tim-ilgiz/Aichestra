"""Orca launch contracts, separate from inference discovery and scheduling.

Native runtime-managed inference needs only an agent binding. Explicit backend
or endpoint bindings require an adapter that can prove those dimensions; the
native CLI's --model flag alone is insufficient. Never trust config as proof.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Protocol

from .domain import ExecutionTarget, LaunchCapability, LaunchStrategy


class LaunchAdapter(Protocol):
    strategy: LaunchStrategy

    def accepts(self, target: ExecutionTarget) -> bool: ...
    def prove(self, target: ExecutionTarget, schema: dict) -> bool: ...
    def arguments(self, target: ExecutionTarget) -> list[str]: ...
    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool: ...


@dataclass(frozen=True)
class NativeLaunch:
    """Version-matched worker-start contract, checked again on every launch."""
    runtime: str
    strategy: LaunchStrategy = LaunchStrategy.ORCA_NATIVE

    def accepts(self, target: ExecutionTarget) -> bool:
        return (target.runtime.id == self.runtime
                and target.provider is None and target.model is None
                and target.endpoint is None)

    def prove(self, target: ExecutionTarget, schema: dict) -> bool:
        commands = schema.get("commands")
        if not isinstance(commands, list):
            return False
        return (self.accepts(target)
                and any(isinstance(c, dict) and c.get("path") == ["orchestration", "worker-start"]
                        and {"agent", "task", "worktree"} <= set(c.get("flags", ()))
                        for c in commands))

    def arguments(self, target: ExecutionTarget) -> list[str]:
        if target.provider or target.model or target.endpoint:
            raise ValueError("Native launch cannot bind an explicit inference backend")
        return ["--agent", target.runtime.id]

    def confirms(self, target: ExecutionTarget, receipt: dict) -> bool:
        data = receipt.get("result", receipt)
        if not isinstance(data, dict) or not isinstance(data.get("launch"), dict):
            return False
        effective = data["launch"].get("effective")
        return (isinstance(effective, dict)
                and effective.get("agent") == target.runtime.id
                and target.provider is None and target.model is None
                and target.endpoint is None)


# Product support is adapter data, never workflow routing. New integrations may
# register native or attested terminal adapters without changing the controller.
LAUNCH_ADAPTERS: list[LaunchAdapter] = [
    NativeLaunch(name) for name in ("codex", "cursor", "claude", "gemini", "opencode")
]


def discover_launches(targets, *, binary: str | None = None) -> tuple[LaunchCapability, ...]:
    if binary is None:
        from aichestra.providers.orca import resolve_orca_binary
        binary = resolve_orca_binary()
    if not binary:
        return ()
    try:
        result = subprocess.run([binary, "agent-context", "--json"],
                                capture_output=True, text=True, timeout=15)
        schema = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ()
    if not isinstance(schema, dict) or schema.get("schemaVersion") != 1:
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
    # Recheck the exact binding contract, not a serialized runnable boolean.
    for adapter in LAUNCH_ADAPTERS:
        if adapter.strategy == target.launch_strategy and adapter.accepts(target):
            return adapter
    raise ValueError("No launch adapter proves this exact ExecutionTarget binding")


def select_bootstrap(targets) -> ExecutionTarget | None:
    """Select one bootstrap only; the coordinator selects every inner worker."""
    return next(iter(sorted((t for t in targets if t.runnable),
                            key=lambda t: (not t.preferred, t.id))), None)
