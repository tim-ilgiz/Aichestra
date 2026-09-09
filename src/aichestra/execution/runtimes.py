"""Agent Runtime discovery helpers and conservative capability sources."""

from __future__ import annotations

from typing import Any, Mapping

from aichestra.providers.base import which_binary

from .domain import AgentRuntime, ExecutionCapabilities, Locality

# Built-in agent binaries. Product ids are data, not resolver branches.
DEFAULT_RUNTIME_BINARIES: dict[str, tuple[str, ...]] = {
    "codex": ("codex",),
    "cursor": ("cursor-agent",),
    "opencode": ("opencode",),
}

# Conservative static capabilities for well-known agent processes. Only facts
# that are safe without proving provider/model selection belong here. Model-
# dependent modalities (e.g. vision) must come from proven runtime/session or
# provider+model intersection later — never optimistic static defaults.
# Config / probe facts may override or extend these.
TRACKED_RUNTIME_CAPABILITY_DEFAULTS: dict[str, frozenset[str]] = {
    "codex": frozenset({"code_edit", "repository_read", "shell"}),
    "cursor": frozenset({"code_edit", "repository_read", "shell"}),
    "opencode": frozenset({"code_edit", "repository_read", "shell"}),
}


def runtime_capabilities_for(
    runtime_id: str,
    entry: Mapping[str, Any] | None = None,
) -> ExecutionCapabilities:
    """Merge tracked defaults with optional config/probe capability facts."""
    names = set(TRACKED_RUNTIME_CAPABILITY_DEFAULTS.get(runtime_id, ()))
    if entry and "capabilities" in entry:
        # Explicit config replaces tracked defaults when provided, so unknown
        # or restricted deployments can narrow the advertisement.
        names = set(entry.get("capabilities") or ())
    return ExecutionCapabilities(frozenset(names))


def discover_agent_runtimes(
    config: Mapping[str, Any],
    *,
    runtime_binaries: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[AgentRuntime, ...]:
    execution = config.get("execution", {})
    runtime_config = execution.get("runtimes", {})
    if not isinstance(runtime_config, Mapping):
        runtime_config = {}
    binaries = dict(
        DEFAULT_RUNTIME_BINARIES if runtime_binaries is None else runtime_binaries
    )
    for name, entry in runtime_config.items():
        if isinstance(entry, Mapping):
            binaries.setdefault(name, tuple(entry.get("binaries", ())))
    runtimes: list[AgentRuntime] = []
    for name, commands in binaries.items():
        entry = runtime_config.get(name, {})
        if not isinstance(entry, Mapping):
            entry = {}
        path = which_binary(commands) if commands else None
        legacy = config.get("providers", {}).get(name, {})
        legacy_enabled = (
            legacy.get("enabled", True) if isinstance(legacy, dict) else legacy
        )
        locality = _runtime_locality(entry)
        runtimes.append(
            AgentRuntime(
                id=name,
                available=path is not None,
                enabled=entry.get("enabled", bool(legacy_enabled)),
                binary_path=path,
                capabilities=runtime_capabilities_for(name, entry),
                locality=locality,
            )
        )
    return tuple(runtimes)


def _runtime_locality(entry: Mapping[str, Any]) -> Locality:
    if "locality" in entry:
        return Locality(entry["locality"])
    return Locality.CLOUD
