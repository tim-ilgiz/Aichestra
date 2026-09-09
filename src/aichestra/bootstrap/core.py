"""Idempotent bootstrap/update core (FR-014/015/041/048/049)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aichestra.config.layering import (
    load_json,
    machine_local_path,
    resolve_config,
    save_machine_local,
)
from aichestra.machine_profiler import profile_machine
from aichestra.repo import find_repo_root


@dataclass
class BootstrapResult:
    repo_root: str
    created_machine_local: bool
    preserved_machine_local: bool
    local_enabled: bool
    actions: list[str] = field(default_factory=list)
    profile_summary: dict[str, Any] = field(default_factory=dict)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "created_machine_local": self.created_machine_local,
            "preserved_machine_local": self.preserved_machine_local,
            "local_enabled": self.local_enabled,
            "actions": list(self.actions),
            "profile_summary": dict(self.profile_summary),
            "ok": self.ok,
        }


def bootstrap(
    *,
    repo_root: Path | str | None = None,
    enable_local: bool | None = None,
    approve_model_download: bool = False,
) -> BootstrapResult:
    """Idempotent bootstrap: ensure dirs/config; preserve machine-local settings."""
    root = Path(repo_root) if repo_root else find_repo_root()
    root = root.resolve()
    actions: list[str] = []

    local_dir = root / ".local"
    local_dir.mkdir(parents=True, exist_ok=True)
    actions.append("ensure_.local")

    gitignore = root / ".gitignore"
    _ensure_gitignore_entries(gitignore, actions)

    ml_path = machine_local_path(root)
    created = False
    preserved = False
    if ml_path.is_file():
        preserved = True
        existing = load_json(ml_path)
        actions.append("preserve_machine_local")
        changed = False
        if enable_local is not None:
            existing.setdefault("local", {})
            existing["local"]["enabled"] = bool(enable_local)
            changed = True
            actions.append("update_local_enabled_flag")
        if approve_model_download:
            existing.setdefault("notes", {})
            existing["notes"]["model_download_requires_approval"] = True
            existing["notes"]["approve_model_download"] = True
            changed = True
            actions.append("persist_approve_model_download")
        if changed:
            save_machine_local(existing, root)
    else:
        profile = profile_machine(root=root)
        data = {
            "local": {
                "enabled": bool(enable_local) if enable_local is not None else False,
                "suggested_model_class": profile.suggested_model_class,
                "max_local_workers": profile.max_local_workers,
            },
            "providers": {},
            "profiler": {
                "os": profile.os,
                "architecture": profile.architecture,
                "memory_gb": profile.memory.total_gb,
                "suggested_profile_id": profile.suggested_profile_id,
            },
            "notes": {
                "model_download_requires_approval": True,
                "approve_model_download": bool(approve_model_download),
            },
        }
        save_machine_local(data, root)
        created = True
        actions.append("create_machine_local")
        if approve_model_download:
            actions.append("persist_approve_model_download")

    cfg = resolve_config(repo_root=root)
    local_enabled = bool(cfg.get("local", {}).get("enabled", False))
    profile = profile_machine(root=root)
    return BootstrapResult(
        repo_root=str(root),
        created_machine_local=created,
        preserved_machine_local=preserved,
        local_enabled=local_enabled,
        actions=actions,
        profile_summary={
            "os": profile.os,
            "architecture": profile.architecture,
            "memory_gb": profile.memory.total_gb,
            "suggested_model_class": profile.suggested_model_class,
        },
        ok=True,
    )


def update(*, repo_root: Path | str | None = None) -> BootstrapResult:
    """Idempotent update after git pull — preserves machine-local settings."""
    result = bootstrap(repo_root=repo_root)
    result.actions.append("update_via_bootstrap")
    return result


def _ensure_gitignore_entries(gitignore: Path, actions: list[str]) -> None:
    required = [
        ".local/",
        "*.local.json",
        ".env",
        ".env.*",
        "!.env.example",
        "models/",
        "*.gguf",
        "sessions/",
        "caches/",
        "*.log",
    ]
    existing = ""
    if gitignore.is_file():
        existing = gitignore.read_text(encoding="utf-8")
    lines = existing.splitlines()
    changed = False
    for entry in required:
        if entry not in lines:
            lines.append(entry)
            changed = True
    if changed:
        text = "\n".join(lines).rstrip() + "\n"
        gitignore.write_text(text, encoding="utf-8")
        actions.append("update_gitignore")
