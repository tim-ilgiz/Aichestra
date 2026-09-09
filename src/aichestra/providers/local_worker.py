"""Optional local-worker = OpenCode + local runtime (initially Ollama)."""

from __future__ import annotations

from typing import Any

from aichestra.local_runtime.discovery import discover_local_runtimes
from aichestra.local_runtime.resources import (
    ResourceAssessment,
    ResourcePressure,
    assess_resources,
)
from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderStatus,
    probe_version,
    which_binary,
)

_OPENCODE_BINARIES = ("opencode",)


class LocalWorkerProvider(ProviderAdapter):
    kind = ProviderKind.LOCAL_WORKER

    def __init__(
        self,
        *,
        local_enabled: bool = False,
        ollama_host: str | None = None,
        memory_total_gb: float | None = None,
        memory_available_gb: float | None = None,
    ) -> None:
        self.local_enabled = local_enabled
        self.ollama_host = ollama_host
        self.memory_total_gb = memory_total_gb
        self.memory_available_gb = memory_available_gb

    def probe(self) -> ProviderStatus:
        if not self.local_enabled:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.WORKER,
                failure=FailureClass.UNAVAILABLE,
                detail="local.enabled=false; local-worker optional and skipped",
                intercepts_native_cli=False,
                metadata={"local_enabled": False},
            )

        assessment = self._assess()
        if assessment and not assessment.local_recommended:
            self._unload_under_pressure(assessment)
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.WORKER,
                failure=FailureClass.UNAVAILABLE,
                detail=(
                    f"local-worker disabled under resource pressure "
                    f"({assessment.pressure.value})"
                ),
                intercepts_native_cli=False,
                metadata={
                    "local_enabled": True,
                    "resources": assessment.to_dict(),
                },
            )

        opencode = which_binary(_OPENCODE_BINARIES)
        runtimes = discover_local_runtimes(ollama_host=self.ollama_host)
        runtime_ok = any(r.is_available() for r in runtimes)
        if not opencode and not runtime_ok:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.WORKER,
                failure=FailureClass.UNAVAILABLE,
                detail="OpenCode and local runtime both unavailable",
                intercepts_native_cli=False,
                metadata={"local_enabled": True},
            )

        available = bool(opencode) and runtime_ok
        detail_parts = []
        if opencode:
            detail_parts.append("OpenCode present")
        else:
            detail_parts.append("OpenCode missing")
        if runtime_ok:
            detail_parts.append("local runtime present")
        else:
            detail_parts.append("local runtime missing")

        meta: dict[str, Any] = {
            "local_enabled": True,
            "runtimes": [r.name for r in runtimes if r.is_available()],
        }
        if assessment:
            meta["resources"] = assessment.to_dict()

        return ProviderStatus(
            kind=self.kind,
            available=available,
            role=ProviderRole.WORKER,
            binary_path=opencode,
            version=probe_version(opencode) if opencode else None,
            failure=FailureClass.NONE if available else FailureClass.UNAVAILABLE,
            detail="; ".join(detail_parts),
            intercepts_native_cli=False,
            metadata=meta,
        )

    def _assess(self) -> ResourceAssessment | None:
        total = self.memory_total_gb
        if total is None:
            try:
                from aichestra.machine_profiler import profile_machine

                profile = profile_machine()
                total = profile.memory.total_gb
                available = None
                if profile.memory.available_bytes is not None:
                    available = profile.memory.available_bytes / (1024**3)
                self.memory_available_gb = available
            except Exception:
                return None
        return assess_resources(
            memory_total_gb=float(total),
            memory_available_gb=self.memory_available_gb,
        )

    def _unload_under_pressure(self, assessment: ResourceAssessment) -> None:
        if assessment.pressure not in {
            ResourcePressure.HIGH,
            ResourcePressure.CRITICAL,
        }:
            return
        if "unload_idle_local_models" not in assessment.actions:
            return
        for runtime in discover_local_runtimes(ollama_host=self.ollama_host):
            if runtime.is_available() and runtime.capabilities().can_unload_idle:
                try:
                    runtime.unload_idle()
                except Exception:
                    continue
