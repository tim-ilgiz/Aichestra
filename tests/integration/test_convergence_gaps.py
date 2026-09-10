"""Fake-provider env, research provider labeling, bootstrap approve persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

from aichestra.bootstrap.core import bootstrap
from aichestra.config.layering import load_json, machine_local_path
from aichestra.orchestration.research_compact import research_paths
from aichestra.providers.discovery import discover_providers_report


def test_fake_providers_env_honored(monkeypatch) -> None:
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    report = discover_providers_report(local_enabled=False)
    assert report["fake_providers"] is True
    assert report["providers"]["codex"]["available"] is True
    assert report["providers"]["codex"]["metadata"].get("fake") is True


def test_force_real_bypasses_fakes(monkeypatch) -> None:
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    report = discover_providers_report(local_enabled=False, force_real=True)
    assert report["fake_providers"] is False


def test_research_provider_labels_filesystem_when_local_absent(
    fixture_project_a: Path,
) -> None:
    summary = research_paths(
        fixture_project_a,
        prefer_local_worker=True,
        local_worker_available=False,
    )
    assert summary.PROVIDER == "filesystem"


def test_research_does_not_claim_local_without_invocation(
    fixture_project_a: Path,
) -> None:
    summary = research_paths(
        fixture_project_a,
        prefer_local_worker=True,
        local_worker_available=True,
    )
    # Availability alone must not label PROVIDER=local-worker (FR-053).
    assert summary.PROVIDER == "filesystem"


def test_research_labels_local_when_runner_succeeds(
    fixture_project_a: Path,
) -> None:
    from aichestra.providers.base import FailureClass, ProviderTaskResult

    def runner(_root: Path, query: str) -> ProviderTaskResult:
        return ProviderTaskResult(
            ok=True,
            output=f"worker found clues for {query}",
            failure=FailureClass.NONE,
            detail="ok",
        )

    summary = research_paths(
        fixture_project_a,
        query="auth",
        prefer_local_worker=True,
        local_research_runner=runner,
    )
    assert summary.PROVIDER == "local-worker"
    assert summary.QUERY == "auth"
    assert "auth" in summary.SUMMARY


def test_approve_model_download_persists_on_rebootstrap(
    fake_aichestra_root: Path,
) -> None:
    first = bootstrap(repo_root=fake_aichestra_root, enable_local=False)
    assert first.created_machine_local is True
    ml = machine_local_path(fake_aichestra_root)
    data = load_json(ml)
    assert data.get("notes", {}).get("approve_model_download") is False

    second = bootstrap(
        repo_root=fake_aichestra_root,
        approve_model_download=True,
    )
    assert second.preserved_machine_local is True
    assert "persist_approve_model_download" in second.actions
    data2 = json.loads(ml.read_text(encoding="utf-8"))
    assert data2["notes"]["approve_model_download"] is True


def test_resource_pressure_disables_local_worker() -> None:
    from aichestra.providers.local_worker import LocalWorkerProvider

    status = LocalWorkerProvider(
        local_enabled=True,
        memory_total_gb=24.0,
        memory_available_gb=1.0,
    ).probe()
    assert status.available is False
    assert "pressure" in status.detail.lower()
    assert status.metadata["resources"]["policy"]["may_terminate_user_apps"] is False
