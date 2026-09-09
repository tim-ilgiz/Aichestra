"""Pure compatibility resolution. Does not launch or select inner workers."""

import json

from .domain import (
    Compatibility, DiscoveryFacts, ExecutionCapabilities, ExecutionPolicy,
    ExecutionTarget, LaunchCapability, LaunchStrategy, Locality,
)


def resolve_targets(
    facts: DiscoveryFacts,
    compatibility: tuple[Compatibility, ...],
    policy: ExecutionPolicy = ExecutionPolicy(),
    known_launches: tuple[LaunchCapability, ...] = (),
) -> list[ExecutionTarget]:
    """Return inspectable candidates; only ``runnable`` targets may execute.

    Unknown references/incompatible combinations yield no target. Disabled,
    unavailable and incapable dependencies remain visible with separate states.
    Launch inputs are trusted exact adapter bindings, never prompt metadata.
    """
    runtimes = _index(facts.runtimes, lambda r: r.id)
    providers = _index(facts.providers, lambda p: p.id)
    models = _index(facts.models, lambda m: (m.provider, m.id))
    launches = _index(known_launches, lambda l: (
        l.runtime, l.provider, l.model, l.endpoint))
    targets = {}
    for binding in compatibility:
        runtime = runtimes.get(binding.runtime)
        provider = providers.get(binding.provider) if binding.provider else None
        model = models.get((binding.provider, binding.model)) if binding.model else None
        if runtime is None or (binding.provider is not None and provider is None):
            continue
        if binding.model is not None and model is None:
            continue
        endpoint = provider.endpoint if provider else None
        key = (runtime.id, binding.provider, binding.model, endpoint)
        target_id = json.dumps(key, separators=(",", ":"), ensure_ascii=True)
        if target_id in targets:
            raise ValueError(f"Duplicate compatibility binding: {target_id}")
        locality = provider.locality if provider else runtime.locality
        caps = runtime.capabilities.names
        # Runtime tool capabilities cannot manufacture model modalities.
        if provider:
            model_caps = model.capabilities.names if model else frozenset()
            inference = binding.model_capabilities | binding.required_model.names
            caps = (caps - inference) | (caps & model_caps)
        capabilities = ExecutionCapabilities(frozenset(caps))
        enabled = (
            binding.enabled and runtime.enabled
            and runtime.id not in policy.disabled_runtimes
            and (provider is None or (provider.enabled and
                 provider.id not in policy.disabled_providers))
            and (model is None or (model.enabled and
                 (model.provider, model.id) not in policy.disabled_models))
        )
        available = runtime.available and (provider is None or provider.available) and (
            model is None or model.available)
        reasons = []
        if not capabilities.supports(policy.required):
            reasons.append("required execution capabilities missing")
        if not (model.capabilities if model else ExecutionCapabilities()).supports(
            binding.required_model
        ):
            reasons.append("required model capabilities missing")
        if not facts.machine.capabilities.supports(binding.required_machine):
            reasons.append("required machine capabilities missing")
        if binding.supported_os and facts.machine.os not in binding.supported_os:
            reasons.append("unsupported machine OS")
        if (model and locality == Locality.LOCAL
                and model.min_memory_bytes > facts.machine.memory_bytes):
            reasons.append("insufficient local memory")
        allowed = locality in policy.allowed_localities and (
            policy.allowed_targets is None or target_id in policy.allowed_targets)
        launch = launches.get(key)
        targets[target_id] = ExecutionTarget(
            id=target_id, runtime=runtime, provider=provider, model=model,
            endpoint=endpoint, locality=locality, capabilities=capabilities,
            enabled=enabled, available=available, capable=not reasons,
            allowed=allowed, preferred=target_id in policy.preferred_targets,
            launch_strategy=launch.strategy if launch else LaunchStrategy.UNSUPPORTED,
            reasons=tuple(reasons),
        )
    return list(targets.values())


def _index(items, key):
    result = {}
    for item in items:
        identity = key(item)
        if identity in result:
            raise ValueError(f"Duplicate execution fact: {identity}")
        result[identity] = item
    return result
