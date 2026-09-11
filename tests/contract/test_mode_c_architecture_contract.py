"""Architecture contract: Mode C single-Orca-Run invariants (MODE-C-001–016).

These tests lock the *target* architecture after Phase 24: coordinator under
Orca owns the concrete workflow/DAG and inner worker selection; Orca owns
canonical Run/Task/Dispatch/worker/terminal/worktree lifecycle; Aichestra
supplies discovery, ExecutionTargets, policy, security, and deterministic gates.
"""

from __future__ import annotations

from tests.fakes.providers import fake_execution_targets

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
    MODE_C_HANDOFF_ROLE,
    GateKind,
    GateStatus,
    ModeCRunController,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import FailureClass, ProviderKind
from aichestra.providers.codex import CodexProvider
from aichestra.providers.cursor import CursorProvider
from aichestra.providers.discovery import discover_providers
from tests.fakes.providers import fake_codex, fake_cursor, fake_local_worker, fake_orca


def _run_create_count(orca) -> int:
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
    providers=None,
) -> WorkflowBindings:
    root: str | None
    if project_root is ...:
        root = str(tmp_path)
    else:
        root = project_root  # type: ignore[assignment]
    return WorkflowBindings(
        execution_targets=fake_execution_targets(),
        orca=orca,


        providers=list(providers) if providers is not None else [p.probe() for p in [lead if lead is not None else fake_codex("success"), local_worker] if p is not None],
        project_root=root,
        task_prompt=task_prompt,
        research_query=research_query,
        attachments=attachments,
        maintenance_kwargs={"change_summary": "typo", "touches_behavior": False},
        verification_enabled=True,
        verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
    )


def _handoff_reqs(orca):
    return [r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE]


def test_mode_c_requires_orca(tmp_path: Path) -> None:
    """MODE-C-001 / MODE-C-006: Mode C fails closed without Orca."""
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=None, lead=lead),
    )
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "Orca" in outcome.detail
    assert outcome.result.get("failure") == FailureClass.UNAVAILABLE.value
    assert lead.sent == []
    # CLI uses run_all(); must fail closed without AssertionError.
    state = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=None, lead=lead),
    ).run_all()
    assert state.stopped
    assert GateKind.ORCA_HANDOFF.value in state.failed
    assert lead.sent == []


def test_mode_c_requires_project_root() -> None:
    """MODE-C-005: unresolved project root fails before orchestration."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,
            providers=[(lead).probe()],
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
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=orca),
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
        bindings=_small_bindings(tmp_path, orca=orca, lead=cursor),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert cursor.sent == []


def test_mode_c_never_directly_executes_local_worker(tmp_path: Path) -> None:
    """MODE-C-004: local-worker must not be scheduled directly by Aichestra."""
    orca = fake_orca("success")
    worker = fake_local_worker("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            local_worker=worker,
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    )
    wf.bindings.local_enabled = True
    state = wf.run_all()
    assert not state.failed, state.failed
    assert worker.sent == []
    assert len(_handoff_reqs(orca)) == 1


def test_all_mode_c_tasks_use_same_orca_run(tmp_path: Path) -> None:
    """MODE-C-007: all Mode C agent tasks share one run_id."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="refactor across 12 files in multi-package monorepo",
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
    assert GateKind.ORCA_HANDOFF.value not in wf.state.completed


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
    """MODE-C-010: --attach paths must reach Orca handoff as attachments."""
    img = tmp_path / "screenshot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    log = tmp_path / "log.txt"
    log.write_text("error line\n", encoding="utf-8")
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            lead=lead,
            attachments=(str(img), str(log)),
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    assert handoff.attachments
    assert len(handoff.attachments) >= 1
    assert lead.sent == []
    assert not (tmp_path / ".aichestra" / "attachments").exists()
    routing = state.metadata.get("media_routing") or {}
    assert routing.get("staged_outside_parent") is True


