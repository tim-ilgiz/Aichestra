"""Adapters for machine, Agent Runtime and Model Provider discovery.

Configuration is already layered by config.layering. It declares facts, not
launch proof. No agent execution, model downloads or worker construction.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import urlsplit

from aichestra.machine_profiler import MachineProfile, profile_machine

from .domain import (
    DiscoveryFacts,
    ExecutionCapabilities,
    Locality,
    MachineCapabilities,
    Model,
    ModelProvider,
)
from .model_providers import (
    PROVIDER_PROBE_REGISTRY,
    ModelProviderProbe,
    ProviderProbeRegistry,
    is_openai_compatible_entry,
    openai_compatible_probe,
)
from .runtimes import discover_agent_runtimes


def machine_facts(profile: MachineProfile) -> MachineCapabilities:
    caps = {g.accelerator_kind for g in profile.gpus if g.accelerator_kind}
    if profile.gpus:
        caps.add("gpu")
    return MachineCapabilities(
        profile.os,
        profile.memory.total_bytes,
        ExecutionCapabilities(frozenset(caps)),
    )


def discover_execution_facts(
    config: Mapping[str, Any],
    *,
    machine: MachineProfile | None = None,
    runtime_binaries: Mapping[str, tuple[str, ...]] | None = None,
    provider_registry: ProviderProbeRegistry | None = None,
    provider_probes: list[ModelProviderProbe] | None = None,
) -> DiscoveryFacts:
    """Independent probes via the production provider registry.

    ``execution.runtimes`` / ``execution.model_providers`` hold arbitrary ids.
    Availability requires a real probe; ``configured`` alone is not reachability.
    """
    execution = config.get("execution", {})
    provider_config = execution.get("model_providers", {})
    if not isinstance(provider_config, Mapping):
        provider_config = {}
    local = config.get("local", {})
    ollama_host = (
        provider_config.get("ollama", {}).get("endpoint")
        if isinstance(provider_config.get("ollama"), Mapping)
        else None
    ) or local.get("ollama_host") or local.get("endpoint")
    registry = provider_registry or PROVIDER_PROBE_REGISTRY
    if provider_probes is None:
        probes = registry.build_probes(
            provider_config,
            default_endpoints={"ollama": ollama_host},
        )
        # Config-declared OpenAI-compatible backends without a named factory.
        seen = {p.id for p in probes}
        for name, entry in provider_config.items():
            if name in seen or not isinstance(entry, Mapping):
                continue
            if not is_openai_compatible_entry(entry):
                continue
            endpoint = entry.get("endpoint")
            probes.append(
                openai_compatible_probe(
                    name,
                    endpoint=str(endpoint) if endpoint else None,
                    config=entry,
                )
            )
    else:
        probes = list(provider_probes)

    providers: dict[str, ModelProvider] = {}
    models: dict[tuple[str, str], Model] = {}
    for probe in probes:
        entry = provider_config.get(probe.id, {})
        if not isinstance(entry, Mapping):
            entry = {}
        endpoint = entry.get("endpoint", probe.endpoint)
        same_endpoint = endpoint == probe.endpoint
        available = bool(same_endpoint and probe.is_reachable())
        providers[probe.id] = ModelProvider(
            probe.id,
            available=available,
            enabled=entry.get("enabled", True),
            configured=bool(endpoint) or probe.id in provider_config,
            endpoint=endpoint,
            locality=provider_locality(entry, endpoint),
        )
        if available:
            for discovered in probe.list_models():
                models[(probe.id, discovered.id)] = Model(
                    discovered.id,
                    probe.id,
                    available=discovered.installed,
                    capabilities=ExecutionCapabilities(
                        frozenset(discovered.capabilities)
                    ),
                )

    for name, entry in provider_config.items():
        if not isinstance(entry, Mapping):
            continue
        endpoint = entry.get("endpoint")
        if name not in providers:
            providers[name] = ModelProvider(
                name,
                enabled=entry.get("enabled", True),
                configured=entry.get(
                    "configured", bool(endpoint) or bool(entry)
                ),
                endpoint=endpoint,
                locality=provider_locality(entry, endpoint),
            )
        else:
            providers[name] = replace(
                providers[name],
                locality=provider_locality(entry, providers[name].endpoint),
            )
        for model_id, data in entry.get("models", {}).items():
            if not isinstance(data, Mapping):
                continue
            old = models.get((name, model_id), Model(model_id, name))
            models[(name, model_id)] = replace(
                old,
                enabled=data.get("enabled", old.enabled),
                available=data.get("available", old.available),
                capabilities=(
                    _caps(data) if "capabilities" in data else old.capabilities
                ),
                min_memory_bytes=data.get(
                    "min_memory_bytes", old.min_memory_bytes
                ),
            )

    return DiscoveryFacts(
        discover_agent_runtimes(config, runtime_binaries=runtime_binaries),
        tuple(providers.values()),
        tuple(models.values()),
        machine_facts(machine if machine is not None else profile_machine()),
    )


def provider_locality(
    entry: Mapping[str, Any], endpoint: str | None
) -> Locality:
    """Explicit locality wins; otherwise infer from endpoint when present."""
    if "locality" in entry:
        return Locality(entry["locality"])
    if endpoint:
        return endpoint_locality(endpoint)
    return Locality.CLOUD


def endpoint_locality(endpoint: str) -> Locality:
    host = urlsplit(endpoint).hostname
    if host in {"localhost", "127.0.0.1", "::1"}:
        return Locality.LOCAL
    return Locality.REMOTE


def _caps(entry: Mapping[str, Any]) -> ExecutionCapabilities:
    return ExecutionCapabilities(frozenset(entry.get("capabilities", ())))


# Back-compat re-export for callers that imported legacy helper from discovery.
from .compatibility import legacy_local_compatibility  # noqa: E402
