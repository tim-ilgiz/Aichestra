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
    coordinator_binding_to_dict,
    default_coordinator_binding,
    default_quota_policy,
    default_role_bindings,
    load_coordinator_binding,
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
        "orchestration": {
            "coordinator": coordinator_binding_to_dict(default_coordinator_binding()),
        },
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
            existing = apply_settings_sets(existing, sets, project_root=root)
            save_project_config(root, existing)
        _ensure_gitignore_user_local(root)
        return {
            "ok": True,
            "created": False,
            "path": str(path),
            "project_root": str(root),
            "config": existing,
        }

    cfg = default_project_config(project_root=root)
    if sets:
        cfg = apply_settings_sets(cfg, sets, project_root=root)
    if not yes and sys.stdin.isatty():
        cfg = interactive_settings(root, config=cfg, save=False)
    save_project_config(root, cfg)
    _ensure_gitignore_user_local(root)
    return {
        "ok": True,
        "created": True,
        "path": str(path),
        "project_root": str(root),
        "config": cfg,
    }


def _layered_known_runtimes(
    project_root: Path | str | None,
    project_cfg: Mapping[str, Any] | None = None,
) -> set[str]:
    """Runtimes from package/OS/machine layers plus the draft project config."""
    from aichestra.config.layering import deep_merge, resolve_config
    from aichestra.repo import resolve_aichestra_config_root

    root = Path(project_root).resolve() if project_root is not None else None
    base = resolve_config(
        repo_root=resolve_aichestra_config_root(project_root=root),
        project_root=None,
    )
    merged = deep_merge(base, dict(project_cfg or {}))
    return configured_runtimes(merged)


def show_settings(project_root: Path | str) -> dict[str, Any]:
    from aichestra.orchestration.verification import (
        require_verification_toggle,
        verification_enabled,
    )

    raw = load_project_config(project_root)
    require_verification_toggle(raw)
    known = _layered_known_runtimes(project_root, raw)
    bindings = load_role_bindings(raw, known=known)
    quota = load_quota_policy(raw, known=known)
    coordinator = load_coordinator_binding(raw, known=known)
    return {
        "project_root": str(Path(project_root).resolve()),
        "path": str(project_config_path(project_root)),
        "orchestration": {"coordinator": coordinator_binding_to_dict(coordinator)},
        "roles": role_bindings_to_dict(bindings),
        "quota": quota.to_dict(),
        "verification": {
            "enabled": verification_enabled(raw),
        },
        "verify": raw.get("verify"),
        "raw": raw,
    }


def apply_settings_sets(
    config: dict[str, Any],
    pairs: list[str],
    *,
    project_root: Path | str | None = None,
    known=None,
) -> dict[str, Any]:
    from aichestra.orchestration.verification import require_verification_toggle

    out = copy.deepcopy(config)
    for pair in pairs:
        match = _SET_PAIR.match(pair.strip())
        if not match:
            raise ValueError(f"invalid --set (expected KEY=VALUE): {pair}")
        key = match.group(1).strip()
        value_raw = match.group(2)
        value = _parse_value(value_raw)
        effective = (
            known
            if known is not None
            else _layered_known_runtimes(project_root, out)
        )
        _assign_dotted(out, key, value, known=effective | configured_runtimes(out))
    # Re-validate coordinator/worker/quota after mutation.
    require_verification_toggle(out)
    effective = (
        known if known is not None else _layered_known_runtimes(project_root, out)
    )
    effective = effective | configured_runtimes(out)
    load_coordinator_binding(out, known=effective)
    load_role_bindings(out, known=effective)
    load_quota_policy(out, known=effective)
    return out


def settings_set(project_root: Path | str, pairs: list[str]) -> dict[str, Any]:
    path = project_config_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(
            f"missing {path}; run `aichestra init` in the project first"
        )
    cfg = load_project_config(project_root)
    cfg = apply_settings_sets(cfg, pairs, project_root=project_root)
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


