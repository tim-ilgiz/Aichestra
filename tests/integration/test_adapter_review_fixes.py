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



@pytest.mark.parametrize("provider,local,expected", [("codex", False, "codex"), ("cursor", False, "cursor")])
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
            "reply": {}, "worker-release": {"state": "released"}, "worker-list": {"workers": []},
            "task-list": {"tasks": [{"id": "t1", "status": "completed"}]},
        }
        if command == "check" and not any("reply" in c for c in calls):
            return ProviderTaskResult(ok=True, output=json.dumps({"deliveryId": "delivery1", "messages": [{
                "type": "question", "id": "q1", "dispatchId": "d1", "body": "AICHESTRA_GATE:maintenance"}]}))
        return ProviderTaskResult(ok=True, output=json.dumps(receipts[command]))
    monkeypatch.setattr("aichestra.providers.orca.run_cli_task", run)
    policy = {"preferred_lead": "codex", "fallback_lead": "cursor", "local_enabled": local,
              "local_available": local, "providers": {provider: {"available": True}}}
    attachment = tmp_path / "input.txt"
    attachment.write_text("reference")
    request = ProviderTaskRequest(prompt="Update the project", role="mode_c_handoff", attachments=(str(attachment),),
        gate_handler=lambda gate: {"ok": True, "gate": gate},
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


@pytest.fixture
def coordinator_rpc(monkeypatch, tmp_path):
    """Production adapter with CLI receipts in the installed Orca envelope."""
    from aichestra.providers.orca import OrcaProvider
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "live-test")
    adapter = OrcaProvider()
    monkeypatch.setattr(adapter, "probe", lambda: ProviderStatus(
        kind=ProviderKind.ORCA, available=True, binary_path="orca"))
    calls = []
    fault = {}
    replied = False

    def run(**kwargs):
        nonlocal replied
        argv = kwargs["argv"]
        calls.append(argv)
        command = argv[2] if len(argv) > 2 and argv[1] == "orchestration" else "status"
        receipts = {
            "status": {"runtime": {"reachable": True}},
            "run-create": {"run": {"id": "r1"}},
            "run-use": {"run": {"id": "r1"}},
            "task-create": {"task": {"id": "t1"}},
            "worker-start": {"dispatchId": "d1", "agentTerminalHandle": "worker1",
                             "worktreePath": str(tmp_path)},
            "reply": {}, "worker-release": {"state": "released"},
            "worker-list": {"workers": []},
            # Real Run records are namespaces, with no terminal state field.
            "run-show": {"run": {"id": "r1", "objective": "change"}},
            "task-list": {"tasks": [{"id": "t1", "status": "completed"}]},
        }
        if command == "check":
            event = ({"type": "worker_done", "id": "done1",
                      "payload": json.dumps({"dispatchId": "d1", "outcome": "succeeded"})}
                     if replied or fault.get("omit_gate") else
                     {"type": "question", "id": "q1", "from_handle": "worker1",
                      "subject": "AICHESTRA_GATE:maintenance"})
            data = {"deliveryId": "delivery1", "messages": [event]}
        else:
            data = receipts[command]
        if command == "reply":
            replied = True
        data = fault.get(command, data)
        return ProviderTaskResult(ok=data is not None, output=json.dumps({"result": data}))

    monkeypatch.setattr("aichestra.providers.orca.run_cli_task", run)
    return adapter, calls, fault


def _coordinator_controller(adapter, tmp_path):
    return ModeCRunController(bindings=WorkflowBindings(
        orca=adapter, providers=[fake_codex().probe()], project_root=str(tmp_path),
        task_prompt="fix typo", verification_commands=[[sys.executable, "-c", "pass"]]))


def test_production_gate_reply_precedes_completion_and_cleanup(coordinator_rpc, tmp_path):
    adapter, calls, _ = coordinator_rpc
    controller = _coordinator_controller(adapter, tmp_path)
    state = controller.run_all()
    assert not state.failed
    verbs = [c[2] for c in calls if c[1] == "orchestration"]
    assert verbs.index("reply") < verbs.index("worker-release") < verbs.index("run-show")
    reply = next(c for c in calls if "reply" in c)
    answer = json.loads(reply[reply.index("--body") + 1])
    assert answer["run_id"] == "r1"
    assert answer["decision"] == state.decision.to_dict()
    assert state.metadata["orca_run_status"]["ok"] is True


@pytest.mark.parametrize("fault", [
    {"omit_gate": True},
    {"reply": None},
    {"worker-release": None},
    {"worker-release": {"state": "release_pending"}},
    {"worker-list": {"workers": [{"dispatchId": "d1"}]}},
    {"worker-list": {}},
    {"run-show": None},
    {"run-show": {"run": {"id": "r1", "state": "failed"}}},
    {"run-show": {"run": {"id": "other"}}},
    {"task-list": {"tasks": [{"id": "t1", "status": "dispatched"}]}},
    {"task-list": {"tasks": [{"id": "t1", "status": "failed"}]}},
    {"task-list": {}},
])
def test_production_mode_c_fails_closed(coordinator_rpc, tmp_path, fault):
    adapter, _, faults = coordinator_rpc
    faults.update(fault)
    state = _coordinator_controller(adapter, tmp_path).run_all()
    assert state.failed and state.stopped
    assert state.metadata["orca_run_status"]["ok"] is False


def test_local_only_coordinator_cannot_use_unpinned_opencode(coordinator_rpc, tmp_path):
    adapter, calls, _ = coordinator_rpc
    from tests.fakes.providers import fake_local_worker
    controller = _coordinator_controller(adapter, tmp_path)
    controller.bindings.providers = [fake_local_worker().probe()]
    controller.bindings.local_enabled = True
    controller.bindings.local_model_ref = "ollama/qwen"
    controller.bindings.local_endpoint = "http://localhost:11434"
    state = controller.run_all()
    assert state.failed and state.stopped
    assert "cannot yet pin" in state.gate_outcomes[GateKind.ORCA_HANDOFF.value].detail
    assert not any("worker-start" in c or "task-create" in c for c in calls)


@pytest.mark.parametrize("role", ["lead_implement", "research", "test_writer", "doc_writer", "lead_review"])
def test_legacy_mutations_require_authority(coordinator_rpc, monkeypatch, role):
    adapter, calls, _ = coordinator_rpc
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE")
    result = adapter.execute_task(ProviderTaskRequest(prompt="change", role=role,
        context={"run_id": "r1"}))
    assert not result.ok
    assert not any(c[1] == "orchestration" for c in calls)
