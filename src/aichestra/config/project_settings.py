"""Project `.aichestra/` init and settings (show/set)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from aichestra.config.roles import (
    ROLE_KEYS,
    default_quota_policy,
    default_role_bindings,
    load_quota_policy,
    load_role_bindings,
    parse_role_binding,
    role_bindings_to_dict,
)

PROJECT_DIRNAME = ".aichestra"
PROJECT_CONFIG_NAME = "project.json"
USER_LOCAL_NAME = "user.local.json"

_SET_PAIR = re.compile(r"^([^=]+)=(.*)$", re.DOTALL)


def project_aichestra_dir(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / PROJECT_DIRNAME


def project_config_path(project_root: Path | str) -> Path:
    return project_aichestra_dir(project_root) / PROJECT_CONFIG_NAME


def default_project_config(*, project_root: Path | str | None = None) -> dict[str, Any]:
    """Default project.json — roles/quota; verification off (fail-closed) until enabled."""
    _ = project_root
    return {
        "roles": role_bindings_to_dict(default_role_bindings()),
        "quota": default_quota_policy().to_dict(),
        "verification": {"enabled": False},
        "verify": None,
    }


def load_project_config(project_root: Path | str) -> dict[str, Any]:
    path = project_config_path(project_root)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return data


def save_project_config(project_root: Path | str, data: Mapping[str, Any]) -> Path:
    root = project_aichestra_dir(project_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / PROJECT_CONFIG_NAME
    path.write_text(
        json.dumps(dict(data), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def init_project(
    project_root: Path | str,
    *,
    yes: bool = False,
    sets: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create `.aichestra/project.json` if missing (or force overwrite)."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {root}")
    path = project_config_path(root)
    if path.is_file() and not force:
        existing = load_project_config(root)
        if sets:
            existing = apply_settings_sets(existing, sets)
            save_project_config(root, existing)
        _ensure_gitignore_user_local(root)
        return {
            "ok": True,
            "created": False,
            "path": str(path),
            "config": existing,
        }

    cfg = default_project_config(project_root=root)
    if sets:
        cfg = apply_settings_sets(cfg, sets)
    elif not yes:
        # Non-interactive default when stdin not a TTY; keep defaults.
        pass
    save_project_config(root, cfg)
    _ensure_gitignore_user_local(root)
    return {"ok": True, "created": True, "path": str(path), "config": cfg}


def show_settings(project_root: Path | str) -> dict[str, Any]:
    from aichestra.orchestration.verification import verification_enabled

    raw = load_project_config(project_root)
    bindings = load_role_bindings(raw)
    quota = load_quota_policy(raw)
    return {
        "project_root": str(Path(project_root).resolve()),
        "path": str(project_config_path(project_root)),
        "roles": role_bindings_to_dict(bindings),
        "quota": quota.to_dict(),
        "verification": {
            "enabled": verification_enabled(raw),
        },
        "verify": raw.get("verify"),
        "raw": raw,
    }


def apply_settings_sets(config: dict[str, Any], pairs: list[str]) -> dict[str, Any]:
    out = dict(config)
    for pair in pairs:
        match = _SET_PAIR.match(pair.strip())
        if not match:
            raise ValueError(f"invalid --set (expected KEY=VALUE): {pair}")
        key = match.group(1).strip()
        value_raw = match.group(2)
        value = _parse_value(value_raw)
        _assign_dotted(out, key, value)
    # Re-validate roles/quota after mutation.
    load_role_bindings(out)
    load_quota_policy(out)
    return out


def settings_set(project_root: Path | str, pairs: list[str]) -> dict[str, Any]:
    path = project_config_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(
            f"missing {path}; run `aichestra init` in the project first"
        )
    cfg = load_project_config(project_root)
    cfg = apply_settings_sets(cfg, pairs)
    save_project_config(project_root, cfg)
    return show_settings(project_root)


def _parse_value(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "{\"[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _assign_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise ValueError("empty settings key")
    # Convenience: roles.implement=codex → roles.implement object
    if (
        len(parts) == 2
        and parts[0] == "roles"
        and parts[1] in ROLE_KEYS
        and isinstance(value, str)
    ):
        binding = parse_role_binding(value, field=dotted)
        roles = target.setdefault("roles", {})
        if not isinstance(roles, dict):
            raise ValueError("roles must be an object")
        roles[parts[1]] = binding.to_dict()
        return
    if (
        len(parts) == 2
        and parts[0] == "quota"
        and parts[1] == "implement_fallback"
        and isinstance(value, str)
    ):
        binding = parse_role_binding(value, field=dotted)
        quota = target.setdefault("quota", {})
        if not isinstance(quota, dict):
            raise ValueError("quota must be an object")
        quota["implement_fallback"] = binding.to_dict()
        return

    cur: Any = target
    for part in parts[:-1]:
        if not isinstance(cur, dict):
            raise ValueError(f"cannot set {dotted}: parent is not an object")
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    if not isinstance(cur, dict):
        raise ValueError(f"cannot set {dotted}: parent is not an object")
    cur[parts[-1]] = value


def _ensure_gitignore_user_local(project_root: Path) -> None:
    gitignore = Path(project_root) / ".gitignore"
    marker = f"{PROJECT_DIRNAME}/{USER_LOCAL_NAME}"
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8")
        if marker in text or f"**/{marker}" in text:
            return
        suffix = "" if text.endswith("\n") or not text else "\n"
        gitignore.write_text(text + suffix + f"\n# Aichestra personal overlay\n{marker}\n", encoding="utf-8")
    else:
        gitignore.write_text(
            f"# Aichestra personal overlay\n{marker}\n",
            encoding="utf-8",
        )
