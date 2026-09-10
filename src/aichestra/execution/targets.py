"""Pure compatibility resolution. Does not launch or select inner workers."""

from __future__ import annotations

from dataclasses import replace

from .domain import (
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
    Model,
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

    Compatibility rows without an exact model expand to discovered models for
    that provider so "supports provider" is not mistaken for "no model forever".
    """
    runtimes = _index(facts.runtimes, lambda r: r.id)
    providers = _index(facts.providers, lambda p: p.id)
    models = _index(facts.models, lambda m: (m.provider, m.id))
    launches = _index(
        known_launches,
        lambda launch: launch.binding_key().as_tuple(),
    )
    targets: dict[str, ExecutionTarget] = {}
    for binding in expand_compatibility(compatibility, facts.models):
        runtime = runtimes.get(binding.runtime)
        provider = (
            providers.get(binding.provider) if binding.provider else None
        )
        if runtime is None or (
            binding.provider is not None and provider is None
        ):
            continue
        if binding.provider is None and binding.model is not None:
            # Opaque native --model preference; not a ModelProvider backend.
            model = Model(
                binding.model,
                provider=binding.runtime,
                available=True,
                enabled=True,
            )
        elif binding.model is not None:
            model = models.get((binding.provider, binding.model))
            if model is None:
                continue
        else:
            model = None
        endpoint = provider.endpoint if provider else None
        target_key = ExecutionTargetKey(
            runtime.id, binding.provider, binding.model
        )
        target_id = target_key.target_id()
        if target_id in targets:
            raise ValueError(f"Duplicate compatibility binding: {target_id}")
        locality = provider.locality if provider else runtime.locality
        capabilities = combine_capabilities(runtime.capabilities, model, binding)
        enabled = (
            binding.enabled
            and runtime.enabled
            and runtime.id not in policy.disabled_runtimes
            and (
                provider is None
                or (
                    provider.enabled
                    and provider.id not in policy.disabled_providers
                )
            )
            and (
                model is None
                or (
                    model.enabled
                    and (model.provider, model.id) not in policy.disabled_models
                )
            )
        )
        available = (
            runtime.available
            and (provider is None or provider.available)
            and (model is None or model.available)
        )
        reasons: list[str] = []
        if not capabilities.supports(policy.required):
            reasons.append("required execution capabilities missing")
        if not (
            model.capabilities if model else ExecutionCapabilities()
        ).supports(binding.required_model):
            reasons.append("required model capabilities missing")
        if not facts.machine.capabilities.supports(binding.required_machine):
            reasons.append("required machine capabilities missing")
        if binding.supported_os and facts.machine.os not in binding.supported_os:
            reasons.append("unsupported machine OS")
        if (
            model
            and locality == Locality.LOCAL
            and model.min_memory_bytes > facts.machine.memory_bytes
        ):
            reasons.append("insufficient local memory")
        allowed = locality in policy.allowed_localities and (
            policy.allowed_targets is None or target_id in policy.allowed_targets
        )
        launch_key = LaunchBindingKey(
            runtime.id, binding.provider, binding.model, endpoint
        )
        launch = launches.get(launch_key.as_tuple())
        targets[target_id] = ExecutionTarget(
            id=target_id,
            runtime=runtime,
            provider=provider,
            model=model,
            endpoint=endpoint,
            locality=locality,
            capabilities=capabilities,
            enabled=enabled,
            available=available,
            capable=not reasons,
            allowed=allowed,
            preferred=target_id in policy.preferred_targets,
            launch_strategy=(
                launch.strategy if launch else LaunchStrategy.UNSUPPORTED
            ),
            launch_proven=bool(launch.proven) if launch else False,
            reasons=tuple(reasons),
        )
    return list(targets.values())


def expand_compatibility(
    compatibility: tuple[Compatibility, ...],
    models: tuple[Model, ...],
) -> tuple[Compatibility, ...]:
    """Expand provider-only bindings across discovered models."""
    expanded: list[Compatibility] = []
    for binding in compatibility:
        if binding.model is not None or binding.provider is None:
            expanded.append(binding)
            continue
        provider_models = [
            model for model in models if model.provider == binding.provider
        ]
        if not provider_models:
            # Keep the unbound row so discovery can still show the pair; it is
            # not a permanent "no model" claim — models appear when probed.
            expanded.append(binding)
            continue
        for model in provider_models:
            expanded.append(replace(binding, model=model.id))
    return tuple(expanded)


def combine_capabilities(
    runtime_caps: ExecutionCapabilities,
    model: Model | None,
    binding: Compatibility,
) -> ExecutionCapabilities:
    """Runtime tools survive; model modalities require runtime support too."""
    caps = set(runtime_caps.names)
    if model is None and binding.provider is None:
        return ExecutionCapabilities(frozenset(caps))
    model_caps = model.capabilities.names if model else frozenset()
    modalities = binding.model_capabilities | binding.required_model.names
    # Drop modalities the runtime does not support; never invent from model.
    caps = (caps - modalities) | (caps & model_caps)
    return ExecutionCapabilities(frozenset(caps))


def _index(items, key):
    result = {}
    for item in items:
        identity = key(item)
        if identity in result:
            raise ValueError(f"Duplicate execution fact: {identity}")
        result[identity] = item
    return result