def test_missing_attachment_fails_closed(tmp_path: Path) -> None:
    orca = fake_orca("success")
    missing = tmp_path / "does-not-exist.png"
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            attachments=(str(missing),),
        ),
    )
    state = wf.run_all()
    assert state.failed
    assert GateKind.ATTACHMENTS.value in state.failed
    assert not _handoff_reqs(orca)


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
        bindings=_small_bindings(tmp_path, orca=orca),
    )
    state = wf.run_all()
    assert state.failed
    blob = " ".join(o.detail for o in state.gate_outcomes.values()).lower()
    assert "run_id" in blob or "synthetic" in blob
    assert not wf.state.metadata.get("orca_run_id_synthesized")
    assert "aichestra-run-" not in str(wf.state.metadata.get("orca_run_id") or "")


def test_disabled_lead_never_dispatched(tmp_path: Path) -> None:
    """Disabled/unavailable codex must not be invented as Mode C agent."""
    orca = fake_orca("success")
    disabled = fake_codex("unavailable")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=(),
            orca=orca,

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
            verification_enabled=True,
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert state.failed
    assert disabled.sent == []
    assert not _handoff_reqs(orca)


def test_local_policy_forwarded_not_scheduled(tmp_path: Path) -> None:
    """Local capability is policy for Orca — Aichestra does not schedule research."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            local_worker=fake_local_worker("success"),
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    )
    wf.bindings.local_enabled = False
    wf.bindings.local_model_ref = None
    state = wf.run_all()
    assert not state.failed, state.failed
    package = state.metadata.get("mode_c_policy_package") or {}
    assert package.get("local_enabled") is False
    assert package.get("canonical_lifecycle_owner") == "orca"
    assert package.get("workflow_dag_owner") == "coordinator_under_orca"
    assert package.get("inner_worker_selection_owner") == "coordinator_under_orca"
    assert package.get("aichestra_role") == "policy_context_gates"
    assert "orchestration_owner" not in package
    roles = {(r.role or "") for r in orca.sent}
    assert "research" not in roles
    assert MODE_C_HANDOFF_ROLE in roles


def test_local_capabilities_reach_orca_policy(tmp_path: Path) -> None:
    """Selected model_ref + endpoint + capabilities are policy data for Orca."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            local_worker=fake_local_worker("success"),
        ),
    )
    wf.bindings.local_enabled = True
    wf.bindings.local_endpoint = "http://127.0.0.1:11434"
    wf.bindings.local_model_ref = "llava:7b"
    wf.bindings.local_capabilities = ("text", "vision")
    wf.bindings.installed_models = (
        {"id": "llava:7b", "capabilities": ["text", "vision"]},
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    handoff = _handoff_reqs(orca)[0]
    caps = (handoff.context or {}).get("capabilities") or {}
    assert caps.get("local_enabled") is True
    assert caps.get("local_model_ref") == "llava:7b"
    assert caps.get("local_endpoint") == "http://127.0.0.1:11434"
    assert "vision" in (caps.get("local_capabilities") or [])
    package = state.metadata["mode_c_policy_package"]
    assert package["local_model_ref"] == "llava:7b"


def test_research_artifact_policy_in_package(tmp_path: Path) -> None:
    """Deterministic research_artifact hint — not LLM MD invention."""
    from aichestra.orchestration.research_compact import RESEARCH_NOTES_PATH

    orca = fake_orca("success")
    # SMALL typo fix → none (live-test shape).
    small = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=orca, task_prompt="fix one-line typo"),
    ).run_all()
    assert not small.failed, small.failed
    pkg = small.metadata["mode_c_policy_package"]
    assert pkg["research_artifact"] == "none"
    assert pkg["research_artifact_notes_path"] == RESEARCH_NOTES_PATH
    assert pkg["speckit_scale"] == "small"

    # local_enabled is capability, not a local→cloud research handoff.
    orca_local = fake_orca("success")
    local_cap = _small_bindings(
        tmp_path, orca=orca_local, task_prompt="fix one-line typo"
    )
    local_cap.local_enabled = True
    local_state = ModeCRunController(
        mode=Mode.ORCHESTRATED, bindings=local_cap
    ).run_all()
    assert not local_state.failed, local_state.failed
    assert local_state.metadata["mode_c_policy_package"]["research_artifact"] == "none"

    orca2 = fake_orca("success")
    with_query = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca2,
            task_prompt="fix one-line typo",
            research_query="auth module",
        ),
    ).run_all()
    assert not with_query.failed, with_query.failed
    assert with_query.metadata["mode_c_policy_package"]["research_artifact"] == "file"

    orca3 = fake_orca("success")
    medium = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca3,
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    ).run_all()
    assert not medium.failed, medium.failed
    med_pkg = medium.metadata["mode_c_policy_package"]
    assert med_pkg["speckit_scale"] == "medium"
    assert med_pkg["research_artifact"] == "file"


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


