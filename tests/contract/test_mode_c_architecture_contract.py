"""Architecture contract: Mode C single-Orca-Run invariants (MODE-C-001–010).

These tests lock the *target* architecture. They must not encode Orca-less
Mode C, direct lead fallback, or project_root=None continuation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aichestra.config.layering import provider_enabled, resolve_config
from aichestra.orchestration.modes import (
    Mode,
    intercepts_native_cli,
    starts_full_orchestration,
)
from aichestra.orchestration.workflow import (
    ModeCRunController,
    Phase,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import FailureClass, ProviderKind
from aichestra.providers.codex import CodexProvider
from aichestra.providers.cursor import CursorProvider
from aichestra.providers.discovery import discover_providers
from tests.fakes.providers import fake_codex, fake_cursor, fake_local_worker, fake_orca


def _run_create_count(orca) -> int:
    """Count Mode C Orca Run creations (`ensure_run` → orchestration run-create)."""
    return sum(1 for req in orca.sent if (req.role or "").strip().lower() == "ensure_run")


def _small_bindings(
    tmp_path: Path,
    *,
    orca=None,
    lead=None,
    local_worker=None,
    project_root: str | None | object = ...,
    attachments: tuple[str, ...] = (),
    task_prompt: str = "fix one-line typo",
    research_query: str = "",
) -> WorkflowBindings:
    root: str | None
    if project_root is ...:
        root = str(tmp_path)
    else:
        root = project_root  # type: ignore[assignment]
    return WorkflowBindings(
        orca=orca,
        lead=lead if lead is not None else fake_codex("success"),
        local_worker=local_worker,
        project_root=root,
        task_prompt=task_prompt,
        research_query=research_query,
        attachments=attachments,
        maintenance_kwargs={"change_summary": "typo", "touches_behavior": False},
        verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
    )


def test_mode_c_requires_orca(tmp_path: Path) -> None:
    """MODE-C-001 / MODE-C-006: Mode C fails closed without Orca."""
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=None, lead=lead),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "Orca" in outcome.detail
    assert outcome.result.get("failure") == FailureClass.UNAVAILABLE.value
    assert lead.sent == []


def test_mode_c_requires_project_root() -> None:
    """MODE-C-005: unresolved project root fails before orchestration."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            project_root=None,
            task_prompt="should not run",
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    detail = outcome.detail.lower()
    assert "project-root" in detail or "project root" in detail
    assert orca.sent == []
    assert lead.sent == []


def test_mode_c_creates_exactly_one_orca_run(tmp_path: Path) -> None:
    """MODE-C-002: one Mode C invocation → exactly one run-create / ensure_run."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            lead=lead,
            local_worker=fake_local_worker("success"),
            research_query="README",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert _run_create_count(orca) == 1


def test_mode_c_never_directly_executes_codex(tmp_path: Path) -> None:
    """MODE-C-004: Codex adapter must not receive Mode C execute_task traffic."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=orca, lead=lead),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert lead.sent == []


def test_mode_c_never_directly_executes_cursor(tmp_path: Path) -> None:
    """MODE-C-004: Cursor adapter must not receive Mode C execute_task traffic."""
    orca = fake_orca("success")
    cursor = fake_cursor("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            lead=cursor,
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert cursor.sent == []


def test_mode_c_never_directly_executes_local_worker(tmp_path: Path) -> None:
    """MODE-C-004: local-worker must not be scheduled directly by Aichestra."""
    orca = fake_orca("success")
    worker = fake_local_worker("success")
    # MEDIUM prompt keeps research on the Mode C schedule.
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            lead=fake_codex("success"),
            local_worker=worker,
            task_prompt="refactor across 12 files in multi-package monorepo",
            research_query="README",
        ),
    )
    outcome = wf.run_phase()  # classify
    assert outcome is not None and outcome.status is PhaseStatus.SUCCEEDED
    wf.state.metadata["brief_satisfied"] = True
    if wf.state.metadata.get("plan_required"):
        wf.state.metadata["plan_satisfied"] = True
    state = wf.run_all()
    assert not state.failed, state.failed
    assert worker.sent == []
    assert any((r.role or "") == "research" for r in orca.sent)


