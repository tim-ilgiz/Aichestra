"""Regression: layered local policy reaches discovery / research / selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aichestra.local_runtime.base import LocalModel, ModelCapability
from aichestra.local_runtime.model_selector import select_model
from aichestra.orchestration.research_compact import research_paths
from aichestra.providers.base import (
    FailureClass,
    ProviderKind,
    ProviderRole,
    ProviderStatus,
)
from aichestra.providers.discovery import discover_providers
from aichestra.providers.local_worker import LocalWorkerProvider


def test_select_model_reason_when_allowed_policy_filters_all() -> None:
    models = [
        LocalModel(
            id="alpha",
            name="alpha",
            runtime="ollama",
            capabilities=frozenset({ModelCapability.TEXT}),
            parameter_size="7B",
        ),
        LocalModel(
            id="beta",
            name="beta",
            runtime="ollama",
            capabilities=frozenset({ModelCapability.TEXT}),
            parameter_size="14B",
        ),
    ]
    sel = select_model(
        models,
        local_enabled=True,
        allowed_ids=["gamma"],
    )
    assert sel.model is None
    assert sel.axes["CAPABLE"] is True
    assert sel.axes["ALLOWED"] is False
    assert sel.reason == "no capable model allowed by policy"


def test_discover_providers_forwards_project_local_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingLocalWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                kind=ProviderKind.LOCAL_WORKER,
                available=False,
                role=ProviderRole.WORKER,
                failure=FailureClass.UNAVAILABLE,
                detail="capture-only",
                intercepts_native_cli=False,
            )

    class StubProvider:
        kind = ProviderKind.ORCA

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.CONTROL_PLANE,
                failure=FailureClass.UNAVAILABLE,
                detail="stub",
                intercepts_native_cli=False,
            )

    class StubCodex(StubProvider):
        kind = ProviderKind.CODEX

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail="stub",
                intercepts_native_cli=False,
            )

    class StubCursor(StubProvider):
        kind = ProviderKind.CURSOR

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.FALLBACK_LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail="stub",
                intercepts_native_cli=False,
            )

    monkeypatch.setattr(
        "aichestra.providers.discovery.LocalWorkerProvider", CapturingLocalWorker
    )
    monkeypatch.setattr("aichestra.providers.discovery.OrcaProvider", StubProvider)
    monkeypatch.setattr("aichestra.providers.discovery.CodexProvider", StubCodex)
    monkeypatch.setattr("aichestra.providers.discovery.CursorProvider", StubCursor)

    cfg = {
        "local": {
            "enabled": True,
            "allowed_models": ["qwen2.5:14b"],
            "preferred_models": ["qwen2.5:14b"],
            "max_parameter_billions": 32,
            "ollama_host": "http://127.0.0.1:11434",
        }
    }
    discover_providers(
        local_enabled=True,
        ollama_host="http://127.0.0.1:11434",
        force_real=True,
        config=cfg,
    )

    assert captured.get("config") is cfg
    # Same construction path Mode C / orchestrate uses after discover_providers.
    real = LocalWorkerProvider(local_enabled=True, config=captured["config"])
    assert real.allowed_ids == ["qwen2.5:14b"]
    assert real.preferred_ids == ["qwen2.5:14b"]
    assert real.max_parameter_billions == 32.0


def test_research_paths_uses_caller_config_not_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_b = tmp_path / "project-B"
    project_b.mkdir()
    (project_b / "README.md").write_text("# project B\n", encoding="utf-8")

    captured: dict[str, Any] = {}

    class CapturingLocalWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.clear()
            captured.update(kwargs)

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                kind=ProviderKind.LOCAL_WORKER,
                available=False,
                role=ProviderRole.WORKER,
                failure=FailureClass.UNAVAILABLE,
                detail="capture-only",
                intercepts_native_cli=False,
            )

    def boom_resolve(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError(f"ambient resolve_config must not run: {kwargs}")

    monkeypatch.setattr(
        "aichestra.providers.local_worker.LocalWorkerProvider", CapturingLocalWorker
    )
    monkeypatch.setattr("aichestra.config.layering.resolve_config", boom_resolve)

    cfg_b = {
        "project_id": "project-B",
        "local": {
            "enabled": True,
            "allowed_models": ["only-for-b"],
            "max_parameter_billions": 8,
        },
    }
    summary = research_paths(
        project_b,
        prefer_local_worker=True,
        local_worker_available=None,
        config=cfg_b,
    )
    assert summary.PROVIDER == "filesystem"
    assert captured.get("config") is cfg_b
    assert captured["config"]["local"]["allowed_models"] == ["only-for-b"]


def test_research_paths_fallback_resolve_uses_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_b = tmp_path / "project-B"
    project_b.mkdir()
    (project_b / "README.md").write_text("# project B\n", encoding="utf-8")

    resolve_calls: list[dict[str, Any]] = []

    def fake_resolve(**kwargs: Any) -> dict[str, Any]:
        resolve_calls.append(kwargs)
        return {
            "project_id": "project-B",
            "local": {"enabled": False},
        }

    class CapturingLocalWorker:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def probe(self) -> ProviderStatus:
            return ProviderStatus(
                kind=ProviderKind.LOCAL_WORKER,
                available=False,
                role=ProviderRole.WORKER,
                failure=FailureClass.UNAVAILABLE,
                detail="capture-only",
                intercepts_native_cli=False,
            )

    monkeypatch.setattr("aichestra.config.layering.resolve_config", fake_resolve)
    monkeypatch.setattr(
        "aichestra.providers.local_worker.LocalWorkerProvider", CapturingLocalWorker
    )

    summary = research_paths(
        project_b,
        prefer_local_worker=True,
        local_worker_available=None,
        config=None,
    )
    assert summary.PROVIDER == "filesystem"
    assert resolve_calls
    assert resolve_calls[0].get("project_root") == project_b


def test_orchestrate_passes_layered_config_into_discover_providers() -> None:
    """Mode C CLI must not re-resolve ambient config inside discover_providers."""
    import inspect

    from aichestra.cli import _cmd_orchestrate

    src = inspect.getsource(_cmd_orchestrate)
    assert "discover_providers(" in src
    assert "config=cfg" in src
