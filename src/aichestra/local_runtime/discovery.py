"""Discover available local inference runtimes."""

from __future__ import annotations

from typing import Any

from aichestra.local_runtime.base import LocalRuntime
from aichestra.local_runtime.ollama import OllamaRuntime


def discover_local_runtimes(
    *,
    ollama_host: str | None = None,
    include_unavailable: bool = True,
) -> list[LocalRuntime]:
    """Return runtime adapters. Ollama is the initial implementation."""
    runtimes: list[LocalRuntime] = [OllamaRuntime(host=ollama_host)]
    if include_unavailable:
        return runtimes
    return [r for r in runtimes if r.is_available()]


def discover_local_runtime_report(
    *,
    ollama_host: str | None = None,
    local_enabled: bool = False,
) -> dict[str, Any]:
    """Structured discovery report for doctor / CLI."""
    runtimes = discover_local_runtimes(ollama_host=ollama_host)
    return {
        "local_enabled": local_enabled,
        "runtimes": [r.to_dict() for r in runtimes],
        "any_available": any(r.is_available() for r in runtimes),
        "note": (
            "local.enabled=false remains valid; discovery is informational only"
            if not local_enabled
            else "local inference enabled in config"
        ),
    }
