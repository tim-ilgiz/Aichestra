"""Configuration layering: tracked → OS → machine-local → project → runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from aichestra.platform_detect import detect_os
from aichestra.repo import find_repo_root

MACHINE_LOCAL_FILENAME = "machine.local.json"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return data


def machine_local_path(repo_root: Path | None = None) -> Path:
    root = repo_root or find_repo_root()
    return root / ".local" / MACHINE_LOCAL_FILENAME


def resolve_config(
    *,
    repo_root: Path | None = None,
    project_root: Path | None = None,
    runtime_override: dict[str, Any] | None = None,
    machine_local: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve layered configuration without requiring fixed home paths."""
    root = repo_root or find_repo_root()
    tracked = _load_tracked_defaults(root)
    os_layer = _load_os_defaults(root)
    local = (
        machine_local
        if machine_local is not None
        else load_json(machine_local_path(root))
    )
    project: dict[str, Any] = {}
    if project_root is not None:
        project = load_json(Path(project_root) / ".aichestra" / "project.json")
    layers = [tracked, os_layer, local, project]
    if runtime_override:
        layers.append(runtime_override)
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = deep_merge(merged, layer)
    return merged


def _load_tracked_defaults(root: Path) -> dict[str, Any]:
    json_path = root / "policies" / "defaults.json"
    if json_path.is_file():
        return load_json(json_path)
    return {
        "local": {"enabled": False},
        "providers": {
            "preferred_lead": "codex",
            "fallback_lead": "cursor",
        },
        "orchestration": {"mode_default": "native"},
    }


def _load_os_defaults(root: Path) -> dict[str, Any]:
    path = root / "policies" / f"defaults.{detect_os().value}.json"
    return load_json(path)


def save_machine_local(data: dict[str, Any], repo_root: Path | None = None) -> Path:
    path = machine_local_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def local_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = config if config is not None else resolve_config()
    return bool(cfg.get("local", {}).get("enabled", False))


def provider_enabled(config: dict[str, Any] | None, kind: str) -> bool:
    """Whether a named provider is enabled in layered config (default True).

    Local worker uses ``local.enabled`` as the canonical switch.
    """
    cfg = config if config is not None else resolve_config()
    key = kind.strip().lower().replace("_", "-")
    if key in {"local", "local-worker", "local_worker"}:
        return local_enabled(cfg)
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    entry = providers.get(key) or providers.get(key.replace("-", "_"))
    if isinstance(entry, dict) and "enabled" in entry:
        return bool(entry["enabled"])
    if isinstance(entry, bool):
        return entry
    return True


def preferred_lead_name(config: dict[str, Any] | None = None) -> str:
    cfg = config if config is not None else resolve_config()
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    raw = providers.get("preferred_lead") or "codex"
    return str(raw).strip().lower() or "codex"


def fallback_lead_name(config: dict[str, Any] | None = None) -> str:
    cfg = config if config is not None else resolve_config()
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    raw = providers.get("fallback_lead") or "cursor"
    return str(raw).strip().lower() or "cursor"


def apply_provider_enable_overrides(
    config: dict[str, Any],
    *,
    no_orca: bool = False,
    no_codex: bool = False,
    no_cursor: bool = False,
    no_local: bool = False,
) -> dict[str, Any]:
    """Return a deep-copied config with CLI disable flags applied as runtime override.

    Invocation flags have highest precedence for canonical ExecutionTargets:

    * ``--no-codex`` / ``--no-cursor`` — legacy ``providers.*.enabled=false``
      plus union into ``execution.policy.disabled_runtimes`` so explicit
      ``execution.runtimes.<id>.enabled=true`` cannot re-enable the runtime
      for this invocation (CLI override wins; existing disabled_runtimes preserved).
    * ``--no-local`` — legacy ``local.enabled=false`` plus strip ``local`` from
      ``execution.policy.allowed_localities`` so *all* locality=local targets
      are forbidden (product/provider-agnostic; remote/cloud unaffected).
    """
    override: dict[str, Any] = {"providers": {}, "local": {}}
    policy_override: dict[str, Any] = {}
    if no_orca:
        override["providers"]["orca"] = {"enabled": False}
    if no_codex:
        override["providers"]["codex"] = {"enabled": False}
        policy_override["disabled_runtimes"] = _union_disabled_runtimes(
            config, "codex"
        )
    if no_cursor:
        override["providers"]["cursor"] = {"enabled": False}
        policy_override["disabled_runtimes"] = _union_disabled_runtimes(
            config,
            "cursor",
            extra=policy_override.get("disabled_runtimes"),
        )
    if no_local:
        override["local"]["enabled"] = False
        # Highest invocation override: strip LOCAL even if config explicitly
        # listed it under execution.policy.allowed_localities.
        policy_override["allowed_localities"] = _allowed_localities_without_local(
            config
        )
    if policy_override:
        override["execution"] = {"policy": policy_override}
    if (
        not override["providers"]
        and "enabled" not in override["local"]
        and "execution" not in override
    ):
        return config
    if not override["providers"]:
        del override["providers"]
    if "enabled" not in override["local"]:
        del override["local"]
    return deep_merge(config, override)


def _union_disabled_runtimes(
    config: Mapping[str, Any] | dict[str, Any],
    runtime_id: str,
    *,
    extra: list[str] | None = None,
) -> list[str]:
    """Union existing disabled_runtimes with an invocation-disabled runtime id."""
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    policy = (
        execution.get("policy") if isinstance(execution.get("policy"), dict) else {}
    )
    existing = policy.get("disabled_runtimes") if isinstance(policy, dict) else None
    merged: list[str] = []
    seen: set[str] = set()
    for source in (existing or (), extra or (), (runtime_id,)):
        for item in source:
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            merged.append(name)
    return merged


def _allowed_localities_without_local(config: Mapping[str, Any] | dict[str, Any]) -> list[str]:
    """Compute allowed_localities for a ``--no-local`` invocation override."""
    # Stable non-local localities; keep config order when localities are explicit.
    default_non_local = ("remote", "cloud")
    execution = config.get("execution") if isinstance(config.get("execution"), dict) else {}
    policy = (
        execution.get("policy") if isinstance(execution.get("policy"), dict) else {}
    )
    raw = policy.get("allowed_localities") if isinstance(policy, dict) else None
    if raw is None:
        return list(default_non_local)
    return [str(loc) for loc in raw if str(loc).strip().lower() != "local"]
