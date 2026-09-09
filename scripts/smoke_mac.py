#!/usr/bin/env python3
"""Mac live smoke — report only components actually available.

Windows/Linux live smoke remain NOT VALIDATED until executed on those OSes.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from aichestra.doctor import run_doctor
from aichestra.machine_profiler import profile_machine
from aichestra.providers.discovery import discover_providers_report
from aichestra.repo import find_repo_root


def main() -> int:
    system = platform.system().lower()
    root = find_repo_root(ROOT)
    doctor = run_doctor(repo_root=root)
    profile = profile_machine(root=root)
    # Live smoke must probe real binaries, not CI fake-provider mode.
    providers = discover_providers_report(
        local_enabled=False,
        force_real=True,
    )

    live_components: dict[str, str] = {}
    if system == "darwin":
        live_components["machine_profiler"] = "LIVE"
        live_components["doctor"] = "LIVE" if doctor.ok else "LIVE_WITH_WARNINGS"
        for kind, info in providers["providers"].items():
            live_components[kind] = "LIVE" if info.get("available") else "ABSENT"
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
        "components": live_components,
        "profile_os": profile.os,
        "doctor_ok": doctor.ok,
    }
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if doctor.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
