#!/usr/bin/env python3
"""Mac live smoke — report only components actually available.

Windows/Linux live smoke remain NOT VALIDATED until executed on those OSes.

Discovery ≠ task execution: a binary on PATH may be discoverable without being
Mode-C-executable (e.g. plain ``cursor`` IDE vs ``cursor-agent``).
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from aichestra.doctor import run_doctor
from aichestra.machine_profiler import profile_machine
from aichestra.providers.base import ProviderTaskRequest
from aichestra.providers.codex import CodexProvider
from aichestra.providers.cursor import CursorProvider
from aichestra.providers.local_worker import LocalWorkerProvider
from aichestra.providers.orca import OrcaProvider
from aichestra.repo import find_repo_root


def _execution_live(adapter) -> tuple[str, dict]:
    """Probe-only → DISCOVERED; LIVE only after successful execute_task."""
    status = adapter.probe()
    meta = {
        "available": status.available,
        "detail": status.detail,
        "discovery_note": "discovery≠task execution; LIVE requires execute_task",
    }
    supports = True
    if hasattr(adapter, "supports_execution"):
        try:
            supports = bool(adapter.supports_execution())
        except Exception as exc:  # noqa: BLE001
            supports = False
            meta["supports_execution_error"] = str(exc)
    meta["supports_execution"] = supports
    if not status.available:
        return "ABSENT", meta
    if not supports:
        return "DISCOVERED_NOT_EXECUTABLE", meta
    return "DISCOVERED", meta


def _try_execute_live(adapter) -> tuple[str, dict]:
    """Optional real execute_task — only this may upgrade label to LIVE."""
    label, meta = _execution_live(adapter)
    if label != "DISCOVERED":
        return label, meta
    try:
        result = adapter.execute_task(
            ProviderTaskRequest(
                prompt="Aichestra smoke dry execute — no side effects.",
                role="smoke",
                read_only=True,
                timeout_seconds=15.0,
            )
        )
        meta["execute_ok"] = result.ok
        meta["execute_detail"] = result.detail
        meta["execute_failure"] = result.failure.value
        if result.ok:
            return "LIVE", meta
        return "DISCOVERED_EXECUTE_FAILED", meta
    except Exception as exc:  # noqa: BLE001
        meta["execute_error"] = str(exc)
        return "DISCOVERED_EXECUTE_ERROR", meta


def _try_orca_supervised_smoke(project_root: Path) -> dict:
    """Optional Mode C-shaped Orca path: ensure_run → supervised implement.

    Enabled with ``AICHESTRA_SMOKE_ORCA_SUPERVISED=1``. This is the production
    shape (one Run + worker-start + worktree), not merely run-create.
    """
    from aichestra.providers.base import ProviderTaskRequest
    from aichestra.providers.orca import OrcaProvider

    orca = OrcaProvider()
    meta: dict = {"path": "ensure_run→worker-start", "project_root": str(project_root)}
    ensure = orca.execute_task(
        ProviderTaskRequest(
            prompt="Aichestra smoke Mode C ensure_run",
            role="ensure_run",
            read_only=True,
            cwd=str(project_root),
            timeout_seconds=30.0,
        )
    )
    meta["ensure_ok"] = ensure.ok
    meta["ensure_detail"] = ensure.detail
    run_id = (ensure.metadata or {}).get("run_id")
    meta["run_id"] = run_id
    if not ensure.ok or not run_id:
        meta["label"] = "SUPERVISED_ENSURE_FAILED"
        return meta
    implement = orca.execute_task(
        ProviderTaskRequest(
            prompt="Aichestra smoke supervised read-only ping — no edits.",
            role="research",
            read_only=True,
            cwd=str(project_root),
            timeout_seconds=60.0,
            context={"run_id": run_id, "agent": "codex", "worktree": "current"},
        )
    )
    meta["supervised_ok"] = implement.ok
    meta["supervised_detail"] = implement.detail
    meta["worktree_path"] = (implement.metadata or {}).get("worktree_path")
    meta["label"] = "LIVE_SUPERVISED" if implement.ok else "SUPERVISED_FAILED"
    return meta


def main() -> int:
    system = platform.system().lower()
    root = find_repo_root(ROOT)
    doctor = run_doctor(repo_root=root)
    profile = profile_machine(root=root)

    # Live smoke must probe real binaries, not CI fake-provider mode.
    adapters = [
        OrcaProvider(),
        CodexProvider(),
        CursorProvider(),
        LocalWorkerProvider(local_enabled=False),
    ]

    live_components: dict[str, str] = {}
    provider_meta: dict[str, dict] = {}
    execute_results: dict[str, dict] = {}
    supervised: dict | None = None
    if system == "darwin":
        live_components["machine_profiler"] = "LIVE"
        if profile.memory.available_bytes is not None:
            live_components["memory_available"] = "LIVE"
        live_components["doctor"] = "LIVE" if doctor.ok else "LIVE_WITH_WARNINGS"
        for adapter in adapters:
            if os.environ.get("AICHESTRA_SMOKE_EXECUTE", "").strip() in {
                "1",
                "true",
                "yes",
            }:
                label, meta = _try_execute_live(adapter)
                execute_results[adapter.kind.value] = {
                    "ok": meta.get("execute_ok"),
                    "detail": meta.get("execute_detail") or meta.get("execute_error"),
                    "failure": meta.get("execute_failure"),
                }
            else:
                label, meta = _execution_live(adapter)
            live_components[adapter.kind.value] = label
            provider_meta[adapter.kind.value] = meta
        if os.environ.get("AICHESTRA_SMOKE_ORCA_SUPERVISED", "").strip() in {
            "1",
            "true",
            "yes",
        }:
            supervised = _try_orca_supervised_smoke(root)
            live_components["orca_supervised"] = supervised.get("label", "UNKNOWN")
    else:
        live_components["note"] = (
            f"This smoke script was executed on {system}; "
            "Mac-oriented live labels apply only on darwin."
        )

    report = {
        "os_executed": system,
        "repo_root": str(root),
        "macos_live": system == "darwin",
        "windows_live": False,
        "linux_live": False,
        "windows_status": "NOT VALIDATED",
        "linux_status": "NOT VALIDATED",
        "note": "Provider LIVE requires probe.available AND supports_execution; "
        "discovery≠task execution. Set AICHESTRA_SMOKE_ORCA_SUPERVISED=1 for "
        "Mode C-shaped ensure_run→worker-start smoke.",
        "components": live_components,
        "provider_meta": provider_meta,
        "profile_os": profile.os,
        "memory_available_bytes": profile.memory.available_bytes,
        "doctor_ok": doctor.ok,
    }
    if execute_results:
        report["smoke_execute"] = execute_results
    if supervised:
        report["orca_supervised"] = supervised
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if doctor.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
