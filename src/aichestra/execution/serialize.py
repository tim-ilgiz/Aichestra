"""Bounded ExecutionTarget / ExecutionPolicy serialization for Mode C packages.

Canonical coordinator facts only — no secrets, credentials, or arbitrary
machine dumps. Does not launch workers or invent LaunchCapability proofs.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .compatibility import load_compatibility_bindings
from .discovery import discover_execution_facts
from .domain import (
    DiscoveryFacts,
    ExecutionCapabilities,
    ExecutionPolicy,
    ExecutionTarget,
    ExecutionTargetKey,
    LaunchCapability,
    Locality,
)
from .model_providers import ProviderProbeRegistry, ModelProviderProbe
from .targets import resolve_targets

# Coordinator package contract version for execution_targets / execution_policy.
EXECUTION_TARGET_CONTRACT_VERSION = 1

LEGACY_COMPATIBILITY_FIELDS: tuple[str, ...] = (
    "preferred_lead",
    "fallback_lead",
    "local_enabled",
    "local_endpoint",
    "local_model_ref",
    "local_capabilities",
    "installed_models",
    "provider_policy",
)

CANONICAL_EXECUTION_FIELDS: tuple[str, ...] = (
    "execution_targets",
    "execution_policy",
)

# Explicit Mode C ownership — not ambiguous ``orchestration_owner: orca``.
OWNERSHIP_METADATA: dict[str, str] = {
    "workflow_dag_owner": "coordinator_under_orca",
    "canonical_lifecycle_owner": "orca",
    "inner_worker_selection_owner": "coordinator_under_orca",
    "aichestra_role": "policy_context_gates",
}

_REDACTED_ENDPOINT = "[REDACTED_ENDPOINT]"


def safe_endpoint_for_context(endpoint: str | None) -> str | None:
    """Return a coordinator-safe endpoint representation (never mutate internals).

    Keeps ordinary loopback URLs useful. Drops URL userinfo, *all* query
    parameters, and the fragment by construction — no credential-key denylist.
    Malformed or suspicious values fail closed. Internal exact endpoints
    (ExecutionTarget.endpoint / LaunchBindingKey) remain unchanged.
    """
    if endpoint is None:
        return None
    if not isinstance(endpoint, str):
        return _REDACTED_ENDPOINT
    text = endpoint.strip()
    if not text:
        return None

    # Bare host:port / opaque tokens without a URL shape — fail closed if
    # credential-like markers appear; otherwise pass through for discovery ids.
    if "://" not in text and not text.startswith("//"):
        lowered = text.lower()
        if "@" in text or any(
            marker in lowered
            for marker in ("password", "token", "secret", "api_key", "apikey")
        ):
            return _REDACTED_ENDPOINT
        return text

    try:
        parts = urlsplit(text)
    except ValueError:
        return _REDACTED_ENDPOINT

    if not parts.scheme or not parts.netloc:
        return _REDACTED_ENDPOINT

    hostname = parts.hostname
    if hostname is None:
        return _REDACTED_ENDPOINT

    # Drop userinfo entirely (never expose alice:secret).
    try:
        port = parts.port
    except ValueError:
        return _REDACTED_ENDPOINT
    host_part = hostname
    if ":" in hostname and not hostname.startswith("["):
        host_part = f"[{hostname}]"
    netloc = host_part
    if port is not None:
        netloc = f"{host_part}:{port}"

    # Query-safe by construction: scheme://host[:port]/path only.
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def serialize_execution_target(target: ExecutionTarget) -> dict[str, Any]:
    """Neutral bounded representation for the generic coordinator."""
    return {
        "id": target.id,
        "runtime": target.runtime.id,
        "provider": target.provider.id if target.provider else None,
        "model": target.model.id if target.model else None,
        "endpoint": safe_endpoint_for_context(target.endpoint),
        "locality": target.locality.value,
        "capabilities": sorted(target.capabilities.names),
        "enabled": target.enabled,
        "available": target.available,
        "capable": target.capable,
        "allowed": target.allowed,
        "preferred": target.preferred,
        "launch_strategy": target.launch_strategy.value,
        "runnable": target.runnable,
        "reasons": list(target.reasons),
    }


def serialize_execution_policy(policy: ExecutionPolicy) -> dict[str, Any]:
    return {
        "required_capabilities": sorted(policy.required.names),
        "disabled_runtimes": sorted(policy.disabled_runtimes),
        "disabled_providers": sorted(policy.disabled_providers),
        "disabled_models": sorted(
            [list(pair) for pair in policy.disabled_models],
            key=lambda item: (item[0], item[1]),
        ),
        "allowed_targets": (
            None
            if policy.allowed_targets is None
            else sorted(policy.allowed_targets)
        ),
        "preferred_targets": sorted(policy.preferred_targets),
        "allowed_localities": sorted(loc.value for loc in policy.allowed_localities),
    }


def build_execution_policy(config: Mapping[str, Any]) -> ExecutionPolicy:
    """Build ExecutionPolicy from layered config (not product phase routing)."""
    execution = config.get("execution", {})
    raw = execution.get("policy", {}) if isinstance(execution, Mapping) else {}
    if not isinstance(raw, Mapping):
        raw = {}

    required = ExecutionCapabilities(
        frozenset(str(c) for c in (raw.get("required_capabilities") or ()))
    )
    disabled_runtimes = frozenset(
        str(r) for r in (raw.get("disabled_runtimes") or ())
    )
    disabled_providers = frozenset(
        str(p) for p in (raw.get("disabled_providers") or ())
    )
    disabled_models: set[tuple[str, str]] = set()
    for entry in raw.get("disabled_models") or ():
        pair = _model_pair(entry)
        if pair is not None:
            disabled_models.add(pair)

    allowed_raw = raw.get("allowed_targets", None)
    allowed_targets: frozenset[str] | None
    if allowed_raw is None:
        allowed_targets = None
    else:
        allowed_targets = frozenset(
            tid for tid in (_target_ref_id(item) for item in allowed_raw) if tid
        )

    preferred_targets = frozenset(
        tid
        for tid in (
            _target_ref_id(item) for item in (raw.get("preferred_targets") or ())
        )
        if tid
    )

    localities_raw = raw.get("allowed_localities")
    if localities_raw is None:
        allowed_localities = frozenset(Locality)
    else:
        allowed_localities = frozenset(Locality(str(loc)) for loc in localities_raw)

    return ExecutionPolicy(
        required=required,
        disabled_runtimes=disabled_runtimes,
        disabled_providers=disabled_providers,
        disabled_models=frozenset(disabled_models),
        allowed_targets=allowed_targets,
        preferred_targets=preferred_targets,
        allowed_localities=allowed_localities,
    )


def resolve_mode_c_execution(
    config: Mapping[str, Any],
    *,
    known_launches: tuple[LaunchCapability, ...] = (),
    machine=None,
    runtime_binaries: Mapping[str, tuple[str, ...]] | None = None,
    provider_registry: ProviderProbeRegistry | None = None,
    provider_probes: list[ModelProviderProbe] | None = None,
) -> tuple[tuple[ExecutionTarget, ...], ExecutionPolicy, DiscoveryFacts]:
    """Production Mode C pipeline: facts → bindings → policy → targets.

    ``known_launches`` defaults empty. T172 MUST NOT synthesize LaunchCapability;
    unproven targets stay ``launch_strategy=unsupported`` / ``runnable=false``.
    """
    facts = discover_execution_facts(
        config,
        machine=machine,
        runtime_binaries=runtime_binaries,
        provider_registry=provider_registry,
        provider_probes=provider_probes,
    )
    bindings = load_compatibility_bindings(config)
    policy = build_execution_policy(config)
    targets = resolve_targets(
        facts,
        bindings,
        policy,
        known_launches=known_launches,
    )
    return tuple(targets), policy, facts


def _target_ref_id(ref: Any) -> str | None:
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    if not isinstance(ref, Mapping):
        return None
    runtime = ref.get("runtime")
    if not isinstance(runtime, str) or not runtime.strip():
        return None
    provider = ref.get("provider")
    model = ref.get("model")
    if provider is not None and (
        not isinstance(provider, str) or not provider.strip()
    ):
        return None
    if model is not None and (not isinstance(model, str) or not model.strip()):
        return None
    return ExecutionTargetKey(
        runtime.strip(),
        provider.strip() if isinstance(provider, str) else None,
        model.strip() if isinstance(model, str) else None,
    ).target_id()


def _model_pair(entry: Any) -> tuple[str, str] | None:
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        provider, model = entry
        if isinstance(provider, str) and isinstance(model, str):
            return (provider, model)
    if isinstance(entry, Mapping):
        provider = entry.get("provider")
        model = entry.get("model")
        if isinstance(provider, str) and isinstance(model, str):
            return (provider, model)
    return None
