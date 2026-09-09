"""Adapters for existing machine, binary and inference discovery components.

Configuration here is already layered by config.layering. It declares facts,
not launch proof. No agent execution, model downloads or worker construction.
"""

from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import urlsplit

from aichestra.local_runtime.discovery import discover_local_runtimes
from aichestra.machine_profiler import MachineProfile, profile_machine
from aichestra.providers.base import which_binary

from .domain import (
    AgentRuntime, Compatibility, DiscoveryFacts, ExecutionCapabilities,
    Locality, MachineCapabilities, Model, ModelProvider,
)


DEFAULT_RUNTIME_BINARIES = {
    "codex": ("codex",), "cursor": ("cursor-agent",), "opencode": ("opencode",),
}


def machine_facts(profile: MachineProfile) -> MachineCapabilities:
    caps = {g.accelerator_kind for g in profile.gpus if g.accelerator_kind}
    if profile.gpus:
        caps.add("gpu")
    return MachineCapabilities(profile.os, profile.memory.total_bytes,
                               ExecutionCapabilities(frozenset(caps)))


def discover_execution_facts(
    config: Mapping[str, Any], *, machine: MachineProfile | None = None,
    runtime_binaries: Mapping[str, tuple[str, ...]] | None = None,
    inference_adapters=None,
) -> DiscoveryFacts:
    """Independent probes; injectable adapter registry supports future products.

    ``execution.runtimes`` and ``execution.model_providers`` contain arbitrary
    identifiers, enabled flags and declared capabilities. Provider availability
    requires a probe; ``configured`` alone does not claim reachability.
    Configured models may declare availability (e.g. a remote model catalogue).
    """
    execution = config.get("execution", {})
    runtime_config = execution.get("runtimes", {})
    binaries = dict(DEFAULT_RUNTIME_BINARIES if runtime_binaries is None else runtime_binaries)
    for name, entry in runtime_config.items():
        binaries.setdefault(name, tuple(entry.get("binaries", ())))
    runtimes = []
    for name, commands in binaries.items():
        entry = runtime_config.get(name, {})
        path = which_binary(commands)
        legacy = config.get("providers", {}).get(name, {})
        legacy_enabled = legacy.get("enabled", True) if isinstance(legacy, dict) else legacy
        runtimes.append(AgentRuntime(
            id=name, available=path is not None,
            enabled=entry.get("enabled", bool(legacy_enabled)), binary_path=path,
            capabilities=_caps(entry), locality=Locality(entry.get("locality", "cloud")),
        ))
    provider_config = execution.get("model_providers", {})
    local = config.get("local", {})
    host = (provider_config.get("ollama", {}).get("endpoint")
            or local.get("ollama_host") or local.get("endpoint"))
    adapters = (discover_local_runtimes(ollama_host=host)
                if inference_adapters is None else inference_adapters)
    providers = {}
    models = {}
    for adapter in adapters:
        entry = provider_config.get(adapter.name, {})
        endpoint = entry.get("endpoint", getattr(adapter, "host", None))
        # Never probe the adapter's old endpoint while reporting a configured one.
        same_endpoint = endpoint == getattr(adapter, "host", endpoint)
        reachable = getattr(adapter, "is_reachable", adapter.is_available)
        available = same_endpoint and reachable()
        providers[adapter.name] = ModelProvider(
            adapter.name, available=available, enabled=entry.get("enabled", True),
            configured=bool(endpoint), endpoint=endpoint,
            locality=Locality(entry.get("locality", _endpoint_locality(endpoint))),
        )
        for model in adapter.list_models() if available else ():
            models[(adapter.name, model.id)] = Model(
                model.id, adapter.name, available=model.installed,
                capabilities=ExecutionCapabilities(frozenset(c.value for c in model.capabilities)),
            )
    for name, entry in provider_config.items():
        if name not in providers:
            providers[name] = ModelProvider(
                name, enabled=entry.get("enabled", True),
                configured=entry.get("configured", bool(entry.get("endpoint"))),
                endpoint=entry.get("endpoint"),
                locality=Locality(entry.get("locality", "cloud")),
            )
        for model_id, data in entry.get("models", {}).items():
            old = models.get((name, model_id), Model(model_id, name))
            models[(name, model_id)] = replace(
                old, enabled=data.get("enabled", old.enabled),
                available=data.get("available", old.available),
                capabilities=_caps(data) if "capabilities" in data else old.capabilities,
                min_memory_bytes=data.get("min_memory_bytes", old.min_memory_bytes),
            )
    return DiscoveryFacts(tuple(runtimes), tuple(providers.values()), tuple(models.values()),
                          machine_facts(machine if machine is not None else profile_machine()))


def legacy_local_compatibility(config: Mapping[str, Any]) -> tuple[Compatibility, ...]:
    """Translate the historical OpenCode/Ollama seam, not a canonical worker.

    Flags affect only this compatibility binding, never all OpenCode targets
    (which could use cloud inference) or all local inference providers.
    """
    local = config.get("local", {})
    legacy = config.get("providers", {}).get("local_worker",
             config.get("providers", {}).get("local-worker", {}))
    flag = legacy.get("enabled", True) if isinstance(legacy, dict) else legacy is not False
    return (Compatibility(
        runtime="opencode", provider="ollama", model=local.get("model"),
        enabled=bool(local.get("enabled", False) and flag),
        required_model=ExecutionCapabilities(frozenset({"code"})),
    ),)


def _caps(entry) -> ExecutionCapabilities:
    return ExecutionCapabilities(frozenset(entry.get("capabilities", ())))


def _endpoint_locality(endpoint: str | None) -> Locality:
    host = urlsplit(endpoint or "").hostname
    return Locality.LOCAL if host in {"localhost", "127.0.0.1", "::1"} else Locality.REMOTE
