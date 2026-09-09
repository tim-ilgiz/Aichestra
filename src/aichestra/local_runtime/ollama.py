"""Ollama local runtime adapter."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

from aichestra.local_runtime.base import (
    LocalModel,
    LocalRuntime,
    ModelCapability,
    RuntimeCapabilities,
)

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

_VISION_HINTS = (
    "llava",
    "vision",
    "bakllava",
    "moondream",
    "minicpm-v",
    "qwen2-vl",
    "qwen2.5-vl",
)


class OllamaRuntime(LocalRuntime):
    name = "ollama"

    def __init__(self, host: str | None = None, *, binary: str | None = None) -> None:
        self.host = (host or DEFAULT_OLLAMA_HOST).rstrip("/")
        self.binary = binary or shutil.which("ollama")

    def is_available(self) -> bool:
        if self.binary:
            return True
        return self._probe_api()

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            can_list_models=True,
            can_unload_idle=True,
            supports_custom_endpoint=True,
        )

    def list_models(self) -> list[LocalModel]:
        models = self._list_via_api()
        if models is not None:
            return models
        return self._list_via_cli()

    def unload_idle(self) -> bool:
        """Request Ollama to stop loaded models (best-effort; never kills user apps)."""
        # Prefer API stop-all via listing running models when available.
        stopped = self._stop_running_via_api()
        if stopped is not None:
            return stopped
        if not self.binary:
            return False
        try:
            # `ollama stop` without args is unsupported on some versions;
            # list running then stop each — degrade gracefully.
            completed = subprocess.run(
                [self.binary, "ps"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if completed.returncode != 0:
            return False
        lines = (completed.stdout or "").splitlines()
        ok = True
        for line in lines[1:]:
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            try:
                stop = subprocess.run(
                    [self.binary, "stop", name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired):
                ok = False
                continue
            if stop.returncode != 0:
                ok = False
        return ok

    def _stop_running_via_api(self) -> bool | None:
        try:
            with urllib.request.urlopen(f"{self.host}/api/ps", timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        models = payload.get("models") or []
        if not models:
            return True
        ok = True
        for item in models:
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name:
                continue
            body = json.dumps({"model": name, "keep_alive": 0}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.host}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if getattr(resp, "status", 200) >= 400:
                        ok = False
            except (urllib.error.URLError, TimeoutError, OSError):
                ok = False
        return ok

    def _probe_api(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=2) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _list_via_api(self) -> list[LocalModel] | None:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        return [_model_from_ollama(item) for item in payload.get("models", [])]

    def _list_via_cli(self) -> list[LocalModel]:
        if not self.binary:
            return []
        try:
            completed = subprocess.run(
                [self.binary, "list"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        models: list[LocalModel] = []
        lines = (completed.stdout or "").splitlines()
        for line in lines[1:]:  # skip header
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            size = parts[1] if len(parts) > 1 else None
            models.append(
                LocalModel(
                    id=name,
                    name=name,
                    runtime=self.name,
                    capabilities=_infer_capabilities(name),
                    parameter_size=size,
                    installed=True,
                )
            )
        return models


def _model_from_ollama(item: dict[str, Any]) -> LocalModel:
    name = str(item.get("name") or item.get("model") or "unknown")
    details = item.get("details") or {}
    size = details.get("parameter_size") or item.get("size")
    if isinstance(size, int):
        parameter_size = f"{size}"
    else:
        parameter_size = str(size) if size is not None else None
    return LocalModel(
        id=name,
        name=name,
        runtime="ollama",
        capabilities=_infer_capabilities(name, details),
        parameter_size=parameter_size,
        installed=True,
        metadata={"digest": item.get("digest")},
    )


def _infer_capabilities(
    name: str, details: dict[str, Any] | None = None
) -> frozenset[ModelCapability]:
    lower = name.lower()
    caps = {ModelCapability.TEXT, ModelCapability.CODE}
    family = ""
    if details:
        family = str(details.get("family") or details.get("families") or "").lower()
    blob = f"{lower} {family}"
    if any(hint in blob for hint in _VISION_HINTS):
        caps.add(ModelCapability.VISION)
    if "embed" in lower:
        return frozenset({ModelCapability.EMBEDDING})
    return frozenset(caps)


def parse_parameter_billions(size: str | None) -> float | None:
    if not size:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*[Bb]", size)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)", size)
    if match and "b" in size.lower():
        return float(match.group(1))
    return None
