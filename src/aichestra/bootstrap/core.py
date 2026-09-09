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
    doctor_ok: bool | None = None
    doctor_checks: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "created_machine_local": self.created_machine_local,
            "preserved_machine_local": self.preserved_machine_local,
            "local_enabled": self.local_enabled,
            "actions": list(self.actions),
            "profile_summary": dict(self.profile_summary),
            "ok": self.ok,
            "doctor_ok": self.doctor_ok,
            "doctor_checks": self.doctor_checks,
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

    doctor_ok, doctor_checks = _enrich_after_config(
        root=root,
        cfg=cfg,
        local_enabled=local_enabled,
        actions=actions,
    )

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
            "memory_available_gb": (
                round(profile.memory.available_bytes / (1024**3), 2)
                if profile.memory.available_bytes is not None
                else None
            ),
            "suggested_model_class": profile.suggested_model_class,
            "doctor_ok": doctor_ok,
            "doctor_checks": doctor_checks,
        },
        ok=True,
        doctor_ok=doctor_ok,
        doctor_checks=doctor_checks,
    )


def update(*, repo_root: Path | str | None = None) -> BootstrapResult:
    """Idempotent update after git pull — preserves machine-local settings."""
    result = bootstrap(repo_root=repo_root)
    result.actions.append("update_via_bootstrap")
    return result


def _enrich_after_config(
    *,
    root: Path,
    cfg: dict[str, Any],
    local_enabled: bool,
    actions: list[str],
) -> tuple[bool | None, int | None]:
    """Discover providers/runtimes, note availability, run doctor — no model downloads."""
    from aichestra.doctor import run_doctor
    from aichestra.local_runtime.discovery import discover_local_runtime_report
    from aichestra.providers.discovery import discover_providers

    local_cfg = cfg.get("local") if isinstance(cfg.get("local"), dict) else {}
    ollama_host = None
    if isinstance(local_cfg, dict):
        ollama_host = local_cfg.get("ollama_host") or local_cfg.get("endpoint")

    providers = discover_providers(
        local_enabled=local_enabled,
        ollama_host=str(ollama_host) if ollama_host else None,
    )
    provider_summary: dict[str, Any] = {}
    for status in providers:
        kind_key = status.kind.value.replace("-", "_")
        actions.append(f"probe_{kind_key}")
        provider_summary[status.kind.value] = {
            "available": status.available,
            "detail": status.detail,
            "role": status.role.value if status.role else None,
        }
        if not status.available:
            actions.append(f"missing_{kind_key}")

    runtime_report = discover_local_runtime_report(
        local_enabled=local_enabled,
        ollama_host=str(ollama_host) if ollama_host else None,
    )
    actions.append("probe_local_runtimes")
    if local_enabled and not runtime_report.get("any_available"):
        actions.append("missing_local_runtime")
    actions.append("no_auto_model_download")

    ml_path = machine_local_path(root)
    try:
        existing = load_json(ml_path) if ml_path.is_file() else {}
        notes = existing.setdefault("notes", {})
        if not isinstance(notes, dict):
            notes = {}
            existing["notes"] = notes
        notes["provider_availability"] = provider_summary
        notes["local_runtime"] = {
            "any_available": bool(runtime_report.get("any_available")),
            "runtimes": runtime_report.get("runtimes", runtime_report),
        }
        notes["model_download_requires_approval"] = True
        # Never write secrets into notes.
        for secret_key in ("token", "password", "api_key", "secret", "ssh_key"):
            notes.pop(secret_key, None)
        save_machine_local(existing, root)
        actions.append("write_provider_availability_notes")
    except OSError:
        actions.append("write_provider_availability_notes_failed")

    doctor_ok: bool | None = None
    doctor_checks: int | None = None
    try:
        report = run_doctor(repo_root=root)
        doctor_ok = report.ok
        doctor_checks = len(report.checks)
        actions.append("run_doctor")
        if not report.ok:
            actions.append("doctor_reported_failures")
    except Exception as exc:  # noqa: BLE001 — bootstrap must stay idempotent
        actions.append(f"run_doctor_failed:{type(exc).__name__}")

    return doctor_ok, doctor_checks


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
