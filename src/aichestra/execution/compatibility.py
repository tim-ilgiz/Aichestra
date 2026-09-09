"""Provider/runtime-neutral compatibility binding source.

Bindings come from config (and legacy local-worker translation). The resolver
never contains product-specific branches.
"""

from __future__ import annotations

from typing import Any, Mapping

from .domain import Compatibility, ExecutionCapabilities


def load_compatibility_bindings(
    config: Mapping[str, Any],
) -> tuple[Compatibility, ...]:
    """Load explicit bindings; invalid/incomplete rows are skipped fail-closed.

    A binding without ``model`` means ``runtime`` supports ``provider`` and the
    resolver expands discovered models. It does not mean "no model forever".

    If any explicit ``execution.bindings`` entry exists for the same
    ``(runtime, provider)`` pair as a legacy compatibility binding, the
    explicit binding(s) supersede that legacy pair completely — including when
    the explicit row is a model wildcard and legacy carries a concrete model.
    Unrelated explicit pairs do not suppress unrelated legacy pairs.
    """
    execution = config.get("execution", {})
    raw = execution.get("bindings", ())
    bindings: list[Compatibility] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    explicit_pairs: set[tuple[str, str | None]] = set()
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            binding = _binding_from_entry(entry)
            if binding is None:
                continue
            key = (binding.runtime, binding.provider, binding.model)
            if key in seen:
                raise ValueError(f"Duplicate compatibility binding: {key}")
            seen.add(key)
            explicit_pairs.add((binding.runtime, binding.provider))
            bindings.append(binding)
    for legacy in legacy_local_compatibility(config):
        if (legacy.runtime, legacy.provider) in explicit_pairs:
            # Explicit runtime/provider pair supersedes the legacy seam.
            continue
        key = (legacy.runtime, legacy.provider, legacy.model)
        if key in seen:
            continue
        seen.add(key)
        bindings.append(legacy)
    return tuple(bindings)


def legacy_local_compatibility(config: Mapping[str, Any]) -> tuple[Compatibility, ...]:
    """Translate historical OpenCode/Ollama seam, not a canonical worker type.

    Flags affect only this compatibility binding, never all OpenCode targets
    (which could use cloud inference) or all local inference providers.
    """
    local = config.get("local", {})
    legacy = config.get("providers", {}).get(
        "local_worker",
        config.get("providers", {}).get("local-worker", {}),
    )
    flag = (
        legacy.get("enabled", True)
        if isinstance(legacy, dict)
        else legacy is not False
    )
    return (
        Compatibility(
            runtime="opencode",
            provider="ollama",
            model=local.get("model"),
            enabled=bool(local.get("enabled", False) and flag),
            required_model=ExecutionCapabilities(frozenset({"code"})),
        ),
    )


def _binding_from_entry(entry: Any) -> Compatibility | None:
    if not isinstance(entry, Mapping):
        return None
    runtime = entry.get("runtime")
    if not isinstance(runtime, str) or not runtime.strip():
        return None
    provider = entry.get("provider")
    if provider is not None and (
        not isinstance(provider, str) or not provider.strip()
    ):
        return None
    model = entry.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        return None
    required_model = ExecutionCapabilities(
        frozenset(entry.get("required_model_capabilities", ()))
    )
    required_machine = ExecutionCapabilities(
        frozenset(entry.get("required_machine_capabilities", ()))
    )
    supported_os = frozenset(entry.get("supported_os", ()))
    model_capabilities = entry.get("model_capabilities")
    kwargs: dict[str, Any] = {
        "runtime": runtime.strip(),
        "provider": provider.strip() if isinstance(provider, str) else None,
        "model": model.strip() if isinstance(model, str) else None,
        "enabled": bool(entry.get("enabled", True)),
        "required_model": required_model,
        "required_machine": required_machine,
        "supported_os": supported_os,
    }
    if model_capabilities is not None:
        kwargs["model_capabilities"] = frozenset(model_capabilities)
    return Compatibility(**kwargs)