def _assign_dotted(
    target: dict[str, Any],
    dotted: str,
    value: Any,
    *,
    known=None,
) -> None:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise ValueError("empty settings key")
    effective = known if known is not None else configured_runtimes(target)
    # Convenience: roles.implement=codex → roles.implement object
    if len(parts) == 2 and parts[0] == "roles" and parts[1] == "coordinator":
        raise ValueError(
            "roles.coordinator is invalid; set orchestration.coordinator"
        )
    if (
        len(parts) == 2
        and parts[0] == "roles"
        and parts[1] in ROLE_KEYS
        and isinstance(value, str)
    ):
        binding = parse_role_binding(value, field=dotted, known=effective)
        roles = target.setdefault("roles", {})
        if not isinstance(roles, dict):
            raise ValueError("roles must be an object")
        roles[parts[1]] = binding.to_dict()
        return
    if (
        len(parts) == 2
        and parts[0] == "orchestration"
        and parts[1] == "coordinator"
        and isinstance(value, str)
    ):
        binding = parse_role_binding(value, field=dotted, known=effective)
        orch = target.setdefault("orchestration", {})
        if not isinstance(orch, dict):
            raise ValueError("orchestration must be an object")
        orch["coordinator"] = binding.to_dict()
        return
    if (
        len(parts) == 2
        and parts[0] == "quota"
        and parts[1] == "implement_fallback"
        and isinstance(value, str)
    ) or (
        len(parts) == 3
        and parts[0] == "quota"
        and parts[1] == "roles"
        and parts[2] == "implement"
        and isinstance(value, str)
    ):
        binding = parse_role_binding(
            value, field="quota.roles.implement", known=effective
        )
        quota = target.setdefault("quota", {})
        if not isinstance(quota, dict):
            raise ValueError("quota must be an object")
        roles = quota.setdefault("roles", {})
        if not isinstance(roles, dict):
            raise ValueError("quota.roles must be an object")
        roles["implement"] = binding.to_dict()
        quota.pop("implement_fallback", None)
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


_RUNTIME_LABELS = {
    "codex": "Codex",
    "cursor": "Cursor",
    "gemini": "Gemini",
    "opencode": "OpenCode",
    "ollama": "Ollama",
}


