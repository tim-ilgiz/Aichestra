"""Regression coverage for Mode C adapter and workflow review findings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aichestra.cli import main
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    GateKind,
    GateStatus,
    ModeCRunController,
    WorkflowBindings,
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
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            providers=[(fake_codex("success")).probe()],
            project_root=str(tmp_path),
            task_prompt="noop typo",
            maintenance_kwargs={"change_summary": "noop", "touches_behavior": False},
            verification_commands=[],
        ),
    )
    state = wf.run_all()
    assert GateKind.VERIFICATION.value in state.failed
    assert state.phase_outcomes[GateKind.VERIFICATION.value].status is GateStatus.FAILED
    assert state.metadata["verification"]["ok"] is False
    assert "not configured" in state.phase_outcomes[GateKind.VERIFICATION.value].detail


def test_mode_c_does_not_use_edit_lock(tmp_path: Path) -> None:
    """Mode C routes writes through Orca worktrees — no local edit.lock."""
    root = tmp_path / "proj"
    root.mkdir()
    blocker = request_edit_lease(root, "other-agent")
    assert blocker.allowed

    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=fake_orca("success"),
            providers=[(fake_codex("success")).probe()],
            project_root=str(root),
            task_prompt="implement",
            maintenance_kwargs={"change_summary": "noop", "touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()  # thin coordinator; Orca worktrees — ignore advisory lock
    assert not state.failed, state.failed
    assert GateKind.ORCA_HANDOFF.value in state.completed
    assert "edit_lease" not in state.metadata
    release_edit_lease(root, "other-agent")


def test_cross_process_lock_file_created(tmp_path: Path) -> None:
    """Advisory lock helper still works for non-Mode-C tooling."""
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
    assert GateKind.ORCA_HANDOFF.value in payload.get("completed", [])
    assert "test_writer" not in payload.get("failed", [])
    assert "mode_c_writers" not in json.dumps(payload)
    assert "no writer executor" not in json.dumps(payload)
    assert code == 0



@pytest.mark.parametrize("provider,local,expected", [("codex", False, "codex"), ("cursor", False, "cursor"), ("local-worker", True, "opencode")])
def test_real_adapter_coordinator_contract(monkeypatch, tmp_path, provider, local, expected):
    """Exercise production adapter command construction, not a fake workflow DAG."""
    from aichestra.providers.orca import OrcaProvider
    from aichestra.providers.base import ProviderSession
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "test-live-authority")
    adapter = OrcaProvider()
    monkeypatch.setattr(adapter, "probe", lambda: ProviderStatus(
        kind=ProviderKind.ORCA, available=True, binary_path="orca"))
    calls = []
    def run(**kwargs):
        argv = kwargs["argv"]
        calls.append(argv)
        command = argv[2] if argv[1] == "orchestration" else "status"
        receipts = {
            "status": {}, "run-use": {}, "task-create": {"id": "t1"},
            "worker-start": {"dispatchId": "d1", "worktreePath": str(tmp_path)},
            "check": {"type": "worker_done", "dispatchId": "d1", "outcome": "succeeded"},
            "run-show": {"id": "r1", "state": "active"},
        }
        return ProviderTaskResult(ok=True, output=json.dumps(receipts[command]))
    monkeypatch.setattr("aichestra.providers.orca.run_cli_task", run)
    policy = {"preferred_lead": "codex", "fallback_lead": "cursor", "local_enabled": local,
              "local_available": local, "providers": {provider: {"available": True}}}
    attachment = tmp_path / "input.txt"
    attachment.write_text("reference")
    request = ProviderTaskRequest(prompt="Update the project", role="mode_c_handoff", attachments=(str(attachment),),
        cwd=str(tmp_path), context={"run_id": "r1", "provider_policy": policy,
        "project_context": {"instruction_excerpts": {"AGENTS.md": "x" * 5000}, "marker": "CONTEXT_END"}})
    result = adapter.send(ProviderSession(session_id="s", kind=ProviderKind.ORCA), request)
    assert result.ok
    task = next(c for c in calls if "task-create" in c)
    spec = task[task.index("--spec") + 1]
    assert "explicit Mode C coordinator" in spec
    assert "CONTEXT_END" in spec
    assert "NEVER create another Run" in spec
    assert "arbitrary Tasks/Dispatches" in spec
    worker = next(c for c in calls if "worker-start" in c)
    assert worker[worker.index("--agent") + 1] == expected
    assert worker[worker.index("--worktree") + 1] == "current"
    assert "--setup" not in worker and "--name" not in worker
    assert worker[worker.index("--attach") + 1] == str(attachment)
    assert result.metadata["attachment_delivery"]["staged"] == []
    assert sum("task-create" in c for c in calls) == 1
    assert not any("run-create" in c for c in calls)
    state = adapter.send(ProviderSession(session_id="s", kind=ProviderKind.ORCA),
        ProviderTaskRequest(prompt="state", role="run_status", context={"run_id": "r1"}, read_only=True))
    assert state.metadata["receipt"] == {"id": "r1", "state": "active"}


def test_orca_authority_precondition_before_mutations(monkeypatch):
    from aichestra.providers.orca import OrcaProvider
    from aichestra.providers.base import ProviderSession
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
    adapter = OrcaProvider()
    monkeypatch.setattr(adapter, "probe", lambda: ProviderStatus(kind=ProviderKind.ORCA, available=True, binary_path="orca"))
    calls = []
    def run(**kwargs):
        calls.append(kwargs["argv"])
        return ProviderTaskResult(ok=True, output="{}")
    monkeypatch.setattr("aichestra.providers.orca.run_cli_task", run)
    result = adapter.send(ProviderSession(session_id="s", kind=ProviderKind.ORCA), ProviderTaskRequest(prompt="task", role="ensure_run"))
    assert not result.ok and "ORCA_TERMINAL_HANDLE" in result.detail
    assert calls == [["orca", "status", "--json"]]
