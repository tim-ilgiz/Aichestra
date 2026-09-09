"""Runtime/provider-neutral discovery and resolution; no worker lifecycle."""

from .compatibility import legacy_local_compatibility, load_compatibility_bindings
from .discovery import discover_execution_facts, endpoint_locality, provider_locality
from .domain import (
    AgentRuntime,
    Compatibility,
    DiscoveryFacts,
    ExecutionCapabilities,
    ExecutionPolicy,
    ExecutionTarget,
    ExecutionTargetKey,
    LaunchBindingKey,
    LaunchCapability,
    LaunchStrategy,
    Locality,
    MachineCapabilities,
    Model,
    ModelProvider,
)
from .model_providers import (
    PROVIDER_PROBE_REGISTRY,
    DiscoveredModel,
    ModelProviderProbe,
    ProviderProbeRegistry,
    register_provider_probe,
    unregister_provider_probe,
)
from .targets import combine_capabilities, expand_compatibility, resolve_targets

__all__ = [
    "AgentRuntime",
    "Compatibility",
    "DiscoveredModel",
    "DiscoveryFacts",
    "ExecutionCapabilities",
    "ExecutionPolicy",
    "ExecutionTarget",
    "ExecutionTargetKey",
    "LaunchBindingKey",
    "LaunchCapability",
    "LaunchStrategy",
    "Locality",
    "MachineCapabilities",
    "Model",
    "ModelProvider",
    "ModelProviderProbe",
    "PROVIDER_PROBE_REGISTRY",
    "ProviderProbeRegistry",
    "combine_capabilities",
    "discover_execution_facts",
    "endpoint_locality",
    "expand_compatibility",
    "legacy_local_compatibility",
    "load_compatibility_bindings",
    "provider_locality",
    "register_provider_probe",
    "resolve_targets",
    "unregister_provider_probe",
]
