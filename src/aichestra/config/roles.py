"""Project role bindings: runtime (+ optional provider/model) per work role."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ROLE_KEYS: tuple[str, ...] = ("implement", "research", "tests", "docs")

# Product ids are identifiers, not enum cases for dispatch logic — validation set.
KNOWN_RUNTIMES: frozenset[str] = frozenset(
    {"codex", "cursor", "gemini", "claude", "opencode"}
)

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
    implement_fallback: RoleBinding

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "implement_fallback": self.implement_fallback.to_dict(),
        }


def normalize_runtime_id(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        raise ValueError("runtime id must be non-empty")
    text = _RUNTIME_ALIASES.get(text, text)
    if text not in KNOWN_RUNTIMES:
        raise ValueError(
            f"unknown runtime {raw!r}; expected one of {sorted(KNOWN_RUNTIMES)} "
            f"or aliases {sorted(_RUNTIME_ALIASES)}"
        )
    return text


def parse_role_binding(raw: Any, *, field: str = "role") -> RoleBinding:
    """Accept string runtime or object {runtime, provider?, model?}."""
    if isinstance(raw, str):
        return RoleBinding(runtime=normalize_runtime_id(raw))
    if isinstance(raw, Mapping):
        runtime = raw.get("runtime") or raw.get("agent")
        if runtime is None or not str(runtime).strip():
            raise ValueError(f"{field} object requires 'runtime'")
        provider = raw.get("provider")
        model = raw.get("model") or raw.get("model_ref")
        return RoleBinding(
            runtime=normalize_runtime_id(str(runtime)),
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


def default_quota_policy() -> QuotaPolicy:
    return QuotaPolicy(
        mode="manual",
        implement_fallback=RoleBinding(runtime="cursor"),
    )


def load_role_bindings(config: Mapping[str, Any] | None) -> dict[str, RoleBinding]:
    base = default_role_bindings()
    if not config:
        return base
    raw_roles = config.get("roles")
    if not isinstance(raw_roles, Mapping):
        return base
    out = dict(base)
    for key in ROLE_KEYS:
        if key in raw_roles:
            out[key] = parse_role_binding(raw_roles[key], field=f"roles.{key}")
    return out


def load_quota_policy(config: Mapping[str, Any] | None) -> QuotaPolicy:
    base = default_quota_policy()
    if not config:
        return base
    raw = config.get("quota")
    if not isinstance(raw, Mapping):
        return base
    mode = str(raw.get("mode") or base.mode).strip().lower()
    if mode not in {"manual", "auto"}:
        raise ValueError("quota.mode must be 'manual' or 'auto'")
    fb_raw = raw.get("implement_fallback", base.implement_fallback.to_dict())
    fallback = parse_role_binding(fb_raw, field="quota.implement_fallback")
    return QuotaPolicy(mode=mode, implement_fallback=fallback)


def role_bindings_to_dict(bindings: Mapping[str, RoleBinding]) -> dict[str, Any]:
    return {key: bindings[key].to_dict() for key in ROLE_KEYS if key in bindings}


def binding_runtime_disabled(
    binding: RoleBinding,
    *,
    disabled_runtimes: frozenset[str] | set[str],
) -> bool:
    return binding.runtime in disabled_runtimes
