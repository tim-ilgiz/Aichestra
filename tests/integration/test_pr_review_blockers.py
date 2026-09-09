"""Regression coverage for PR review Request-changes blockers (v1)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from aichestra.orchestration.change_signals import _is_test_path, infer_change_signals
from aichestra.orchestration.handoff import build_handoff_packet, prepare_manual_handoff
from aichestra.orchestration.maintenance_reviewer import review_change
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.speckit_policy import SpecKitScale, classify_speckit_scale
from aichestra.orchestration.workflow import (
    OrchestratedWorkflow,
    Phase,
    WorkflowBindings,
)
from aichestra.orchestration.worktrees import _pid_alive
from aichestra.providers.base import ProviderTaskRequest
from aichestra.providers.orca import interpret_orca_wait_event
from aichestra.security.sanitize import sanitize_mapping
from aichestra.security.staging_ops import build_op
from tests.fakes.providers import fake_codex, fake_orca


def test_windows_pid_check_does_not_call_os_kill() -> None:
    with mock.patch("os.name", "nt"):
        with mock.patch(
            "aichestra.orchestration.worktrees._pid_alive_windows",
            return_value=True,
        ) as win:
            with mock.patch("os.kill") as kill:
                assert _pid_alive(4242) is True
                win.assert_called_once_with(4242)
                kill.assert_not_called()


def test_curl_health_disables_curlrc_first() -> None:
    op = build_op("curl_health", url="https://stg.example/health")
    assert op.argv[0] == "curl"
    assert op.argv[1] == "-q"
    assert "--get" in op.argv


def test_staging_rejects_ssh_private_keys() -> None:
    with pytest.raises(ValueError, match="allowlist|diagnostic"):
        build_op("cat_file", path="/home/deploy/.ssh/id_ed25519")


def test_orca_question_from_other_dispatch_not_success() -> None:
    ok, detail, _meta = interpret_orca_wait_event(
        {"type": "question", "dispatchId": "other"},
        dispatch_id="ours",
    )
    assert ok is False
    assert "question" in detail


def test_orca_worker_done_matching_dispatch_ok() -> None:
    ok, detail, _meta = interpret_orca_wait_event(
        {"type": "worker_done", "dispatchId": "d1"},
        dispatch_id="d1",
    )
    assert ok is True
    assert detail == "worker_done"


def test_latest_py_not_test_path() -> None:
    assert _is_test_path("src/latest.py") is False
    assert _is_test_path("tests/test_foo.py") is True


def test_classify_not_hardcoded_medium() -> None:
    small = classify_speckit_scale(risk="low", estimated_files=1)
    large = classify_speckit_scale(touches_security=True)
    assert small.scale is SpecKitScale.SMALL
    assert large.scale is SpecKitScale.LARGE_HIGH_RISK


def test_workflow_classify_uses_speckit_and_binds_orca_run() -> None:
    orca = fake_orca("success")
    lead = fake_codex("success")
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            project_root=None,
            task_prompt="fix one-line typo",
            maintenance_kwargs={"change_summary": "typo", "touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    outcome = wf.run_phase()  # classify
    assert outcome is not None and outcome.status.value == "succeeded"
    assert wf.state.metadata["classify"]["classification"] == "small"
    assert wf.state.metadata.get("orca_run_id")
    assert any((req.role or "") == "ensure_run" for req in orca.sent)


def test_lead_review_includes_task_and_sanitized_verification(tmp_path: Path) -> None:
    orca = fake_orca("success")
    lead = fake_codex("success")
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=lead,
            project_root=str(proj),
            task_prompt="ACCEPTANCE: users can log in with SSO",
            maintenance_kwargs={
                "change_summary": "sso",
                "touches_behavior": False,
                "touches_public_api": False,
                "risk": "low",
            },
            verification_commands=[
                [
                    sys.executable,
                    "-c",
                    "print('Authorization: Bearer DEMO_FAKE_TOKEN'); import sys; sys.exit(0)",
                ]
            ],
        ),
    )
    state = wf.run_all()
    assert Phase.LEAD_REVIEW.value in state.completed, state.failed
    assert lead.sent == []
    review_req = next(req for req in orca.sent if (req.role or "") == "lead_review")
    bounded = review_req.bounded_prompt()
    assert "ACCEPTANCE" in bounded or "SSO" in bounded or "log in" in bounded.lower()
    assert "DEMO_FAKE_TOKEN" not in bounded
    assert "DEMO_FAKE_TOKEN" not in str(review_req.context)


def test_quota_failure_prepares_manual_handoff(tmp_path: Path) -> None:
    orca = fake_orca("success")
    proj = tmp_path / "proj"
    proj.mkdir()
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            lead=fake_codex("success"),
            project_root=str(proj),
            task_prompt="continue feature",
            maintenance_kwargs={"change_summary": "x"},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    # Classify then fail implement on quota via Orca (not direct lead).
    wf.run_phase()
    orca._execute_scenario = "quota"
    outcome = wf.run_phase()  # lead_implement
    assert outcome is not None
    assert outcome.status.value == "failed"
    assert "manual_handoff" in wf.state.metadata
    assert wf.state.metadata["manual_handoff"]["mode"] == "manual_one_action"


def test_bounded_prompt_keeps_read_only_constraint() -> None:
    req = ProviderTaskRequest(
        prompt="A" * 9000,
        read_only=True,
        max_prompt_chars=200,
    )
    text = req.bounded_prompt()
    assert "CONSTRAINT: read-only" in text
    assert len(text) <= 200


def test_sanitize_mapping_redacts_verification_blob() -> None:
    raw = {
        "stdout": "Authorization: Bearer DEMO_FAKE_TOKEN",
        "stderr": "",
        "ok": True,
    }
    cleaned = sanitize_mapping(raw)
    assert "DEMO_FAKE_TOKEN" not in cleaned["stdout"]


def test_handoff_cli_helper_packet_usable() -> None:
    packet = build_handoff_packet(
        original_request="finish auth",
        repo_path="/tmp/proj",
        workflow_phase="lead_implement",
        next_action="continue in Cursor",
    )
    payload = prepare_manual_handoff(packet)
    assert "bounded_brief" in payload
    assert "finish auth" in payload["bounded_brief"]


def test_existing_cover_does_not_force_update() -> None:
    decision = review_change(
        change_summary="covered",
        touches_behavior=True,
        existing_tests_cover=True,
    )
    assert decision.TEST_DECISION == "none"


def test_infer_signals_latest_py_is_behavior_not_test() -> None:
    signals = infer_change_signals(
        change_summary="implement feature",
        changed_paths=["src/latest.py"],
    )
    assert signals["touches_behavior"] is True
    assert signals["existing_tests_cover"] is False
