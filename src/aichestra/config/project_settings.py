"""Project `.aichestra/` init and settings (show/set)."""

from __future__ import annotations

import copy
import sys
import json
import re
from pathlib import Path
from typing import Any, Mapping

from aichestra.config.roles import (
    ROLE_KEYS,
    configured_runtimes,
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
    if not yes and sys.stdin.isatty():
        cfg = interactive_settings(root, config=cfg, save=False)
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
    out = copy.deepcopy(config)
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
        binding = parse_role_binding(value, field=dotted, known=configured_runtimes(target))
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
        binding = parse_role_binding(value, field=dotted, known=configured_runtimes(target))
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


def resolve_project_root(override=None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    cwd = Path.cwd().resolve()
    return next((p for p in (cwd, *cwd.parents)
                 if project_config_path(p).is_file()), cwd)


def interactive_settings(project_root, *, config=None, save=True):
    """Small console editor using discovered runtimes/models; writes on Save."""
    if not sys.stdin.isatty():
        raise ValueError("Interactive settings requires a terminal; use settings show or settings set KEY=VALUE")
    from aichestra.config.layering import resolve_config
    from aichestra.repo import resolve_aichestra_config_root
    from aichestra.execution.discovery import discover_execution_facts
    root = Path(project_root).resolve()
    cfg = copy.deepcopy(config if config is not None else load_project_config(root))
    if not cfg:
        raise ValueError("Run aichestra init first")
    layered = resolve_config(repo_root=resolve_aichestra_config_root(project_root=root), project_root=root)
    facts = discover_execution_facts(layered)
    runtimes = [r for r in facts.runtimes if r.available and r.enabled]

    def choose(label, options):
        if not options:
            raise ValueError(f"No discovered {label}; install/configure a runtime, then retry")
        print(label)
        for i, (name, _) in enumerate(options, 1):
            print(f"  {i}. {name}")
        raw = input("Choose number (Enter cancels): ").strip()
        if not raw:
            return None
        if not raw.isdigit() or not 1 <= int(raw) <= len(options):
            raise ValueError("Invalid selection")
        return options[int(raw) - 1][1]

    def binding():
        runtime = choose("Available runtimes", [(r.id, r.id) for r in runtimes])
        if runtime is None:
            return None
        options = [("Runtime default model", {"runtime": runtime})]
        # Only present compatible discovered models, not a cross-product.
        from aichestra.execution.compatibility import load_compatibility_bindings
        from aichestra.execution.targets import resolve_targets
        for t in resolve_targets(facts, load_compatibility_bindings(layered)):
            if t.runtime.id == runtime and t.model and t.enabled and t.available and t.capable:
                options.append((f"{t.provider.id + '/' if t.provider else ''}{t.model.id}", {
                    "runtime": runtime, "model": t.model.id,
                    **({"provider": t.provider.id} if t.provider else {})}))
        return choose("Models", options)

    while True:
        print("\n1. Coding  2. Research  3. Tests  4. Documentation  5. Quota fallback  6. Verification  7. Save  0. Cancel")
        action = input("Settings: ").strip()
        if action == "0":
            return config if config is not None else load_project_config(root)
        if action == "7":
            load_role_bindings(cfg)
            load_quota_policy(cfg)
            if save:
                save_project_config(root, cfg)
            return cfg
        if action in {"1", "2", "3", "4"}:
            value = binding()
            if value:
                cfg.setdefault("roles", {})[ROLE_KEYS[int(action) - 1]] = value
        elif action == "5":
            mode = choose("Quota mode", [("Manual: stop and notify", "manual"), ("Auto: continue same Run", "auto")])
            if mode:
                cfg.setdefault("quota", {})["mode"] = mode
                if mode == "auto":
                    value = binding()
                    if value:
                        cfg["quota"]["implement_fallback"] = value
        elif action == "6":
            raw = input('Verification argv JSON (e.g. [["python", "-m", "pytest"]]): ').strip()
            if raw:
                value = json.loads(raw)
                from aichestra.orchestration.verification import verification_commands_from_config
                if not verification_commands_from_config({"verify": value, "verification": {"enabled": True}}):
                    raise ValueError("Verification requires non-empty commands")
                cfg["verify"] = value
                cfg["verification"] = {"enabled": True}
