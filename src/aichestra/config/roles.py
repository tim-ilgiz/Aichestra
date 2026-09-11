"""Project role bindings: runtime (+ optional provider/model) per work role.

Worker roles live under ``roles.*``. The Mode C coordinator LLM is
``orchestration.coordinator`` and MUST NOT be read from ``roles.implement``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ROLE_KEYS: tuple[str, ...] = ("implement", "research", "tests", "docs")
QUOTA_ROLE_KEYS: tuple[str, ...] = ("implement",)

# Product ids are identifiers, not enum cases for dispatch logic — validation set.
from aichestra.execution.runtimes import DEFAULT_RUNTIME_BINARIES

KNOWN_RUNTIMES = frozenset(DEFAULT_RUNTIME_BINARIES)


def configured_runtimes(config, *, extra_known=None):
    """Runtime ids accepted for role/quota bindings.

    ``extra_known`` unions layered (machine/global) registrations so project.json
    may bind a runtime registered outside the project file.
    """
    known = set(KNOWN_RUNTIMES)
    known |= set((config or {}).get("execution", {}).get("runtimes", {}) or {})
    if extra_known:
        known |= set(extra_known)
    return known



_RUNTIME_ALIASES: dict[str, str] = {
    "local": "opencode",
    "local-worker": "opencode",
    "local_worker": "opencode",
}


@dataclass(frozen=True)
class RoleBinding:
    runtime: str
    provider: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"runtime": self.runtime}
        if self.provider:
            out["provider"] = self.provider
        if self.model:
            out["model"] = self.model
        return out


@dataclass(frozen=True)
class QuotaPolicy:
    mode: str  # manual | auto
    role_fallbacks: dict[str, RoleBinding]

    @property
    def implement_fallback(self) -> RoleBinding:
        """Coding-worker fallback. Not a coordinator replacement."""
        return self.role_fallbacks["implement"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "roles": {role: binding.to_dict() for role, binding in self.role_fallbacks.items()},
        }


def normalize_runtime_id(raw: str, *, known=KNOWN_RUNTIMES) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        raise ValueError("runtime id must be non-empty")
    text = _RUNTIME_ALIASES.get(text, text)
    if text not in known:
        raise ValueError(
            f"unknown runtime {raw!r}; expected one of {sorted(known)} "
            f"or aliases {sorted(_RUNTIME_ALIASES)}"
        )
    return text


def parse_role_binding(raw: Any, *, field: str = "role", known=KNOWN_RUNTIMES) -> RoleBinding:
    """Accept string runtime or object {runtime, provider?, model?}."""
    if isinstance(raw, str):
        return RoleBinding(runtime=normalize_runtime_id(raw, known=known))
    if isinstance(raw, Mapping):
        runtime = raw.get("runtime") or raw.get("agent")
        if runtime is None or not str(runtime).strip():
            raise ValueError(f"{field} object requires 'runtime'")
        provider = raw.get("provider")
        model = raw.get("model") or raw.get("model_ref")
        return RoleBinding(
            runtime=normalize_runtime_id(str(runtime), known=known),
            provider=str(provider).strip() if provider else None,
            model=str(model).strip() if model else None,
        )
    raise ValueError(f"{field} must be a runtime string or object")


def default_role_bindings() -> dict[str, RoleBinding]:
    return {
        "implement": RoleBinding(runtime="codex"),
        "research": RoleBinding(runtime="cursor"),
        "tests": RoleBinding(runtime="cursor"),
        "docs": RoleBinding(runtime="cursor"),
    }


def default_coordinator_binding() -> RoleBinding:
    return RoleBinding(runtime="codex")


def default_quota_policy() -> QuotaPolicy:
    return QuotaPolicy(
        mode="manual",
        role_fallbacks={"implement": RoleBinding(runtime="cursor")},
    )


def load_role_bindings(
    config: Mapping[str, Any] | None,
    *,
    known=None,
) -> dict[str, RoleBinding]:
    base = default_role_bindings()
    if not config:
        return base
    raw_roles = config.get("roles")
    if not isinstance(raw_roles, Mapping):
        return base
    if "coordinator" in raw_roles:
        raise ValueError(
            "roles.coordinator is invalid; the Mode C coordinator LLM is "
            "orchestration.coordinator. roles.* are worker roles only"
        )
    unknown = [key for key in raw_roles if key not in ROLE_KEYS]
    if unknown:
        raise ValueError(
            f"unknown worker role(s) {unknown}; expected {list(ROLE_KEYS)}. "
            "Use orchestration.coordinator for the Mode C coordinator LLM"
        )
    out = dict(base)
    effective = known if known is not None else configured_runtimes(config)
    for key in ROLE_KEYS:
        if key in raw_roles:
            out[key] = parse_role_binding(
                raw_roles[key], field=f"roles.{key}", known=effective
            )
    return out


def load_coordinator_binding(
    config: Mapping[str, Any] | None,
    *,
    known=None,
) -> RoleBinding:
    base = default_coordinator_binding()
    if not config:
        return base
    raw = config.get("orchestration")
    if not isinstance(raw, Mapping) or "coordinator" not in raw:
        return base
    effective = known if known is not None else configured_runtimes(config)
    return parse_role_binding(
        raw["coordinator"],
        field="orchestration.coordinator",
        known=effective,
    )


def load_quota_policy(
    config: Mapping[str, Any] | None,
    *,
    known=None,
) -> QuotaPolicy:
    base = default_quota_policy()
    if not config:
        return base
    raw = config.get("quota")
    if not isinstance(raw, Mapping):
        return base
    mode = str(raw.get("mode") or base.mode).strip().lower()
    if mode not in {"manual", "auto"}:
        raise ValueError("quota.mode must be 'manual' or 'auto'")
    effective = known if known is not None else configured_runtimes(config)
    fallbacks = dict(base.role_fallbacks)
    roles_raw = raw.get("roles")
    # Precedence: quota.roles.implement → legacy implement_fallback → default.
    # An empty roles object must not suppress the legacy alias.
    if isinstance(roles_raw, Mapping):
        unknown = [key for key in roles_raw if key not in QUOTA_ROLE_KEYS]
        if unknown:
            raise ValueError(
                f"unknown quota role(s) {unknown}; quota auto/manual currently "
                f"applies to {list(QUOTA_ROLE_KEYS)} (coding workers), not the coordinator"
            )
        for key in QUOTA_ROLE_KEYS:
            if key in roles_raw:
                fallbacks[key] = parse_role_binding(
                    roles_raw[key], field=f"quota.roles.{key}", known=effective
                )
            elif key == "implement" and "implement_fallback" in raw:
                fallbacks[key] = parse_role_binding(
                    raw["implement_fallback"],
                    field="quota.implement_fallback",
                    known=effective,
                )
    elif "implement_fallback" in raw:
        fallbacks["implement"] = parse_role_binding(
            raw["implement_fallback"], field="quota.implement_fallback", known=effective
        )
    return QuotaPolicy(mode=mode, role_fallbacks=fallbacks)


def role_bindings_to_dict(bindings: Mapping[str, RoleBinding]) -> dict[str, Any]:
    return {key: bindings[key].to_dict() for key in ROLE_KEYS if key in bindings}


def coordinator_binding_to_dict(binding: RoleBinding) -> dict[str, Any]:
    return binding.to_dict()


def binding_runtime_disabled(
    binding: RoleBinding,
    *,
    disabled_runtimes: frozenset[str] | set[str],
) -> bool:
    return binding.runtime in disabled_runtimes
