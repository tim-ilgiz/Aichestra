"""Configuration layering: tracked → OS → machine-local → project → runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

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
