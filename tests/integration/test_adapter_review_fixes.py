"""Regression coverage for Mode C adapter and workflow review findings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aichestra.cli import main
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    OrchestratedWorkflow,
    Phase,
    PhaseStatus,
    WorkflowBindings,
    bound_writer_from_lead,
)
from aichestra.orchestration.worktrees import release_edit_lease, request_edit_lease
from aichestra.providers.base import (
    ProviderKind,
    ProviderRole,
    ProviderStatus,
    ProviderTaskRequest,
    ProviderTaskResult,
)
from aichestra.providers.codex import CodexProvider
from aichestra.providers.local_worker import (
    LocalWorkerProvider,
    build_local_opencode_argv,
    build_local_opencode_config,
)
from aichestra.providers.orca import build_orca_argv
from tests.fakes.providers import fake_codex, fake_orca


def test_orca_argv_uses_orchestration_not_run() -> None:
    req = ProviderTaskRequest(prompt="classify task", role="control_plane", read_only=True)
    argv = build_orca_argv("/bin/orca", req, session_id="abcd1234-ffff")
    assert argv[1:3] == ["orchestration", "run-create"]
    assert "run" not in argv[1:3]
    assert "--session" not in argv


def test_orca_write_argv_uses_worker_start() -> None:
    req = ProviderTaskRequest(
        prompt="implement feature",
        role="lead_implement",
        context={"task_id": "t1", "run_id": "r1"},
    )
    argv = build_orca_argv("/bin/orca", req, session_id="abcd1234-ffff")
    assert argv[1:3] == ["orchestration", "worker-start"]
    assert "--agent" in argv
    assert "--worktree" in argv


def test_codex_sandbox_workspace_write_for_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_cli_task(**kwargs):
        captured["argv"] = list(kwargs["argv"])
        return ProviderTaskResult(ok=True, detail="ok", session_id="s")

    monkeypatch.setattr(
        "aichestra.providers.codex.run_cli_task",
        fake_run_cli_task,
    )
    monkeypatch.setattr(
        "aichestra.providers.codex.which_binary",
        lambda _names: "/usr/bin/codex",
    )
    monkeypatch.setattr(
        "aichestra.providers.codex.probe_version",
        lambda _b: "codex 0",
    )
    CodexProvider().execute_task(ProviderTaskRequest(prompt="edit files", read_only=False))
    assert "--sandbox" in captured["argv"]
    assert "workspace-write" in captured["argv"]


def test_codex_sandbox_read_only_for_research(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_cli_task(**kwargs):
        captured["argv"] = list(kwargs["argv"])
        return ProviderTaskResult(ok=True, detail="ok", session_id="s")

    monkeypatch.setattr("aichestra.providers.codex.run_cli_task", fake_run_cli_task)
    monkeypatch.setattr(
        "aichestra.providers.codex.which_binary", lambda _names: "/usr/bin/codex"
    )
    monkeypatch.setattr(
        "aichestra.providers.codex.probe_version", lambda _b: "codex 0"
    )
    CodexProvider().execute_task(ProviderTaskRequest(prompt="inspect", read_only=True))
    assert "read-only" in captured["argv"]


def test_local_worker_pins_model_and_endpoint() -> None:
    argv = build_local_opencode_argv(
        "/bin/opencode",
        model_ref="ollama/qwen2.5-coder",
        prompt="research auth",
        cwd="/tmp/proj",
    )
    assert argv == [
        "/bin/opencode",
        "run",
        "--model",
        "ollama/qwen2.5-coder",
        "research auth",
        "--dir",
        "/tmp/proj",
    ]
    cfg = build_local_opencode_config(
        model_id="qwen2.5-coder",
        ollama_host="http://127.0.0.1:11434",
    )
    assert cfg["model"] == "ollama/qwen2.5-coder"
    assert cfg["provider"]["ollama"]["options"]["baseURL"].endswith("/v1")


def test_local_worker_send_sets_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeModel:
        id = "tinyllama"
        name = "tinyllama"
        installed = True

        def has_capability(self, _cap):
            return True

    class FakeSelection:
        ok = True
        model = FakeModel()
        reason = "ok"

    def fake_run_cli_task(**kwargs):
        captured["argv"] = list(kwargs["argv"])
        captured["env"] = kwargs.get("env")
        return ProviderTaskResult(ok=True, detail="ok", session_id="s")

    worker = LocalWorkerProvider(local_enabled=True)
    monkeypatch.setattr(worker, "probe", lambda: ProviderStatus(
        kind=ProviderKind.LOCAL_WORKER,
        available=True,
        role=ProviderRole.WORKER,
        binary_path="/bin/opencode",
    ))
    monkeypatch.setattr(worker, "_select_local_model", lambda: FakeSelection())
    monkeypatch.setattr(
        "aichestra.providers.local_worker.run_cli_task",
        fake_run_cli_task,
    )
    result = worker.execute_task(ProviderTaskRequest(prompt="research"))
    assert result.ok
    assert "--model" in captured["argv"]
    assert "ollama/tinyllama" in captured["argv"]
    assert "OPENCODE_CONFIG_CONTENT" in (captured["env"] or {})
    cfg = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"] == "ollama/tinyllama"


def test_missing_verification_commands_fail_workflow(tmp_path: Path) -> None:
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            project_root=str(tmp_path),
            task_prompt="noop typo",
            maintenance_kwargs={"change_summary": "noop", "touches_behavior": False},
            verification_commands=[],
        ),
    )
    state = wf.run_all()
    assert Phase.VERIFICATION.value in state.failed
    assert state.phase_outcomes[Phase.VERIFICATION.value].status is PhaseStatus.FAILED
    assert state.metadata["verification"]["ok"] is False
    assert "not configured" in state.phase_outcomes[Phase.VERIFICATION.value].detail


def test_edit_lease_wired_into_lead_implement(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    blocker = request_edit_lease(root, "other-agent")
    assert blocker.allowed

    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            lead=fake_codex("success"),
            project_root=str(root),
            task_prompt="implement",
            maintenance_kwargs={"change_summary": "noop", "touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    # classify
    assert wf.run_phase() is not None
    # lead_implement should refuse shared checkout
    outcome = wf.run_phase()
    assert outcome is not None
    assert outcome.status is PhaseStatus.FAILED
    assert "shared concurrent" in outcome.detail.lower()
    release_edit_lease(root, "other-agent")


def test_cross_process_lock_file_created(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    lease = request_edit_lease(root, "agent-a")
    assert lease.allowed
    lock = root / ".aichestra" / "edit.lock"
    assert lock.is_file()
    release_edit_lease(root, "agent-a")
    assert not lock.exists()


def test_cli_binds_writer_for_login_feature(
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
                "local": {"enabled": False},
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
            "implement login feature",
            "--no-research",
        ]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert Phase.TEST_WRITER.value not in payload.get("failed", [])
    assert "no writer executor" not in json.dumps(payload)
    assert code == 0


def test_bound_writer_from_lead_invokes_lead() -> None:
    lead = fake_codex("success")
    writer = bound_writer_from_lead(lead, project_root=None)
    state = SimpleNamespace(
        decision=SimpleNamespace(
            TEST_DECISION="required",
            TEST_SCOPE=["login"],
            DOC_DECISION="none",
            DOC_TARGETS=[],
        )
    )
    result = writer(state, Phase.TEST_WRITER)  # type: ignore[arg-type]
    assert result.ok
    assert lead.sent
    assert lead.sent[-1].role == Phase.TEST_WRITER.value