def test_unrelated_dispatch_event_is_retryable_not_terminal() -> None:
    from aichestra.providers.orca import interpret_orca_wait_event

    ok, detail, meta = interpret_orca_wait_event(
        {"events": [{"type": "question", "dispatchId": "other"}], "deliveryId": "d-1"},
        dispatch_id="ours",
    )
    assert ok is False
    assert "unrelated" in detail
    assert meta.get("retryable_unrelated") is True
    assert meta.get("ack_delivery_id") == "d-1"


def test_question_from_non_coordinator_terminal_is_unrelated() -> None:
    from aichestra.providers.orca import interpret_orca_wait_event

    ok, detail, meta = interpret_orca_wait_event(
        {
            "messages": [
                {
                    "type": "question",
                    "id": "q-child",
                    "from_handle": "term_child",
                    "subject": "AICHESTRA_GATE:maintenance",
                }
            ]
        },
        dispatch_id="dispatch-coordinator",
        terminal_handle="term_coordinator",
    )

    assert not ok
    assert "unrelated" in detail
    assert meta.get("retryable_unrelated") is True


def test_empty_timed_out_check_poll_is_retryable() -> None:
    """Bounded long-polls must reopen until the overall deadline."""
    from aichestra.providers.orca import interpret_orca_wait_event

    ok, detail, meta = interpret_orca_wait_event(
        {
            "result": {
                "runId": "run_1",
                "dispatchId": "ctx_1",
                "messages": [],
                "count": 0,
                "timedOut": True,
                "cancelled": False,
                "connectionLost": False,
            }
        },
        dispatch_id="ctx_1",
        terminal_handle="term_coord",
    )

    assert not ok
    assert "empty" in detail
    assert meta.get("retryable_poll") is True
    assert meta.get("timed_out") is True
    assert meta.get("retryable_unrelated") is False


def test_answered_question_peek_still_sees_worker_done() -> None:
    """Non-consuming peek keeps the answered question; worker_done must win."""
    from aichestra.providers.orca import interpret_orca_wait_event

    ok, detail, meta = interpret_orca_wait_event(
        {
            "result": {
                "messages": [
                    {
                        "type": "question",
                        "id": "q1",
                        "dispatchId": "ctx_1",
                        "body": "AICHESTRA_GATE:maintenance",
                    },
                    {
                        "type": "worker_done",
                        "id": "done1",
                        "dispatchId": "ctx_1",
                        "outcome": "succeeded",
                    },
                ]
            }
        },
        dispatch_id="ctx_1",
        terminal_handle="term_coord",
        ignore_message_ids=frozenset({"q1"}),
    )

    assert ok is True
    assert detail == "worker_done"
    assert meta.get("event_type") == "worker_done"
    assert meta.get("skipped_answered_questions") == 1