def test_all_mode_c_tasks_use_same_orca_run(tmp_path: Path) -> None:
    """MODE-C-007: all Mode C agent tasks share one run_id."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            lead=fake_codex("success"),
            local_worker=fake_local_worker("success"),
            task_prompt="refactor across 12 files in multi-package monorepo",
            research_query="README",
        ),
    )
    outcome = wf.run_phase()  # classify
    assert outcome is not None and outcome.status is PhaseStatus.SUCCEEDED
    wf.state.metadata["brief_satisfied"] = True
    if wf.state.metadata.get("plan_required"):
        wf.state.metadata["plan_satisfied"] = True
    state = wf.run_all()
    assert not state.failed, state.failed
    run_id = state.metadata.get("orca_run_id")
    assert run_id
    agent_reqs = [
        req
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
    ]
    assert agent_reqs
    assert all((req.context or {}).get("run_id") == run_id for req in agent_reqs)


def test_mode_a_allows_direct_codex() -> None:
    """MODE-C-008: Mode A keeps direct Codex usage; never intercepts native CLI."""
    assert starts_full_orchestration(Mode.NATIVE) is False
    assert intercepts_native_cli(Mode.NATIVE) is False
    status = CodexProvider().probe()
    assert status.kind is ProviderKind.CODEX
    assert status.intercepts_native_cli is False


def test_mode_a_allows_direct_cursor() -> None:
    """MODE-C-008: Mode A keeps direct Cursor usage; never intercepts native CLI."""
    assert starts_full_orchestration(Mode.NATIVE) is False
    assert intercepts_native_cli(Mode.NATIVE) is False
    status = CursorProvider().probe()
    assert status.kind is ProviderKind.CURSOR
    assert status.intercepts_native_cli is False


def test_orca_unavailable_does_not_fallback_directly_to_lead(tmp_path: Path) -> None:
    """MODE-C-006: Orca UNAVAILABLE → Mode C FAIL; Codex execute_task not called."""
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=fake_orca("unavailable"),
            lead=lead,
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert lead.sent == []
    assert Phase.LEAD_IMPLEMENT.value not in wf.state.completed


def test_provider_can_be_independently_disabled(tmp_path: Path) -> None:
    """MODE-C-009: Codex/Cursor/local-worker enabled flags are independent."""
    root = tmp_path / "repo"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "defaults.json").write_text(
        json.dumps(
            {
                "local": {"enabled": False},
                "providers": {
                    "codex": {"enabled": False},
                    "cursor": {"enabled": True},
                    "local-worker": {"enabled": False},
                    "orca": {"enabled": True},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = resolve_config(repo_root=root)
    assert provider_enabled(cfg, "codex") is False
    assert provider_enabled(cfg, "cursor") is True
    assert provider_enabled(cfg, "local-worker") is False


def test_attachments_are_forwarded_to_orca(tmp_path: Path) -> None:
    """MODE-C-010: --attach paths must reach Orca task requests as attachments."""
    img = tmp_path / "screenshot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    log = tmp_path / "log.txt"
    log.write_text("error line\n", encoding="utf-8")
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            lead=lead,
            attachments=(str(img), str(log)),
        ),
    )
    classify = wf.run_phase()
    assert classify is not None and classify.status is PhaseStatus.SUCCEEDED
    implement = wf.run_phase()
    assert implement is not None and implement.status is PhaseStatus.SUCCEEDED
    impl_req = next(r for r in orca.sent if (r.role or "") == "lead_implement")
    assert impl_req.attachments
    assert len(impl_req.attachments) >= 1
    assert all(Path(p).exists() for p in impl_req.attachments)
    assert lead.sent == []


def test_discover_respects_independent_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODE-C-009 companion: discovery honors enabled map."""
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    statuses = discover_providers(
        local_enabled=False,
        enabled={"orca": True, "codex": False, "cursor": True, "local-worker": False},
    )
    by_kind = {s.kind: s for s in statuses}
    assert by_kind[ProviderKind.CODEX].available is False
    assert by_kind[ProviderKind.CURSOR].available is True
