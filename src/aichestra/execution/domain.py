"""Canonical execution facts. Product names are identifiers, never enum cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum


class Locality(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CLOUD = "cloud"


class LaunchStrategy(str, Enum):
    ORCA_NATIVE = "orca-native"
    ORCA_EXISTING_TERMINAL = "orca-existing-terminal"
    ORCA_TERMINAL_BRIDGE = "orca-terminal-bridge"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ExecutionCapabilities:
    """Positive facts only; unknown capabilities are not advertised."""

    names: frozenset[str] = frozenset()

    def supports(self, required: "ExecutionCapabilities") -> bool:
        return required.names <= self.names


@dataclass(frozen=True)
class ExecutionTargetKey:
    """Stable target identity; endpoint changes do not change this key."""

    runtime: str
    provider: str | None = None
    model: str | None = None

    def as_tuple(self) -> tuple[str, str | None, str | None]:
        return (self.runtime, self.provider, self.model)

    def target_id(self) -> str:
        return _stable_id(self.as_tuple())


@dataclass(frozen=True)
class LaunchBindingKey:
    """Exact launch/proof identity including endpoint when applicable."""

    runtime: str
    provider: str | None = None
    model: str | None = None
    endpoint: str | None = None

    def as_tuple(self) -> tuple[str, str | None, str | None, str | None]:
        return (self.runtime, self.provider, self.model, self.endpoint)

    def binding_id(self) -> str:
        return _stable_id(self.as_tuple())

    def target_key(self) -> ExecutionTargetKey:
        return ExecutionTargetKey(self.runtime, self.provider, self.model)


def _stable_id(parts: tuple[object, ...]) -> str:
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class AgentRuntime:
    id: str
    available: bool = False
    enabled: bool = True
    binary_path: str | None = None
    capabilities: ExecutionCapabilities = ExecutionCapabilities()
    locality: Locality = Locality.CLOUD


@dataclass(frozen=True)
class ModelProvider:
    id: str
    available: bool = False
    enabled: bool = True
    configured: bool = False
    endpoint: str | None = None
    locality: Locality = Locality.CLOUD


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    available: bool = False
    enabled: bool = True
    capabilities: ExecutionCapabilities = ExecutionCapabilities()
    min_memory_bytes: int = 0


@dataclass(frozen=True)
class MachineCapabilities:
    os: str = ""
    memory_bytes: int = 0
    capabilities: ExecutionCapabilities = ExecutionCapabilities()


@dataclass(frozen=True)
class ExecutionPolicy:
    """Already-layered user/project constraints, not phase/worker selection."""

    required: ExecutionCapabilities = ExecutionCapabilities()
    disabled_runtimes: frozenset[str] = frozenset()
    disabled_providers: frozenset[str] = frozenset()
    disabled_models: frozenset[tuple[str, str]] = frozenset()
    allowed_targets: frozenset[str] | None = None
    preferred_targets: frozenset[str] = frozenset()
    allowed_localities: frozenset[Locality] = frozenset(Locality)


@dataclass(frozen=True)
class Compatibility:
    """Explicit supported combination; absence means incompatible.

    Provider-less bindings represent runtimes managing their own inference.
    A binding with provider but without model means the runtime supports that
    provider; the resolver expands discovered models. Model capabilities are
    intersected with runtime support, never unioned.
    """

    runtime: str
    provider: str | None = None
    model: str | None = None
    enabled: bool = True
    required_model: ExecutionCapabilities = ExecutionCapabilities()
    required_machine: ExecutionCapabilities = ExecutionCapabilities()
    supported_os: frozenset[str] = frozenset()
    model_capabilities: frozenset[str] = frozenset({
        "text", "code", "vision", "embedding", "long_context",
    })


@dataclass(frozen=True)
class LaunchCapability:
    """Trusted adapter input for an exact binding, NOT discovery evidence.

    T169–T171 supply no production launch capabilities. Future launch adapters
    must verify the Orca-supervised process before supplying this input.
    """

    runtime: str
    provider: str | None = None
    model: str | None = None
    endpoint: str | None = None
    strategy: LaunchStrategy = LaunchStrategy.UNSUPPORTED

    def binding_key(self) -> LaunchBindingKey:
        return LaunchBindingKey(
            self.runtime, self.provider, self.model, self.endpoint
        )

    def target_key(self) -> ExecutionTargetKey:
        return self.binding_key().target_key()


@dataclass(frozen=True)
class ExecutionTarget:
    id: str
    runtime: AgentRuntime
    provider: ModelProvider | None
    model: Model | None
    endpoint: str | None
    locality: Locality
    capabilities: ExecutionCapabilities
    enabled: bool
    available: bool
    capable: bool
    allowed: bool
    preferred: bool
    launch_strategy: LaunchStrategy = LaunchStrategy.UNSUPPORTED
    reasons: tuple[str, ...] = ()

    @property
    def key(self) -> ExecutionTargetKey:
        return ExecutionTargetKey(
            self.runtime.id,
            self.provider.id if self.provider else None,
            self.model.id if self.model else None,
        )

    @property
    def launch_binding_key(self) -> LaunchBindingKey:
        return LaunchBindingKey(
            self.runtime.id,
            self.provider.id if self.provider else None,
            self.model.id if self.model else None,
            self.endpoint,
        )

    @property
    def launchable(self) -> bool:
        return self.launch_strategy != LaunchStrategy.UNSUPPORTED

    @property
    def runnable(self) -> bool:
        return all((self.enabled, self.available, self.capable,
                    self.allowed, self.launchable))


@dataclass(frozen=True)
class DiscoveryFacts:
    runtimes: tuple[AgentRuntime, ...] = ()
    providers: tuple[ModelProvider, ...] = ()
    models: tuple[Model, ...] = ()
    machine: MachineCapabilities = MachineCapabilities()
