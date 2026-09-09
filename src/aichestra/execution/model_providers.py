"""Generic Model Provider probe/discovery contract.

Adapters register factories; ``resolve_targets`` and Mode C never branch on
product names. Configured providers without a probe stay ``available=false``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from aichestra.local_runtime.ollama import OllamaRuntime


@dataclass(frozen=True)
class DiscoveredModel:
    """Provider-reported model fact; not an ExecutionTarget."""

    id: str
    capabilities: frozenset[str] = frozenset()
    installed: bool = True


class ModelProviderProbe(Protocol):
    """Minimal reachability + optional model discovery for one provider id."""

    @property
    def id(self) -> str:
        ...

    @property
    def endpoint(self) -> str | None:
        ...

    def is_reachable(self) -> bool:
        """Real probe or proven source; never claim from config alone."""
        ...

    def list_models(self) -> Sequence[DiscoveredModel]:
        """Return models when the probe supports listing; else empty."""
        ...


ProviderProbeFactory = Callable[..., ModelProviderProbe]


class ProviderProbeRegistry:
    """Production-facing factory registry for arbitrary inference backends."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderProbeFactory] = {}

    def register(self, provider_id: str, factory: ProviderProbeFactory) -> None:
        key = provider_id.strip()
        if not key:
            raise ValueError("provider probe id must be non-empty")
        self._factories[key] = factory

    def unregister(self, provider_id: str) -> None:
        self._factories.pop(provider_id, None)

    def registered(self) -> frozenset[str]:
        return frozenset(self._factories)

    def create(
        self,
        provider_id: str,
        *,
        endpoint: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> ModelProviderProbe:
        factory = self._factories.get(provider_id)
        if factory is None:
            raise KeyError(f"No provider probe registered for {provider_id!r}")
        return factory(endpoint=endpoint, config=dict(config or {}))

    def build_probes(
        self,
        provider_config: Mapping[str, Any],
        *,
        default_endpoints: Mapping[str, str | None] | None = None,
    ) -> list[ModelProviderProbe]:
        """Instantiate probes for registered ids that are configured or defaulted.

        Unregistered configured providers are omitted here; discovery records
        them as configured/unavailable without a probe.
        """
        defaults = dict(default_endpoints or {})
        ids = set(self._factories) | set(provider_config) | set(defaults)
        probes: list[ModelProviderProbe] = []
        for provider_id in sorted(ids):
            if provider_id not in self._factories:
                continue
            entry = provider_config.get(provider_id, {})
            if not isinstance(entry, Mapping):
                entry = {}
            endpoint = entry.get("endpoint", defaults.get(provider_id))
            # Skip default-only probes that are neither configured nor defaulted
            # with an endpoint intent — still include registered defaults like
            # ollama when they appear in defaults map.
            if (
                provider_id not in provider_config
                and provider_id not in defaults
            ):
                continue
            probes.append(
                self.create(provider_id, endpoint=endpoint, config=entry)
            )
        return probes


class OllamaProviderProbe:
    """Ollama through the generic probe contract (not a special resolver case)."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        _ = config
        self._runtime = OllamaRuntime(host=endpoint)

    @property
    def id(self) -> str:
        return "ollama"

    @property
    def endpoint(self) -> str | None:
        return self._runtime.host

    def is_reachable(self) -> bool:
        return self._runtime.is_reachable()

    def list_models(self) -> Sequence[DiscoveredModel]:
        return tuple(
            DiscoveredModel(
                id=model.id,
                capabilities=frozenset(c.value for c in model.capabilities),
                installed=model.installed,
            )
            for model in self._runtime.list_models()
        )


def _ollama_factory(
    *,
    endpoint: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> ModelProviderProbe:
    return OllamaProviderProbe(endpoint=endpoint, config=config)


def default_provider_probe_registry() -> ProviderProbeRegistry:
    registry = ProviderProbeRegistry()
    registry.register("ollama", _ollama_factory)
    return registry


# Process-wide production registry; tests may register temporary factories.
PROVIDER_PROBE_REGISTRY = default_provider_probe_registry()


def register_provider_probe(
    provider_id: str, factory: ProviderProbeFactory
) -> None:
    """Public extension point for LM Studio / vLLM / OpenRouter / custom."""
    PROVIDER_PROBE_REGISTRY.register(provider_id, factory)


def unregister_provider_probe(provider_id: str) -> None:
    PROVIDER_PROBE_REGISTRY.unregister(provider_id)
