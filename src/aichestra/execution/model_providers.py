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


class OpenAICompatibleProviderProbe:
    """Unauthenticated OpenAI-compatible ``/v1/models`` discovery (local backends).

    For LM Studio / local vLLM / similar. Aichestra MUST NOT attach Authorization
    headers or read API keys — authenticated cloud providers are Orca's concern.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        endpoint: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        cfg = dict(config or {})
        self._id = (provider_id or str(cfg.get("id") or "openai")).strip()
        raw = endpoint if endpoint is not None else cfg.get("endpoint")
        self._endpoint = str(raw).rstrip("/") if raw else None
        self._timeout = float(cfg.get("timeout_seconds") or 5)

    @property
    def id(self) -> str:
        return self._id

    @property
    def endpoint(self) -> str | None:
        return self._endpoint

    def is_reachable(self) -> bool:
        return self._get_models_payload() is not None

    def list_models(self) -> Sequence[DiscoveredModel]:
        payload = self._get_models_payload()
        if not isinstance(payload, Mapping):
            return ()
        data = payload.get("data")
        if not isinstance(data, list):
            return ()
        out: list[DiscoveredModel] = []
        for row in data:
            if not isinstance(row, Mapping):
                continue
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            out.append(DiscoveredModel(id=model_id, capabilities=frozenset({"text"})))
        return tuple(out)

    def _get_models_payload(self) -> Mapping[str, Any] | None:
        import json
        import urllib.error
        import urllib.request

        if not self._endpoint:
            return None
        url = f"{self._endpoint}/models"
        # Accept either .../v1 or .../v1/ already; callers usually pass .../v1.
        if not self._endpoint.endswith("/v1") and "/v1/" not in self._endpoint:
            url = f"{self._endpoint}/v1/models"
        headers = {"Accept": "application/json"}
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            return data if isinstance(data, Mapping) else None
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            return None


def openai_compatible_probe(
    provider_id: str,
    *,
    endpoint: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> ModelProviderProbe:
    return OpenAICompatibleProviderProbe(
        provider_id=provider_id, endpoint=endpoint, config=config
    )


def is_openai_compatible_entry(entry: Mapping[str, Any]) -> bool:
    style = str(entry.get("api_style") or entry.get("probe") or "").strip().lower()
    return style in {"openai", "openai_compatible", "openai-compatible"}


def default_provider_probe_registry() -> ProviderProbeRegistry:
    registry = ProviderProbeRegistry()
    registry.register("ollama", _ollama_factory)
    return registry


# Process-wide production registry; tests may register temporary factories.
PROVIDER_PROBE_REGISTRY = default_provider_probe_registry()


def register_provider_probe(
    provider_id: str, factory: ProviderProbeFactory
) -> None:
    """Extension point for local inference probes (LM Studio / vLLM / custom)."""
    PROVIDER_PROBE_REGISTRY.register(provider_id, factory)


def unregister_provider_probe(provider_id: str) -> None:
    PROVIDER_PROBE_REGISTRY.unregister(provider_id)