def resolve_project_root(override=None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    cwd = Path.cwd().resolve()
    return next((p for p in (cwd, *cwd.parents)
                 if project_config_path(p).is_file()), cwd)


def _radio_glyphs() -> tuple[str, str]:
    """Empty / filled radio marks (BMAD/inquirer style), ASCII if needed."""
    empty, filled = "◯", "◉"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        f"{empty}{filled}".encode(encoding)
        return empty, filled
    except (LookupError, UnicodeEncodeError):
        return "( )", "(*)"


def _runtime_label(runtime_id: str) -> str:
    return _RUNTIME_LABELS.get(runtime_id, runtime_id)


def _binding_summary(value: Any) -> str:
    if not isinstance(value, Mapping) or not value.get("runtime"):
        return "not set"
    runtime = _runtime_label(str(value["runtime"]))
    model = value.get("model")
    provider = value.get("provider")
    if model:
        name = f"{provider}/{model}" if provider else str(model)
        return f"{runtime} · {name}"
    return f"{runtime} · runtime default"


def _quota_summary(cfg: Mapping[str, Any]) -> str:
    quota = cfg.get("quota") if isinstance(cfg.get("quota"), Mapping) else {}
    mode = str(quota.get("mode") or "manual")
    if mode == "auto":
        roles = quota.get("roles") if isinstance(quota.get("roles"), Mapping) else {}
        fallback = roles.get("implement") or quota.get("implement_fallback")
        return f"auto · {_binding_summary(fallback)}"
    return "manual · stop and notify"


def _verification_summary(cfg: Mapping[str, Any]) -> str:
    verification = cfg.get("verification") if isinstance(cfg.get("verification"), Mapping) else {}
    if verification.get("enabled") and cfg.get("verify"):
        return "on"
    return "off"


def _print_radio_menu(
    question: str,
    options: list[tuple[str, Any]],
    *,
    selected_index: int | None = None,
    hint: str | None = None,
) -> None:
    empty, filled = _radio_glyphs()
    print()
    print(f"? {question}")
    if hint:
        print(f"  {hint}")
    print()
    for i, (name, _) in enumerate(options):
        mark = filled if i == selected_index else empty
        print(f"  {mark}  {i + 1}. {name}")
    print()


def _prompt_choice(
    question: str,
    options: list[tuple[str, Any]],
    *,
    selected: Any = None,
    hint: str | None = None,
    compare=None,
) -> Any:
    if not options:
        raise ValueError(
            f"No discovered {question}; install/configure a runtime, then retry"
        )
    selected_index = None
    matcher = compare or (lambda left, right: left == right)
    if selected is not None:
        for i, (_, value) in enumerate(options):
            if matcher(value, selected):
                selected_index = i
                break
    _print_radio_menu(question, options, selected_index=selected_index, hint=hint)
    raw = input("Enter number (Enter cancels): ").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= len(options):
        raise ValueError("Invalid selection")
    return options[int(raw) - 1][1]


def _discovered_model_options(runtime: str, facts, layered) -> list[tuple[str, dict[str, Any]]]:
    options: list[tuple[str, dict[str, Any]]] = [
        (
            f"Use {_runtime_label(runtime)}'s default model",
            {"runtime": runtime},
        )
    ]
    from aichestra.execution.compatibility import load_compatibility_bindings
    from aichestra.execution.targets import resolve_targets

    for t in resolve_targets(facts, load_compatibility_bindings(layered)):
        if t.runtime.id == runtime and t.model and t.enabled and t.available and t.capable:
            label = f"{t.provider.id + '/' if t.provider else ''}{t.model.id}"
            payload: dict[str, Any] = {"runtime": runtime, "model": t.model.id}
            if t.provider:
                payload["provider"] = t.provider.id
            options.append((label, payload))
    return options


def _same_binding(left: Any, right: Any) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return left == right
    return (
        left.get("runtime") == right.get("runtime")
        and (left.get("model") or None) == (right.get("model") or None)
        and (left.get("provider") or None) == (right.get("provider") or None)
    )


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
    empty = _radio_glyphs()[0]

    def choose(question, options, *, selected=None, hint=None, compare=None):
        return _prompt_choice(
            question, options, selected=selected, hint=hint, compare=compare
        )

    def binding(role_label: str, current: Any = None):
        print()
        print(f"Editing: {role_label}")
        current_runtime = (
            current.get("runtime") if isinstance(current, Mapping) else None
        )
        runtime = choose(
            f"Choose a runtime for {role_label}",
            [(_runtime_label(r.id), r.id) for r in runtimes],
            selected=current_runtime,
            hint="This picks which agent stack runs this role.",
        )
        if runtime is None:
            return None
        options = _discovered_model_options(runtime, facts, layered)
        current_for_models = current if current_runtime == runtime else None
        if len(options) == 1:
            print()
            print(
                f"  No extra models were discovered for {_runtime_label(runtime)}."
            )
            print(
                f"  {role_label} will use {_runtime_label(runtime)}'s default model."
            )
            return options[0][1]
        return choose(
            f"Choose a model for {role_label} on {_runtime_label(runtime)}",
            options,
            selected=current_for_models,
            hint=(
                f"Pick a specific discovered model, or keep "
                f"{_runtime_label(runtime)}'s own default."
            ),
            compare=_same_binding,
        )

    def print_main_menu() -> None:
        from aichestra.console_ui import box, use_pretty

        coordinator = (cfg.get("orchestration") or {}).get("coordinator")
        roles = cfg.get("roles") if isinstance(cfg.get("roles"), Mapping) else {}
        items = [
            ("1", "Coordinator", _binding_summary(coordinator)),
            ("2", "Coding", _binding_summary(roles.get("implement"))),
            ("3", "Research", _binding_summary(roles.get("research"))),
            ("4", "Tests", _binding_summary(roles.get("tests"))),
            ("5", "Documentation", _binding_summary(roles.get("docs"))),
            ("6", "Quota fallback", _quota_summary(cfg)),
            ("7", "Verification", _verification_summary(cfg)),
            ("8", "Save", "write .aichestra/project.json"),
            ("0", "Cancel", "discard unsaved edits"),
        ]
        print()
        if use_pretty():
            print(
                box(
                    [
                        "Pick a number to edit that role.",
                        "Save writes the project file.",
                    ],
                    title="Project settings",
                )
            )
        else:
            print("Aichestra project settings")
            print("Pick a number to edit that role. Save writes the project file.")
        print()
        for number, title, detail in items:
            print(f"  {empty}  {number}. {title}")
            print(f"        {detail}")
        print()

    while True:
        print_main_menu()
        action = input("Enter number: ").strip()
        if action == "0":
            return config if config is not None else load_project_config(root)
        if action == "8":
            from aichestra.orchestration.verification import require_verification_toggle

            known = _layered_known_runtimes(root, cfg)
            require_verification_toggle(cfg)
            load_coordinator_binding(cfg, known=known)
            load_role_bindings(cfg, known=known)
            load_quota_policy(cfg, known=known)
            if save:
                save_project_config(root, cfg)
            return cfg
        if action == "1":
            value = binding(
                "Coordinator",
                (cfg.get("orchestration") or {}).get("coordinator"),
            )
            if value:
                cfg.setdefault("orchestration", {})["coordinator"] = value
                print(f"  Set Coordinator → {_binding_summary(value)}")
        elif action in {"2", "3", "4", "5"}:
            role_key = ROLE_KEYS[int(action) - 2]
            labels = {
                "implement": "Coding",
                "research": "Research",
                "tests": "Tests",
                "docs": "Documentation",
            }
            roles = cfg.setdefault("roles", {})
            value = binding(labels[role_key], roles.get(role_key) if isinstance(roles, dict) else None)
            if value:
                roles[role_key] = value
                print(f"  Set {labels[role_key]} → {_binding_summary(value)}")
        elif action == "6":
            quota = cfg.setdefault("quota", {})
            mode = choose(
                "How should coding quota exhaustion be handled?",
                [
                    ("Manual — stop and notify (do not switch runtime)", "manual"),
                    ("Auto — continue the same Run on a fallback runtime", "auto"),
                ],
                selected=quota.get("mode") if isinstance(quota, dict) else None,
            )
            if mode:
                quota["mode"] = mode
                if mode == "auto":
                    roles = quota.setdefault("roles", {})
                    current = (
                        roles.get("implement")
                        if isinstance(roles, dict)
                        else None
                    ) or quota.get("implement_fallback")
                    value = binding("Quota fallback for coding", current)
                    if value:
                        if not isinstance(roles, dict):
                            raise ValueError("quota.roles must be an object")
                        roles["implement"] = value
                        quota.pop("implement_fallback", None)
                        print(
                            f"  Set quota fallback → {_binding_summary(value)}"
                        )
                else:
                    print("  Set quota mode → manual (stop and notify)")
        elif action == "7":
            print()
            print("? Verification commands")
            print("  JSON argv list. Leave empty to cancel.")
            print()
            raw = input('Enter JSON (e.g. [["python", "-m", "pytest"]]): ').strip()
            if raw:
                value = json.loads(raw)
                from aichestra.orchestration.verification import verification_commands_from_config
                if not verification_commands_from_config({"verify": value, "verification": {"enabled": True}}):
                    raise ValueError("Verification requires non-empty commands")
                cfg["verify"] = value
                cfg["verification"] = {"enabled": True}
                print("  Set Verification → on")
