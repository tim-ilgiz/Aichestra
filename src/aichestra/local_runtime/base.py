"""Extensible local inference runtime interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelCapability(str, Enum):
    TEXT = "text"
    VISION = "vision"
    CODE = "code"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class RuntimeCapabilities:
    can_list_models: bool = True
    can_unload_idle: bool = False
    supports_custom_endpoint: bool = False


@dataclass(frozen=True)
class LocalModel:
    id: str
    name: str
    runtime: str
    capabilities: frozenset[ModelCapability] = field(
        default_factory=lambda: frozenset({ModelCapability.TEXT})
    )
    parameter_size: str | None = None
    installed: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_capability(self, needed: ModelCapability | str) -> bool:
        key = ModelCapability(needed) if isinstance(needed, str) else needed
        return key in self.capabilities


class LocalRuntime(ABC):
    """Abstract local inference runtime (Ollama first; others pluggable)."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when the runtime binary/service is reachable."""

    @abstractmethod
    def list_models(self) -> list[LocalModel]:
        """Return installed models without downloading anything."""

    @abstractmethod
    def capabilities(self) -> RuntimeCapabilities:
        """Describe runtime feature support."""

    def unload_idle(self) -> bool:
        """Unload idle models when supported. Default: no-op."""
        return False

    def to_dict(self) -> dict[str, Any]:
        available = self.is_available()
        models = self.list_models() if available else []
        return {
            "name": self.name,
            "available": available,
            "capabilities": self.capabilities().__dict__,
            "models": [
                {
                    "id": m.id,
                    "name": m.name,
                    "runtime": m.runtime,
                    "capabilities": sorted(c.value for c in m.capabilities),
                    "parameter_size": m.parameter_size,
                    "installed": m.installed,
                }
                for m in models
            ],
        }
