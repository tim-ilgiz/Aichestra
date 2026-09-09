"""Regression coverage for Mode C review findings (verify, roots, writers, quota)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aichestra.cli import main
from aichestra.orchestration.change_signals import infer_change_signals
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.verification import verification_commands_from_config
from aichestra.orchestration.workflow import (
    OrchestratedWorkflow,
    Phase,
    PhaseStatus,
    WorkflowBindings,
)
from aichestra.providers.base import (
    FailureClass,
    ProviderSession,
    ProviderKind,
    ProviderTaskRequest,
)
from aichestra.providers.execution import run_cli_task
from aichestra.providers.quota_guard import real_provider_execution_blocked
from aichestra.security.staging_ssh import run_staging_diagnostic
from tests.fakes.providers import fake_codex, fake_orca


def test_verification_commands_from_project_shape() -> None:
    assert verification_commands_from_config(
        {"verify": ["python", "-m", "pytest", "-q"]}
    ) == [["python", "-m", "pytest", "-q"]]
    assert verification_commands_from_config(
        {"verify": [["npm", "test"], ["npm", "run", "lint"]]}
    ) == [["npm", "test"], ["npm", "run", "lint"]]


def test_verification_failure_fails_workflow(tmp_path: Path) -> None:
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            project_root=str(tmp_path),
            task_prompt="noop",
            maintenance_kwargs={"change_summary": "noop", "touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(23)"]],
        ),
    )
    state = wf.run_all()
    assert Phase.VERIFICATION.value in state.failed
    assert state.stopped is True
    assert state.metadata["verification"]["ok"] is False
    assert state.metadata["verification"]["exit_code"] == 23


def test_staging_to_dict_redacts_secrets() -> None:
    def executor(_argv):
        return SimpleNamespace(
            returncode=0,
            stdout="Authorization: Bearer SECRETTOKEN123\nuptime ok",
            stderr="token=leakvalue",
        )

    result = run_staging_diagnostic(
        alias_name="stg",
        op_kind="uptime",
        config={
            "staging": {
                "aliases": {
                    "stg": {
                        "host": "stg.example",
                        "user": "deploy",
                        "environment": "staging",
                    }
                }
            }
        },
        executor=executor,
    )
    payload = result.to_dict()
    dumped = json.dumps(payload)
    assert "SECRETTOKEN123" not in dumped
    assert "leakvalue" not in dumped
    assert "SECRETTOKEN123" in result.stdout  # raw retained in-memory only
    assert "[REDACTED]" in payload["stdout"] or "[REDACTED]" in payload["sanitized_for_cloud"]


def test_quota_guard_blocks_run_cli_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICHESTRA_NO_REAL_QUOTA", "1")
    assert real_provider_execution_blocked() is True
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(
        "aichestra.providers.execution.subprocess.run",
        boom,
    )
    session = ProviderSession(session_id="s1", kind=ProviderKind.CODEX)
    result = run_cli_task(
        binary="/usr/bin/codex",
        argv=["codex", "exec", "x"],
        session=session,
        request=ProviderTaskRequest(prompt="x"),
    )
    assert result.ok is False
    assert result.failure is FailureClass.QUOTA
    assert result.metadata.get("blocked_by_quota_guard") is True
    assert called["n"] == 0


def test_change_signals_from_paths_for_auth_api() -> None:
    signals = infer_change_signals(
        change_summary="update auth login flow",
        changed_paths=[
            "src/api/auth.py",
            "tests/test_auth.py",
        ],
    )
    assert signals["touches_behavior"] is True
    assert signals["touches_public_api"] is True
    assert signals["risk"] == "high"
    assert signals["existing_tests_cover"] is True


def test_maintenance_uses_change_signals_not_defaults() -> None:
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            task_prompt="change authorization behavior",
            # No maintenance_kwargs — must infer from summary / paths.
            writer_fn=lambda _state, _phase: fake_codex("success").execute_task(
                ProviderTaskRequest(prompt="write")
            ),
        ),
    )
    # Jump after lead implement.
    while wf.state.current_phase not in {Phase.MAINTENANCE_REVIEW, None}:
        outcome = wf.run_phase()
        assert outcome is not None
        assert outcome.status is not PhaseStatus.FAILED

    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.SUCCEEDED
    decision = wf.state.decision
    assert decision is not None
    assert decision.TEST_DECISION != "none"
    assert wf.state.metadata["change_signals"]["effective"]["touches_behavior"] is True


def test_required_writer_blocks_without_executor() -> None:
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            task_prompt="feature",
            maintenance_kwargs={
                "change_summary": "feature",
                "touches_behavior": True,
                "existing_tests_cover": False,
                "risk": "high",
            },
            writer_fn=None,
        ),
    )
    state = wf.run_all()
    assert Phase.TEST_WRITER.value in state.failed
    detail = state.phase_outcomes[Phase.TEST_WRITER.value].detail
    assert "no writer executor" in detail


def test_orchestrate_separates_repo_and_project_roots(
    fake_aichestra_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    project = tmp_path / "target-app"
    project.mkdir()
    aichestra_dir = project / ".aichestra"
    aichestra_dir.mkdir()
    (aichestra_dir / "project.json").write_text(
        json.dumps(
            {
                "project_id": "target",
                "local": {"enabled": True},
                "verify": [sys.executable, "-c", "import sys; sys.exit(0)"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    code = main(
        [
            "orchestrate",
            "--repo-root",
            str(fake_aichestra_root),
            "--project-root",
            str(project),
            "--prompt",
            "noop typo",
            "--no-research",
        ]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["config_roots"]["repo_root"] == str(fake_aichestra_root.resolve())
    assert payload["config_roots"]["project_root"] == str(project.resolve())
    assert payload["config_roots"]["local_enabled"] is True
    assert payload["config_roots"]["verification_commands"] == [
        [sys.executable, "-c", "import sys; sys.exit(0)"]
    ]
    assert payload["config_roots"]["fake_providers"] is True
    # noop → writers skipped; verify exit 0 → success
    assert code == 0
    assert Phase.VERIFICATION.value in payload["completed"]
