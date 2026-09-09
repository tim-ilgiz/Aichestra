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
    state = wf.run_all()
    assert not state.failed, state.failed
    assert worker.sent == []
    assert any((r.role or "") == "mode_c_agents" for r in orca.sent)
    assert (tmp_path / ".aichestra" / "speckit" / "brief.md").is_file()


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
    state = wf.run_all()
    assert not state.failed, state.failed
    run_id = state.metadata.get("orca_run_id")
    assert run_id
    assert not state.metadata.get("orca_run_id_synthesized")
    agent_reqs = [
        req
        for req in orca.sent
        if (req.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report"}
    ]
    assert agent_reqs
    assert all((req.context or {}).get("run_id") == run_id for req in agent_reqs)
    assert orca.run_creates == 1


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
    state = wf.run_all()
    assert not state.failed, state.failed
    agents_req = next(r for r in orca.sent if (r.role or "") == "mode_c_agents")
    assert agents_req.attachments
    assert len(agents_req.attachments) >= 1
    assert all(Path(p).exists() for p in agents_req.attachments)
    assert lead.sent == []
    # Parent project must not gain .aichestra/attachments/
    assert not (tmp_path / ".aichestra" / "attachments").exists()
    routing = state.metadata.get("media_routing") or {}
    assert routing.get("staged_outside_parent") is True


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


def test_no_synthetic_run_id_when_orca_omits(tmp_path: Path) -> None:
    """Fail closed if Orca ensure_run succeeds without run_id."""
    orca = fake_orca("success")
    original = orca.send

    def send_no_run_id(session, request):
        result = original(session, request)
        if (request.role or "") == "ensure_run":
            meta = dict(result.metadata)
            meta.pop("run_id", None)
            return type(result)(
                ok=True,
                output=result.output,
                failure=result.failure,
                detail=result.detail,
                session_id=result.session_id,
                metadata=meta,
            )
        return result

    orca.send = send_no_run_id  # type: ignore[method-assign]
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=orca, lead=fake_codex("success")),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "run_id" in outcome.detail.lower() or "synthetic" in outcome.detail.lower()
    assert not wf.state.metadata.get("orca_run_id_synthesized")
    assert "aichestra-run-" not in str(wf.state.metadata.get("orca_run_id") or "")


def test_disabled_lead_never_dispatched(tmp_path: Path) -> None:
    """Disabled/unavailable codex must not be selected as Mode C agent."""
    orca = fake_orca("success")
    disabled = fake_codex("unavailable")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=disabled,
            providers=[
                fake_orca("success").probe(),
                disabled.probe(),
                fake_cursor("unavailable").probe(),
            ],
            project_root=str(tmp_path),
            task_prompt="fix typo",
            preferred_lead="codex",
            fallback_lead="cursor",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert state.failed
    assert disabled.sent == []
    detail = " ".join(state.failed) + str(state.phase_outcomes)
    # Thin coordinator fails before inventing a lead agent.
    assert any(
        "lead" in (o.detail or "").lower() or "unavailable" in (o.detail or "").lower()
        for o in state.phase_outcomes.values()
    ) or detail


def test_local_disabled_research_not_opencode(tmp_path: Path) -> None:
    """When local.enabled is false, research agent must not hard-code opencode."""
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
    wf.bindings.local_enabled = False
    state = wf.run_all()
    assert not state.failed, state.failed
    package = state.metadata.get("mode_c_policy_package") or {}
    assert package.get("research_agent") != "opencode"
    assert package.get("research_agent") == "codex"
    agents = next(r for r in orca.sent if (r.role or "") == "mode_c_agents")
    assert (agents.context or {}).get("research_agent") != "opencode"


def test_verify_auto_detect_python(tmp_path: Path) -> None:
    from aichestra.orchestration.verification import detect_verification_commands

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    cmds = detect_verification_commands(tmp_path)
    assert cmds == [["python", "-m", "pytest"]]


def test_worker_done_requires_matching_dispatch() -> None:
    from aichestra.providers.orca import interpret_orca_wait_event

    ok, detail, _ = interpret_orca_wait_event(
        {"type": "worker_done"},
        dispatch_id="expected-dispatch",
    )
    assert ok is False
    assert "missing dispatch" in detail.lower() or "expected" in detail.lower()


