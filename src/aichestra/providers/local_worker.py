"""Optional local-worker = OpenCode + local runtime (initially Ollama).

Launches are always pinned to an installed local model and local endpoint so a
cloud OpenCode default cannot be labeled as local-worker output.
"""

from __future__ import annotations

import json
from typing import Any
from aichestra.local_runtime.base import ModelCapability
from aichestra.local_runtime.discovery import discover_local_runtimes
from aichestra.local_runtime.model_selector import select_model
from aichestra.local_runtime.ollama import DEFAULT_OLLAMA_HOST, OllamaRuntime
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
    ProviderSession,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
    probe_version,
    which_binary,
)
from aichestra.providers.execution import run_cli_task

_OPENCODE_BINARIES = ("opencode",)
_LOCAL_PROVIDER_ID = "ollama"


def _openai_compatible_base(host: str) -> str:
    base = host.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def build_local_opencode_argv(
    binary: str,
    *,
    model_ref: str,
    prompt: str,
    cwd: str | None = None,
) -> list[str]:
    """Build OpenCode argv pinned to an explicit local model id."""
    argv = [binary, "run", "--model", model_ref, prompt]
    if cwd:
        argv.extend(["--dir", cwd])
    return argv


def build_local_opencode_config(
    *,
    model_id: str,
    ollama_host: str,
) -> dict[str, Any]:
    """Inline OpenCode config that forces the Ollama-compatible local endpoint."""
    base_url = _openai_compatible_base(ollama_host)
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{_LOCAL_PROVIDER_ID}/{model_id}",
        "provider": {
            _LOCAL_PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (Aichestra local-worker)",
                "options": {"baseURL": base_url},
                "models": {model_id: {"name": model_id}},
            }
        },
    }


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
        self.ollama_host = ollama_host or DEFAULT_OLLAMA_HOST
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

        selection = self._select_local_model()
        model_ok = selection is not None and selection.ok and selection.model is not None
        available = bool(opencode) and runtime_ok and model_ok
        detail_parts = []
        if opencode:
            detail_parts.append("OpenCode present")
        else:
            detail_parts.append("OpenCode missing")
        if runtime_ok:
            detail_parts.append("local runtime present")
        else:
            detail_parts.append("local runtime missing")
        if model_ok and selection and selection.model:
            detail_parts.append(f"model={selection.model.id}")
        else:
            detail_parts.append(
                selection.reason if selection else "no local model selected"
            )

        meta: dict[str, Any] = {
            "local_enabled": True,
            "runtimes": [r.name for r in runtimes if r.is_available()],
            "ollama_host": self.ollama_host,
        }
        if selection and selection.model:
            meta["selected_model"] = selection.model.id
            meta["model_ref"] = f"{_LOCAL_PROVIDER_ID}/{selection.model.id}"
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

    def _select_local_model(self):
        runtime = OllamaRuntime(host=self.ollama_host)
        models = runtime.list_models() if runtime.is_available() else []
        return select_model(
            models,
            required_capability=ModelCapability.TEXT,
            local_enabled=True,
        )

    def send(
        self,
        session: ProviderSession,
        request: ProviderTaskRequest,
    ) -> ProviderTaskResult:
        """Run a bounded OpenCode task pinned to a local Ollama model/endpoint."""
        status = self.probe()
        if not status.available or not status.binary_path:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=status.detail or "local-worker unavailable",
                session_id=session.session_id,
            )

        selection = self._select_local_model()
        if selection is None or not selection.ok or selection.model is None:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.UNAVAILABLE,
                detail=(
                    selection.reason
                    if selection
                    else "no installed local model for local-worker"
                ),
                session_id=session.session_id,
            )

        model_id = selection.model.id
        model_ref = f"{_LOCAL_PROVIDER_ID}/{model_id}"
        prompt = request.bounded_prompt()
        argv = build_local_opencode_argv(
            status.binary_path,
            model_ref=model_ref,
            prompt=prompt,
            cwd=request.cwd,
        )
        config = build_local_opencode_config(
            model_id=model_id,
            ollama_host=self.ollama_host,
        )
        env = {
            "OPENCODE_CONFIG_CONTENT": json.dumps(config),
        }
        result = run_cli_task(
            binary=status.binary_path,
            argv=argv,
            session=session,
            request=request,
            unavailable_detail="OpenCode binary unavailable",
            env=env,
        )
        meta = dict(result.metadata)
        meta.update(
            {
                "local_worker": True,
                "model_ref": model_ref,
                "ollama_host": self.ollama_host,
                "base_url": _openai_compatible_base(self.ollama_host),
            }
        )
        return ProviderTaskResult(
            ok=result.ok,
            output=result.output,
            failure=result.failure,
            detail=result.detail,
            session_id=result.session_id,
            metadata=meta,
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