def test_mode_c_does_not_own_general_purpose_agent_phase_scheduler(tmp_path: Path) -> None:
    """MODE-C-011/012: one handoff; no Aichestra agent-phase roles."""
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert state.metadata.get("orchestration_shape_agnostic") is True
    assert state.metadata.get("canonical_lifecycle_owner") == "orca"
    assert state.metadata.get("workflow_dag_owner") == "coordinator_under_orca"
    assert state.metadata.get("aichestra_owns_agent_phases") is False
    assert "workflow_owner" not in state.metadata
    agent_roles = [
        (r.role or "")
        for r in orca.sent
        if (r.role or "")
        not in {"ensure_run", "control_plane", "classify", "phase_report", "status_ping"}
    ]
    assert agent_roles.count(MODE_C_HANDOFF_ROLE) == 1
    for forbidden in (
        "research",
        "lead_implement",
        "mode_c_agents",
        "mode_c_writers",
        "test_writer",
        "doc_writer",
        "lead_review",
        "speckit_artifacts",
    ):
        assert forbidden not in agent_roles


def test_mode_c_orca_is_canonical_lifecycle_owner(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=orca),
    )
    state = wf.run_all()
    assert state.metadata.get("canonical_orchestration") == "orca_run"
    assert isinstance(state.metadata.get("orca_run_id"), str)
    assert state.metadata["orca_run_id"]
    assert state.metadata.get("orchestration_shape_agnostic") is True
    status = state.metadata.get("orca_run_status") or {}
    assert status.get("source") == "orca_run_show_plus_aichestra_gates"
    assert status.get("ok") is True


def test_mode_c_policy_gates_do_not_become_worker_scheduler(tmp_path: Path) -> None:
    """Maintenance + verification stay Aichestra gates; they must not call lead.execute_task."""
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=orca, lead=lead),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert GateKind.MAINTENANCE.value in state.completed
    assert GateKind.VERIFICATION.value in state.completed
    assert lead.sent == []
    roles = {(r.role or "") for r in orca.sent}
    assert "test_writer" not in roles
    assert "doc_writer" not in roles
    assert "mode_c_writers" not in roles


def test_mode_c_missing_run_id_receipt_fails_closed(tmp_path: Path) -> None:
    orca = fake_orca("success")
    original = orca.send

    def omit_run(session, request):
        result = original(session, request)
        if (request.role or "") == "ensure_run":
            meta = dict(result.metadata)
            meta.pop("run_id", None)
            return type(result)(
                ok=result.ok,
                output=result.output,
                failure=result.failure,
                detail=result.detail,
                session_id=result.session_id,
                metadata=meta,
            )
        return result

    orca.send = omit_run  # type: ignore[method-assign]
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=orca),
    )
    state = wf.run_all()
    assert state.failed
    assert "aichestra-run-" not in str(state.metadata.get("orca_run_id") or "")