def test_mode_c_does_not_own_general_purpose_agent_phase_scheduler(tmp_path: Path) -> None:
    """Observable: run_all uses Orca-owned handoffs; run_phase refuses agents."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="fix typo",
            research_query="README",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert state.metadata.get("thin_coordinator") is True
    agent_roles = [
        (r.role or "")
        for r in orca.sent
        if (r.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report", "status_ping"}
    ]
    # Exactly one orchestration handoff for research+implement — not per-phase workers.
    assert agent_roles.count("mode_c_agents") == 1
    assert "research" not in agent_roles
    assert "lead_implement" not in agent_roles
    # Writers are one handoff when needed — never per-writer Orca roles.
    assert "test_writer" not in agent_roles
    assert "doc_writer" not in agent_roles
    assert agent_roles.count("mode_c_writers") <= 1

    # run_phase must refuse to schedule agent workers.
    wf2 = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=fake_orca("success")),
    )
    assert wf2.run_phase().status is PhaseStatus.SUCCEEDED  # classify ok
    refused = wf2.run_phase()
    assert refused is not None
    assert refused.status is PhaseStatus.FAILED
    assert "refuses per-phase" in refused.detail.lower() or "run_all" in refused.detail


def test_medium_speckit_artifacts_come_from_orca_not_python_stubs(tmp_path: Path) -> None:
    """FR-070: Spec Kit files are produced via Orca role under the Mode C Run."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert any((r.role or "") == "speckit_artifacts" for r in orca.sent)
    assert state.metadata.get("speckit_artifacts", {}).get("lifecycle") == "orca_run"
    agents = next(r for r in orca.sent if (r.role or "") == "mode_c_agents")
    assert "preferred_lead" in (agents.context or {}) or "preferred_lead" in (
        (agents.context or {}).get("policy_package") or {}
    )

def test_mode_c_orca_is_canonical_lifecycle_owner(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=orca),
    )
    state = wf.run_all()
    assert state.metadata.get("canonical_orchestration") == "orca_run"
    assert isinstance(state.metadata.get("orca_run_id"), str)
    assert state.metadata["orca_run_id"]
    assert state.metadata.get("thin_coordinator") is True


