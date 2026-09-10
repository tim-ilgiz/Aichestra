"""Cross-platform doctor / health check (FR-040/061)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from aichestra.config.layering import local_enabled, resolve_config
from aichestra.local_runtime.discovery import discover_local_runtime_report
from aichestra.machine_profiler import profile_machine
from aichestra.platform_detect import platform_info, wsl_required
from aichestra.providers.discovery import discover_providers_report
from aichestra.repo import find_repo_root
from aichestra.security.staging_allowlist import PRODUCTION_SSH_SUPPORTED
from aichestra.security.staging_ssh import staging_configured


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
        }


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    live_validation: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(c.status is CheckStatus.FAIL for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "live_validation": dict(self.live_validation),
        }


def run_doctor(
    *,
    repo_root: Path | str | None = None,
    project_root: Path | str | None = None,
) -> DoctorReport:
    """Report PASS/WARN/FAIL without printing credentials."""
    report = DoctorReport(
        live_validation={
            "macos": "may be live-validated separately",
            "windows": "NOT VALIDATED",
            "linux": "NOT VALIDATED",
        }
    )
    root: Path | None = None
    try:
        root = Path(repo_root).resolve() if repo_root else find_repo_root()
        version_detail = _aichestra_version_detail(root)
        report.checks.append(
            CheckResult(
                "aichestra",
                CheckStatus.PASS,
                f"repository root resolved: {root}; {version_detail}",
            )
        )
    except FileNotFoundError as exc:
        report.checks.append(
            CheckResult("aichestra", CheckStatus.FAIL, str(exc))
        )
        return report

    report.checks.append(_git_check(root))

    # Machine
    try:
        profile = profile_machine(root=root)
        report.checks.append(
            CheckResult(
                "machine",
                CheckStatus.PASS,
                (
                    f"{profile.os}/{profile.architecture} "
                    f"ram={profile.memory.total_gb}GiB "
                    f"suggested={profile.suggested_model_class}"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 — doctor must not crash
        report.checks.append(
            CheckResult("machine", CheckStatus.FAIL, f"profiler error: {exc}")
        )

    # Platform
    info = platform_info()
    if info.os.value == "other":
        report.checks.append(
            CheckResult("platform", CheckStatus.WARN, f"unsupported OS: {info.system}")
        )
    else:
        wsl_note = " (WSL detected; WSL not required)" if info.is_wsl else ""
        report.checks.append(
            CheckResult(
                "platform",
                CheckStatus.PASS,
                f"{info.os.value} python={info.python_version}; "
                f"wsl_required={wsl_required()}{wsl_note}",
            )
        )

    cfg = resolve_config(repo_root=root, project_root=project_root)
    enabled = local_enabled(cfg)

    # Cloud providers — report each lead/control-plane distinctly (FR-061).
    from aichestra.providers.discovery import enabled_map_from_config

    providers = discover_providers_report(
        local_enabled=enabled,
        enabled=enabled_map_from_config(cfg),
        config=cfg,
    )
    by_kind = providers.get("providers") if isinstance(providers.get("providers"), dict) else {}
    for kind_name, label in (
        ("orca", "orca"),
        ("codex", "codex"),
        ("cursor", "cursor"),
        ("local-worker", "opencode_local_worker"),
        ("opencode", "opencode"),
        ("ollama", "ollama"),
    ):
        info = by_kind.get(kind_name) if isinstance(by_kind, dict) else None
        if not isinstance(info, dict):
            if kind_name == "orca":
                available = bool(providers.get("orca_available"))
                detail = "Orca control plane discovered" if available else "Orca absent"
            elif kind_name == "local-worker":
                available = bool(providers.get("local_worker_available"))
                detail = "local-worker available" if available else "local-worker absent"
            else:
                continue
        else:
            available = bool(info.get("available"))
            detail = str(
                info.get("version")
                or info.get("detail")
                or ("available" if available else "absent")
            )
        if kind_name == "orca" and available:
            detail = (
                f"{detail}; required skill: orchestration "
                "(inspect: orca skills get orchestration --full)"
            )
        report.checks.append(
            CheckResult(
                label,
                CheckStatus.PASS if available else CheckStatus.WARN,
                detail,
            )
        )
    if providers["any_lead"]:
        report.checks.append(
            CheckResult(
                "cloud_providers",
                CheckStatus.PASS,
                "at least one lead provider available (Codex preferred / Cursor fallback)",
            )
        )
    else:
        report.checks.append(
            CheckResult(
                "cloud_providers",
                CheckStatus.WARN,
                "no Codex/Cursor on PATH — native/orchestrated lead workflows degraded",
            )
        )

    # Local AI
    local_report = discover_local_runtime_report(local_enabled=enabled)
    if not enabled:
        report.checks.append(
            CheckResult(
                "local_ai",
                CheckStatus.PASS,
                "local.enabled=false (valid); cloud-only mode OK",
            )
        )
    elif local_report["any_available"]:
        report.checks.append(
            CheckResult("local_ai", CheckStatus.PASS, "local runtime available")
        )
    else:
        report.checks.append(
            CheckResult(
                "local_ai",
                CheckStatus.WARN,
                "local enabled but no runtime discovered",
            )
        )

    # Selected local model hint from config (informational; never auto-download)
    local_cfg = cfg.get("local") if isinstance(cfg.get("local"), dict) else {}
    preferred = local_cfg.get("preferred_models") or local_cfg.get("preferred_ids")
    if preferred:
        report.checks.append(
            CheckResult(
                "local_model_hint",
                CheckStatus.PASS,
                f"preferred local model hint: {preferred}",
            )
        )
    elif enabled:
        report.checks.append(
            CheckResult(
                "local_model_hint",
                CheckStatus.WARN,
                "local enabled but no preferred_models/allowed_models in config",
            )
        )

    # Project
    proj: Path | None = None
    if project_root is not None:
        proj = Path(project_root)
        if proj.is_dir():
            report.checks.append(
                CheckResult("project", CheckStatus.PASS, f"project root: {proj.resolve()}")
            )
        else:
            report.checks.append(
                CheckResult("project", CheckStatus.FAIL, f"project root missing: {proj}")
            )
            proj = None
    else:
        report.checks.append(
            CheckResult(
                "project",
                CheckStatus.PASS,
                "no target project supplied (Aichestra-only check)",
            )
        )

    if proj is not None:
        try:
            from aichestra.orchestration.factory_preserve import detect_factory_tooling

            factory = detect_factory_tooling(proj)
            if factory.present:
                report.checks.append(
                    CheckResult(
                        "factory",
                        CheckStatus.PASS,
                        f"Factory tooling present: {', '.join(factory.markers)}",
                    )
                )
            else:
                report.checks.append(
                    CheckResult("factory", CheckStatus.PASS, "no Factory tooling detected")
                )
        except Exception as exc:  # noqa: BLE001
            report.checks.append(
                CheckResult("factory", CheckStatus.WARN, f"factory detection error: {exc}")
            )

    # Verification commands from project/config
    try:
        from aichestra.orchestration.verification import verification_commands_from_config

        verify_cmds = verification_commands_from_config(cfg)
        if verify_cmds:
            report.checks.append(
                CheckResult(
                    "verification",
                    CheckStatus.PASS,
                    f"{len(verify_cmds)} verification command(s) configured: "
                    + "; ".join(" ".join(c) for c in verify_cmds[:3]),
                )
            )
        elif proj is not None:
            report.checks.append(
                CheckResult(
                    "verification",
                    CheckStatus.WARN,
                    "project root set but no verify/build commands in config",
                )
            )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            CheckResult("verification", CheckStatus.WARN, f"verify config error: {exc}")
        )

    if proj is not None:
        report.checks.append(_speckit_project_check(proj))

    # Selected model / reason from machine-local notes when present
    try:
        from aichestra.config.layering import load_json, machine_local_path

        ml = machine_local_path(root)
        if ml.is_file():
            data = load_json(ml)
            local_notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
            selection = local_notes.get("selected_model") or data.get("local", {}).get(
                "selected_model"
            )
            if selection:
                report.checks.append(
                    CheckResult(
                        "selected_model",
                        CheckStatus.PASS,
                        f"selected_model={selection}",
                    )
                )
            elif enabled:
                report.checks.append(
                    CheckResult(
                        "selected_model",
                        CheckStatus.WARN,
                        "local enabled but no selected_model recorded yet",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            CheckResult("selected_model", CheckStatus.WARN, f"selection read error: {exc}")
        )

    # Staging
    if PRODUCTION_SSH_SUPPORTED:
        report.checks.append(
            CheckResult("staging", CheckStatus.FAIL, "production SSH must not be enabled")
        )
    elif staging_configured(repo_root=root):
        report.checks.append(
            CheckResult(
                "staging",
                CheckStatus.PASS,
                "staging aliases configured; production SSH path absent",
            )
        )
    else:
        report.checks.append(
            CheckResult(
                "staging",
                CheckStatus.WARN,
                "staging-only diagnostics available; no aliases configured yet "
                "(set staging.aliases in machine-local config)",
            )
        )

    return report


def _aichestra_version_detail(root: Path) -> str:
    parts: list[str] = []
    try:
        from importlib.metadata import version

        parts.append(f"pkg={version('aichestra')}")
    except Exception:  # noqa: BLE001
        parts.append("pkg=unknown")
    try:
        import subprocess

        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode == 0 and (completed.stdout or "").strip():
            parts.append(f"commit={(completed.stdout or '').strip()}")
    except Exception:  # noqa: BLE001
        parts.append("commit=unknown")
    return ", ".join(parts)


def _git_check(root: Path) -> CheckResult:
    try:
        import subprocess

        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-b"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            return CheckResult("git", CheckStatus.WARN, "git status failed")
        branch = "unknown"
        for line in (completed.stdout or "").splitlines():
            if line.startswith("## "):
                branch = line[3:].strip()
                break
        dirty = any(
            line and not line.startswith("## ")
            for line in (completed.stdout or "").splitlines()
        )
        return CheckResult(
            "git",
            CheckStatus.PASS,
            f"branch={branch}; dirty={dirty}",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("git", CheckStatus.WARN, f"git probe error: {exc}")


def _speckit_project_check(project_root: Path) -> CheckResult:
    markers = [
        project_root / ".specify",
        project_root / "specs",
        project_root / ".agents" / "skills" / "speckit-specify",
    ]
    found = [str(p.relative_to(project_root)) for p in markers if p.exists()]
    if found:
        return CheckResult(
            "speckit",
            CheckStatus.PASS,
            "Spec Kit markers: " + ", ".join(found),
        )
    return CheckResult(
        "speckit",
        CheckStatus.WARN,
        "no Spec Kit markers (.specify/specs) in target project",
    )


def format_doctor_report(report: DoctorReport) -> str:
    lines = ["Aichestra doctor", "================"]
    for check in report.checks:
        lines.append(f"[{check.status.value}] {check.name}: {check.detail}")
    lines.append("")
    lines.append("Live validation status:")
    for os_name, status in report.live_validation.items():
        lines.append(f"  - {os_name}: {status}")
    lines.append("")
    lines.append(f"Overall: {'PASS' if report.ok else 'FAIL'}")
    return "\n".join(lines)


def doctor_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), indent=2) + "\n"
