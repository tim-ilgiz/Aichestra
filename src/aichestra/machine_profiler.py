"""Deterministic machine profiler — OS/CPU/RAM/disk/GPU via system APIs, not LLMs."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aichestra.config.hardware_profiles import suggest_profile
from aichestra.platform_detect import platform_info


@dataclass(frozen=True)
class CpuInfo:
    name: str
    cores_logical: int
    cores_physical: int | None


@dataclass(frozen=True)
class MemoryInfo:
    total_bytes: int
    available_bytes: int | None

    @property
    def total_gb(self) -> float:
        return round(self.total_bytes / (1024**3), 2)


@dataclass(frozen=True)
class DiskInfo:
    path: str
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class GpuInfo:
    name: str
    vendor: str | None = None
    memory_bytes: int | None = None
    accelerator_kind: str | None = None


@dataclass(frozen=True)
class LocalAiStub:
    """Lightweight local-AI presence stub (full discovery lives in local_runtime)."""

    ollama_on_path: bool
    opencode_on_path: bool
    local_enabled_default: bool = False


@dataclass(frozen=True)
class MachineProfile:
    os: str
    system: str
    architecture: str
    python_version: str
    is_wsl: bool
    cpu: CpuInfo
    memory: MemoryInfo
    disks: tuple[DiskInfo, ...]
    gpus: tuple[GpuInfo, ...]
    local_ai: LocalAiStub
    suggested_profile_id: str
    suggested_model_class: str
    max_local_workers: int
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def profile_machine(*, root: Path | None = None) -> MachineProfile:
    """Collect deterministic hardware facts without LLM involvement."""
    info = platform_info()
    cpu = _cpu_info()
    memory = _memory_info()
    disks = _disk_info(root)
    gpus = _gpu_info()
    local_ai = _local_ai_stub()
    has_gpu = bool(gpus)
    suggestion = suggest_profile(memory.total_gb, has_gpu=has_gpu)
    return MachineProfile(
        os=info.os.value,
        system=info.system,
        architecture=info.architecture,
        python_version=info.python_version,
        is_wsl=info.is_wsl,
        cpu=cpu,
        memory=memory,
        disks=disks,
        gpus=gpus,
        local_ai=local_ai,
        suggested_profile_id=suggestion.id,
        suggested_model_class=suggestion.recommended_model_class,
        max_local_workers=suggestion.max_local_workers,
        extras={
            "hostname": platform.node(),
            "processor": platform.processor() or "",
        },
    )


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], *, timeout: float = 5.0) -> str:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")


def _cpu_info() -> CpuInfo:
    logical = os.cpu_count() or 1
    physical: int | None = None
    name = platform.processor() or platform.machine() or "unknown"
    system = platform.system().lower()
    if system == "darwin":
        brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if brand:
            name = brand
        phys = _run(["sysctl", "-n", "hw.physicalcpu"]).strip()
        if phys.isdigit():
            physical = int(phys)
    elif system == "linux":
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            models = re.findall(r"^model name\s*:\s*(.+)$", text, re.M)
            if models:
                name = models[0].strip()
            physical_ids = set(re.findall(r"^physical id\s*:\s*(.+)$", text, re.M))
            cores = re.findall(r"^cpu cores\s*:\s*(\d+)$", text, re.M)
            if physical_ids and cores:
                physical = len(physical_ids) * int(cores[0])
        except OSError:
            pass
    elif system == "windows":
        out = _run(
            ["wmic", "cpu", "get", "Name,NumberOfCores", "/format:list"],
            timeout=8.0,
        )
        match = re.search(r"Name=(.+)", out)
        if match:
            name = match.group(1).strip()
        cores = re.search(r"NumberOfCores=(\d+)", out)
        if cores:
            physical = int(cores.group(1))
    return CpuInfo(name=name, cores_logical=logical, cores_physical=physical)


def _memory_info() -> MemoryInfo:
    system = platform.system().lower()
    total = 0
    available: int | None = None
    if system == "darwin":
        raw = _run(["sysctl", "-n", "hw.memsize"]).strip()
        if raw.isdigit():
            total = int(raw)
        # vm_stat page size * free-ish pages is approximate; leave available optional.
    elif system == "linux":
        try:
            text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
            total_m = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.M)
            avail_m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", text, re.M)
            if total_m:
                total = int(total_m.group(1)) * 1024
            if avail_m:
                available = int(avail_m.group(1)) * 1024
        except OSError:
            pass
    elif system == "windows":
        out = _run(
            ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"],
            timeout=8.0,
        )
        match = re.search(r"TotalPhysicalMemory=(\d+)", out)
        if match:
            total = int(match.group(1))
    if total <= 0:
        # Last-resort: report 0 rather than inventing values.
        total = 0
    return MemoryInfo(total_bytes=total, available_bytes=available)


def _disk_info(root: Path | None) -> tuple[DiskInfo, ...]:
    targets: list[Path] = []
    if root is not None:
        targets.append(Path(root).resolve())
    targets.append(Path.cwd().resolve())
    home = Path.home()
    if home.exists():
        targets.append(home.resolve())
    seen: set[str] = set()
    disks: list[DiskInfo] = []
    for path in targets:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        disks.append(
            DiskInfo(
                path=str(path),
                total_bytes=usage.total,
                free_bytes=usage.free,
            )
        )
    return tuple(disks)


def _gpu_info() -> tuple[GpuInfo, ...]:
    system = platform.system().lower()
    gpus: list[GpuInfo] = []
    if system == "darwin":
        out = _run(["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"])
        for chip in re.findall(r"Chipset Model:\s*(.+)", out):
            name = chip.strip()
            kind = "apple_silicon" if "Apple" in name or "M" in name else "gpu"
            gpus.append(GpuInfo(name=name, vendor="Apple", accelerator_kind=kind))
        if not gpus and platform.machine().lower() in {"arm64", "aarch64"}:
            gpus.append(
                GpuInfo(
                    name="Apple Silicon GPU",
                    vendor="Apple",
                    accelerator_kind="apple_silicon",
                )
            )
    elif system == "linux":
        if _which("nvidia-smi"):
            out = _run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ]
            )
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if not parts or not parts[0]:
                    continue
                mem = None
                if len(parts) > 1 and parts[1].isdigit():
                    mem = int(parts[1]) * 1024 * 1024
                gpus.append(
                    GpuInfo(
                        name=parts[0],
                        vendor="NVIDIA",
                        memory_bytes=mem,
                        accelerator_kind="cuda",
                    )
                )
        try:
            drm = Path("/sys/class/drm")
            if drm.is_dir():
                for entry in sorted(drm.iterdir()):
                    if not entry.name.startswith("card") or "-" in entry.name:
                        continue
                    vendor_path = entry / "device" / "vendor"
                    if vendor_path.is_file():
                        vendor = vendor_path.read_text(encoding="utf-8").strip()
                        gpus.append(
                            GpuInfo(
                                name=f"drm:{entry.name}",
                                vendor=vendor,
                                accelerator_kind="drm",
                            )
                        )
        except OSError:
            pass
    elif system == "windows":
        out = _run(["wmic", "path", "win32_VideoController", "get", "Name", "/value"])
        for match in re.findall(r"Name=(.+)", out):
            name = match.strip()
            if name:
                gpus.append(GpuInfo(name=name, accelerator_kind="gpu"))
    # Deduplicate by name
    unique: dict[str, GpuInfo] = {g.name: g for g in gpus}
    return tuple(unique.values())


def _local_ai_stub() -> LocalAiStub:
    return LocalAiStub(
        ollama_on_path=_which("ollama") is not None,
        opencode_on_path=_which("opencode") is not None,
        local_enabled_default=False,
    )