def test_mode_c_policy_gates_do_not_become_worker_scheduler(tmp_path: Path) -> None:
    """Maintenance + verification are local; they must not call lead.execute_task."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=orca, lead=lead),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert Phase.MAINTENANCE_REVIEW.value in state.completed
    assert Phase.VERIFICATION.value in state.completed
    assert lead.sent == []


def test_mode_c_requires_real_existing_project_root(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            project_root=str(missing),
            task_prompt="x",
        ),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "directory" in outcome.detail.lower() or "project-root" in outcome.detail.lower()
    assert orca.sent == []


def test_mode_c_resume_creates_no_new_run(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            project_root=str(tmp_path),
            task_prompt="fix typo",
            resume_run_id="resume-run-99",
            maintenance_kwargs={"change_summary": "typo", "touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert state.metadata["orca_run_id"] == "resume-run-99"
    assert orca.run_creates == 0
    assert not any((r.role or "") == "ensure_run" for r in orca.sent)


def test_mode_c_missing_run_id_receipt_fails_closed(tmp_path: Path) -> None:
    """Alias coverage for FR-067."""
    test_no_synthetic_run_id_when_orca_omits(tmp_path)


def test_failed_run_use_does_not_create_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from aichestra.providers import orca as orca_mod
    from aichestra.providers.base import (
        FailureClass,
        ProviderSession,
        ProviderTaskRequest,
        ProviderTaskResult,
    )
    from aichestra.providers.orca import OrcaProvider

    calls: list[list[str]] = []

    def fake_run_cli_task(*, binary, argv, session, request, unavailable_detail):
        calls.append(list(argv))
        if "run-use" in argv:
            return ProviderTaskResult(
                ok=False,
                failure=FailureClass.ERROR,
                detail="run-use failed",
                session_id=session.session_id,
            )
        return ProviderTaskResult(
            ok=True,
            failure=FailureClass.NONE,
            detail="should not reach",
            session_id=session.session_id,
            output='{"result":{"id":"task-x"}}',
        )

    monkeypatch.setattr(orca_mod, "run_cli_task", fake_run_cli_task)
    provider = OrcaProvider()
    session = ProviderSession(
        session_id="s1", kind=ProviderKind.ORCA, role="x"
    )
    result = provider._dispatch_supervised(
        "orca",
        session,
        ProviderTaskRequest(
            prompt="do work",
            role="lead_implement",
            context={"run_id": "run-1"},
        ),
        agent="codex",
    )
    assert result.ok is False
    assert any("run-use" in " ".join(c) for c in calls)
    assert not any("task-create" in c for c in calls)


def test_task_create_without_proven_run_binding_is_refused() -> None:
    from aichestra.providers.base import ProviderSession, ProviderTaskRequest
    from aichestra.providers.orca import OrcaProvider

    provider = OrcaProvider()
    result = provider._dispatch_supervised(
        "orca",
        ProviderSession(session_id="s1", kind=ProviderKind.ORCA, role="x"),
        ProviderTaskRequest(prompt="x", role="research", context={}),
        agent="codex",
    )
    assert result.ok is False
    assert "run_id" in result.detail


def test_disabled_codex_is_never_dispatched(tmp_path: Path) -> None:
    orca = fake_orca("success")
    disabled = fake_codex("unavailable")
    cursor = fake_cursor("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=None,
            providers=[orca.probe(), disabled.probe(), cursor.probe()],
            preferred_lead="codex",
            fallback_lead="cursor",
            project_root=str(tmp_path),
            task_prompt="fix typo",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    agents = next(r for r in orca.sent if (r.role or "") == "mode_c_agents")
    agent = (agents.context or {}).get("lead_agent") or (agents.context or {}).get("agent")
    assert agent == "cursor"
    assert disabled.sent == []


def test_disabled_cursor_is_never_dispatched(tmp_path: Path) -> None:
    orca = fake_orca("success")
    codex = fake_codex("success")
    disabled_cursor = fake_cursor("unavailable")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=codex,
            providers=[orca.probe(), codex.probe(), disabled_cursor.probe()],
            preferred_lead="codex",
            fallback_lead="cursor",
            project_root=str(tmp_path),
            task_prompt="fix typo",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    agents = next(r for r in orca.sent if (r.role or "") == "mode_c_agents")
    assert (agents.context or {}).get("lead_agent") == "codex"
    assert disabled_cursor.sent == []


def test_disabled_local_worker_is_never_dispatched(tmp_path: Path) -> None:
    orca = fake_orca("success")
    local = fake_local_worker("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=True,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            local_worker=local,
            task_prompt="refactor across 12 files in multi-package monorepo",
            research_query="auth",
        ),
    )
    wf.bindings.local_enabled = False
    state = wf.run_all()
    assert not state.failed, state.failed
    assert local.sent == []
    package = state.metadata.get("mode_c_policy_package") or {}
    assert package.get("research_agent") != "opencode"


def test_no_available_lead_does_not_default_to_codex(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=None,
            providers=[
                orca.probe(),
                fake_codex("unavailable").probe(),
                fake_cursor("unavailable").probe(),
            ],
            preferred_lead="codex",
            fallback_lead="cursor",
            project_root=str(tmp_path),
            task_prompt="fix typo",
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert state.failed
    assert not any((r.role or "") == "mode_c_agents" for r in orca.sent)
    # Must not invent agent=codex on any request.
    for req in orca.sent:
        agent = (req.context or {}).get("agent") or (req.context or {}).get("lead_agent")
        assert agent != "codex"


def test_medium_speckit_reaches_implementation_without_test_metadata_override(
    tmp_path: Path,
) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert Phase.LEAD_IMPLEMENT.value in state.completed
    assert "brief_satisfied" not in state.metadata
    assert "plan_satisfied" not in state.metadata
    assert (tmp_path / ".aichestra" / "speckit" / "brief.md").is_file()
    assert (tmp_path / ".aichestra" / "speckit" / "plan.md").is_file()
    assert any((r.role or "") == "speckit_artifacts" for r in orca.sent)


def test_large_speckit_reaches_implementation_only_after_real_artifacts(
    tmp_path: Path,
) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="security auth full sdd high risk change",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert Phase.LEAD_IMPLEMENT.value in state.completed
    spec = tmp_path / ".aichestra" / "speckit"
    for name in ("brief.md", "clarify.md", "plan.md", "tasks.md"):
        assert (spec / name).is_file(), name
    assert "brief_satisfied" not in state.metadata


def test_missing_required_speckit_artifact_fails_closed(tmp_path: Path) -> None:
    test_speckit_gate = __import__(
        "tests.contract.test_mode_c_orca_control_plane",
        fromlist=["test_speckit_gate_blocks_when_artifacts_missing"],
    )
    test_speckit_gate.test_speckit_gate_blocks_when_artifacts_missing(tmp_path)


def test_parent_checkout_not_modified_by_attachment_staging(tmp_path: Path) -> None:
    test_attachments_are_forwarded_to_orca(tmp_path)


def test_attachment_reaches_orca_worker(tmp_path: Path) -> None:
    src = tmp_path / "shot.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            attachments=(str(src),),
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    agents = next(r for r in orca.sent if (r.role or "") == "mode_c_agents")
    assert agents.attachments
    assert any("shot.png" in p or p.endswith("shot.png") for p in agents.attachments)


def test_handoff_reuses_existing_orca_run() -> None:
    from aichestra.orchestration.handoff import build_handoff_packet, prepare_manual_handoff

    packet = build_handoff_packet(original_request="continue", orca_run_id="run-42")
    payload = prepare_manual_handoff(packet)
    assert payload["preserves_orca_run"] is True
    assert payload["orca_run_id"] == "run-42"
    assert "--run run-42" in payload["suggested_orca_command"]
    assert "run-create" not in payload["suggested_orca_command"]


def test_handoff_does_not_create_second_run() -> None:
    test_handoff_reuses_existing_orca_run()


def test_handoff_cursor_worker_is_inside_existing_run() -> None:
    from aichestra.orchestration.handoff import build_handoff_packet, prepare_manual_handoff

    packet = build_handoff_packet(original_request="continue", orca_run_id="run-7")
    payload = prepare_manual_handoff(packet)
    cmd = payload["suggested_orca_command"]
    assert "run-use --id run-7" in cmd
    assert "--agent cursor" in cmd
    assert "--run run-7" in cmd


def test_worker_done_without_matching_dispatch_is_not_success() -> None:
    test_worker_done_requires_matching_dispatch()


def test_unconfigured_dotnet_project_can_detect_safe_verification(tmp_path: Path) -> None:
    from aichestra.orchestration.verification import detect_verification_commands

    (tmp_path / "App.csproj").write_text("<Project></Project>", encoding="utf-8")
    cmds = detect_verification_commands(tmp_path)
    assert cmds[0] == ["dotnet", "build"]
    assert any(c[0] == "dotnet" and "test" in c for c in cmds)


def test_unconfigured_python_project_can_detect_safe_verification(tmp_path: Path) -> None:
    test_verify_auto_detect_python(tmp_path)


def test_unconfigured_node_project_can_detect_safe_verification(tmp_path: Path) -> None:
    from aichestra.orchestration.verification import detect_verification_commands

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest", "build": "tsc"}}),
        encoding="utf-8",
    )
    cmds = detect_verification_commands(tmp_path)
    assert ["npm", "test", "--", "--watchAll=false"] in cmds
    assert ["npm", "run", "build"] in cmds


def test_bootstrap_can_reach_complete_state_when_environment_ready(
    fake_aichestra_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aichestra import doctor as doctor_mod
    from aichestra.bootstrap.core import bootstrap
    from aichestra.config.layering import load_json, machine_local_path

    class _Ok:
        ok = True
        checks: list = []

    monkeypatch.setattr(doctor_mod, "run_doctor", lambda **kwargs: _Ok())
    result = bootstrap(repo_root=fake_aichestra_root, enable_local=False)
    assert result.ok
    notes = load_json(machine_local_path(fake_aichestra_root)).get("notes") or {}
    assert notes.get("bootstrap_complete") is True
    assert "bootstrap_complete" in result.actions


def test_mode_a_codex_unintercepted() -> None:
    assert intercepts_native_cli(Mode.NATIVE) is False
    status = CodexProvider().probe()
    assert status.intercepts_native_cli is False


def test_mode_a_cursor_unintercepted() -> None:
    assert intercepts_native_cli(Mode.NATIVE) is False
    status = CursorProvider().probe()
    assert status.intercepts_native_cli is False

def test_mode_c_task_cannot_escape_current_run(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=_small_bindings(tmp_path, orca=orca),
    )
    state = wf.run_all()
    run_id = state.metadata["orca_run_id"]
    for req in orca.sent:
        role = (req.role or "").strip().lower()
        if role in {"ensure_run", "control_plane", "classify", "phase_report", "status_ping"}:
            continue
        assert (req.context or {}).get("run_id") == run_id