def test_disabled_codex_is_never_dispatched(tmp_path: Path) -> None:
    orca = fake_orca("success")
    codex = fake_codex("unavailable")
    cursor = fake_cursor("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,

            providers=[orca.probe(), codex.probe(), cursor.probe()],
            project_root=str(tmp_path),
            task_prompt="fix",
            preferred_lead="codex",
            fallback_lead="cursor",
            maintenance_kwargs={"touches_behavior": False},
            verification_enabled=True,
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert codex.sent == []
    policy = state.metadata["provider_policy"]
    assert policy.get("suggested_lead") == "cursor"


def test_disabled_cursor_is_never_dispatched(tmp_path: Path) -> None:
    orca = fake_orca("success")
    cursor = fake_cursor("unavailable")
    codex = fake_codex("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            orca=orca,

            providers=[orca.probe(), codex.probe(), cursor.probe()],
            project_root=str(tmp_path),
            task_prompt="fix",
            preferred_lead="codex",
            fallback_lead="cursor",
            maintenance_kwargs={"touches_behavior": False},
            verification_enabled=True,
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert cursor.sent == []


def test_no_available_lead_does_not_default_to_codex(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=(),
            orca=orca,

            providers=[
                fake_orca("success").probe(),
                fake_codex("unavailable").probe(),
                fake_cursor("unavailable").probe(),
            ],
            project_root=str(tmp_path),
            task_prompt="fix typo",
            preferred_lead="codex",
            fallback_lead="cursor",
            maintenance_kwargs={"touches_behavior": False},
            verification_enabled=True,
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert state.failed
    assert not _handoff_reqs(orca)


def test_speckit_is_policy_not_competing_tree(tmp_path: Path) -> None:
    """FR-080: project Spec Kit stays canonical; no competing .aichestra/speckit/."""
    (tmp_path / ".specify").mkdir()
    (tmp_path / "specs").mkdir()
    (tmp_path / "AGENTS.md").write_text("# Project agents\n", encoding="utf-8")
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            task_prompt="refactor across 12 files in multi-package monorepo",
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert state.metadata["speckit_canonical"]["owner"] == "project"
    assert state.metadata["speckit_canonical"]["competing_aichestra_speckit_forbidden"]
    assert not (tmp_path / ".aichestra" / "speckit").exists()
    assert "speckit_artifacts" not in {(r.role or "") for r in orca.sent}
    handoff = _handoff_reqs(orca)[0]
    ctx = (handoff.context or {}).get("project_context") or {}
    assert ctx.get("speckit", {}).get("owns_canonical") is True


def test_parent_checkout_not_modified_by_attachment_staging(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            attachments=(str(img),),
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    assert not (tmp_path / ".aichestra" / "attachments").exists()
    # Staging temp cleaned after run_all.
    stage = (state.metadata.get("media_routing") or {}).get("stage_root")
    if stage:
        assert not Path(stage).exists()


def test_attachment_reaches_orca_worker(tmp_path: Path) -> None:
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(
            tmp_path,
            orca=orca,
            attachments=(str(img),),
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    handoff = _handoff_reqs(orca)[0]
    assert handoff.attachments
    meta = (handoff.context or {})
    assert meta.get("attachments") or handoff.attachments


def test_basename_collision_does_not_overwrite(tmp_path: Path) -> None:
    from aichestra.providers.attachments import stage_attachments

    d1 = tmp_path / "one"
    d2 = tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    (d1 / "same.txt").write_text("aaa", encoding="utf-8")
    (d2 / "same.txt").write_text("bbb", encoding="utf-8")
    stage = tmp_path / "stage"
    delivery = stage_attachments([d1 / "same.txt", d2 / "same.txt"], stage)
    assert delivery.bytes_delivered
    assert len(delivery.staged) == 2
    assert delivery.staged[0] != delivery.staged[1]
    bodies = {Path(p).read_text(encoding="utf-8") for p in delivery.staged}
    assert bodies == {"aaa", "bbb"}


def test_child_worktree_missing_locator_fails_closed(tmp_path: Path) -> None:
    """If Orca claims a child worktree but locator is missing, do not verify parent."""
    from aichestra.providers.base import ProviderTaskResult

    parent = tmp_path / "parent"
    parent.mkdir()
    orca = fake_orca("success")
    original = orca.send

    def claim_missing_child(session, request):
        if (request.role or "") == MODE_C_HANDOFF_ROLE and isinstance(request.context, dict):
            request.context["worktree_path"] = str(parent / "missing-child")
            request.context["worktree_id"] = "fake-repo::missing"
            request.context["integration_policy"] = "adopt_child_worktree"
        result = original(session, request)
        if (request.role or "") == MODE_C_HANDOFF_ROLE and result.ok:
            meta = dict(result.metadata)
            meta["worktree_path"] = str(parent / "missing-child")
            meta["worktree_id"] = "fake-repo::missing"
            meta["integration_policy"] = "adopt_child_worktree"
            return ProviderTaskResult(
                ok=True,
                output=result.output,
                failure=result.failure,
                detail=result.detail,
                session_id=result.session_id,
                metadata=meta,
            )
        return result

    orca.send = claim_missing_child  # type: ignore[method-assign]
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_small_bindings(tmp_path, orca=orca, project_root=str(parent)),
    )
    state = wf.run_all()
    assert state.failed
    assert "child worktree" in str(state.metadata.get("orca_worktree_adopt_error") or "").lower()
    assert state.metadata.get("verification_cwd") != str(parent) or GateKind.VERIFICATION.value in state.failed or GateKind.ORCA_HANDOFF.value in state.failed
