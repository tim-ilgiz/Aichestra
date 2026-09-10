"""Cross-platform OS detection helpers."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from enum import Enum


class OperatingSystem(str, Enum):
    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"
    OTHER = "other"


@dataclass(frozen=True)
class PlatformInfo:
    os: OperatingSystem
    system: str
    architecture: str
    python_version: str
    is_wsl: bool


def detect_os(system: str | None = None) -> OperatingSystem:
    name = (system or platform.system()).lower()
    if name == "darwin":
        return OperatingSystem.MACOS
    if name == "windows":
        return OperatingSystem.WINDOWS
    if name == "linux":
        return OperatingSystem.LINUX
    return OperatingSystem.OTHER


def is_wsl() -> bool:
    if platform.system().lower() != "linux":
        return False
    try:
        release = platform.release().lower()
        version = Path_read_text("/proc/version").lower()
    except OSError:
        return "microsoft" in platform.release().lower()
    return "microsoft" in release or "microsoft" in version or "wsl" in version


def Path_read_text(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8", errors="ignore")


def platform_info() -> PlatformInfo:
    return PlatformInfo(
        os=detect_os(),
        system=platform.system(),
        architecture=platform.machine(),
        python_version=".".join(map(str, sys.version_info[:3])),
        is_wsl=is_wsl(),
    )


def wsl_required() -> bool:
    """WSL is never required for Aichestra."""
    return False
