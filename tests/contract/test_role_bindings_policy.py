"""Executable role contracts: exact targets and canonical dispatch evidence."""
from dataclasses import replace
import pytest

from aichestra.config.roles import RoleBinding, load_role_bindings
from aichestra.execution.compatibility import load_compatibility_bindings
from aichestra.execution.domain import Model, ModelProvider
from aichestra.execution.roles import RoleBindingResolver, validate_role_receipts
from tests.fakes.providers import fake_execution_targets


def test_roles_create_exact_compatibility_and_resolve_registered_runtime():
    cfg = {"execution": {"runtimes": {"new-agent": {}}},
           "roles": {"tests": {"runtime": "new-agent", "provider": "beeline", "model": "Qwen3.6-27B-textonly"}}}
    binding = load_role_bindings(cfg)["tests"]
    rows = load_compatibility_bindings(cfg)
    assert any((r.runtime, r.provider, r.model) == (binding.runtime, binding.provider, binding.model) for r in rows)
    target = replace(fake_execution_targets("new-agent")[0],
                     provider=ModelProvider("beeline", available=True),
                     model=Model(binding.model, "beeline", available=True))
    assert RoleBindingResolver([target]).resolve("tests", binding) == target
    with pytest.raises(ValueError, match="roles.tests"):
        RoleBindingResolver([replace(target, enabled=False)]).resolve("tests", binding)
    with pytest.raises(ValueError, match="roles.tests"):
        RoleBindingResolver([target]).resolve("tests", replace(binding, model="wrong"))


def receipt(dispatch="d1", role="tests", runtime="codex", model=None):
    task = {"id": "t1", "run_id": "r1", "title": role}
    worker = {"dispatch_id": dispatch, "task_id": "t1", "run_id": "r1"}
    payload = {"worker": worker, "launch": {"effective": {"agent": runtime, "model": model}}}
    return task, worker, payload


def adopted(task, worker, target, reason="primary"):
    return {
        "operation": "aichestra.dispatch_role",
        "run_id": task.get("run_id") or task.get("runId"),
        "task_id": task.get("id") or task.get("taskId"),
        "role": task.get("role") or task.get("task_title") or task.get("title"),
        "dispatch_id": worker.get("dispatch_id") or worker.get("dispatchId"),
        "execution_target_id": target.id,
        "reason": reason,
    }


def test_canonical_receipts_enforce_role_model_and_run():
    target = replace(fake_execution_targets()[0], model=Model("pinned", "codex", available=True))
    task, worker, payload = receipt(model="pinned")
    def audit():
        return validate_role_receipts(
            "r1", [task], [worker], {"d1": payload}, {"tests": target},
            dispatch_role_receipts=[adopted(task, worker, target)],
        )
    assert audit()["dispatches_checked"] == 1
    payload["launch"]["effective"]["model"] = "other"
    with pytest.raises(ValueError, match="violates binding"):
        audit()
    payload["launch"]["effective"]["model"] = "pinned"
    worker["run_id"] = "other"
    with pytest.raises(ValueError, match="Cross-Run"):
        audit()
    worker["run_id"] = "r1"
    task["title"] = "undeclared"
    with pytest.raises(ValueError, match="undeclared role"):
        audit()


def test_validate_role_receipts_endpoint_v1_matches_launch_proof():
    """Post-dispatch audit must use the same /api ≡ /api/v1 endpoint identity."""
    target = replace(
        fake_execution_targets()[0],
        endpoint="https://models.example/api?tenant=A",
    )
    task, worker, payload = receipt()
    payload["launch"]["effective"]["endpoint"] = (
        "https://models.example/api/v1?tenant=A"
    )
    receipts = [adopted(task, worker, target)]
    assert validate_role_receipts(
        "r1", [task], [worker], {"d1": payload}, {"tests": target},
        dispatch_role_receipts=receipts,
    )["ok"]

    payload["launch"]["effective"]["endpoint"] = (
        "https://models.example/api/v1?tenant=B"
    )
    with pytest.raises(ValueError, match="violates binding"):
        validate_role_receipts(
            "r1", [task], [worker], {"d1": payload}, {"tests": target},
            dispatch_role_receipts=receipts,
        )


def test_fallback_receipt_requires_auto_exact_target_and_prior_quota():
    primary = fake_execution_targets()[0]
    fallback = fake_execution_targets("cursor")[0]
    task, first, p1 = receipt(role="implement")
    _, second, p2 = receipt(dispatch="d2", role="implement", runtime="cursor")
    first.update(failure="quota", completed_at="2026-01-01T00:00:00Z")
    second["created_at"] = "2026-01-01T00:00:01Z"
    def audit(mode="auto"):
        return validate_role_receipts(
            "r1", [task], [first, second], {"d1": p1, "d2": p2},
            {"implement": primary}, quota_target=fallback, quota_mode=mode,
            dispatch_role_receipts=[
                adopted(task, first, primary),
                adopted(task, second, fallback, reason="quota-fallback"),
            ],
        )
    assert audit()["ok"]
    with pytest.raises(ValueError):
        audit("manual")
    first["failure"] = "auth"
    with pytest.raises(ValueError, match="violates binding"):
        audit()
    first["failure"] = "quota"
    second["created_at"] = "2025-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="violates binding"):
        audit()


def test_coordinator_receipt_cannot_use_implement_quota_fallback():
    primary = fake_execution_targets()[0]
    fallback = fake_execution_targets("cursor")[0]
    task, first, p1 = receipt(role="mode_c_handoff")
    _, second, p2 = receipt(dispatch="d2", role="mode_c_handoff", runtime="cursor")
    first.update(failure="quota", completed_at="2026-01-01T00:00:00Z")
    second["created_at"] = "2026-01-01T00:00:01Z"
    with pytest.raises(ValueError, match="violates binding"):
        validate_role_receipts(
            "r1",
            [task],
            [first, second],
            {"d1": p1, "d2": p2},
            {"implement": primary},
            bootstrap_target=primary,
            quota_target=fallback,
            quota_mode="auto",
        )


def test_installed_orca_worker_shape_fails_without_effective_launch_evidence():
    target = fake_execution_targets()[0]
    task = {"id": "t", "run_id": "r", "task_title": "tests"}
    worker = {"dispatchId": "d", "taskId": "t", "runId": "r"}
    # Bare startOptions.agent is requested preference only — not durable evidence.
    payload = {"dispatch": {"id": "d", "task_id": "t", "run_id": "r"},
               "worker": {"dispatch_id": "d", "state": "succeeded",
                          "startOptions": {"agent": "codex"}}}
    adoption = [adopted(task, worker, target)]
    with pytest.raises(ValueError, match="no effective launch binding"):
        validate_role_receipts(
            "r", [task], [worker], {"d": payload}, {"tests": target},
            dispatch_role_receipts=adoption,
        )
    # Live Orca 1.4+ nests durable launch.effective under startOptions.launch.
    payload["worker"]["startOptions"] = {
        "agent": "codex",
        "launch": {
            "requested": {"agent": "codex", "model": None},
            "effective": {"agent": "codex", "model": None},
        },
    }
    assert validate_role_receipts(
        "r", [task], [worker], {"d": payload}, {"tests": target},
        dispatch_role_receipts=adoption,
    )["ok"]
    # Top-level launch.effective remains accepted.
    payload = {"dispatch": {"id": "d", "task_id": "t", "run_id": "r"},
               "worker": {"dispatch_id": "d", "state": "succeeded"},
               "launch": {"effective": {"agent": "codex"}}}
    assert validate_role_receipts(
        "r", [task], [worker], {"d": payload}, {"tests": target},
        dispatch_role_receipts=adoption,
    )["ok"]
    # JSON twin start_options string (Orca worker-show) also counts.
    import json
    nested = {
        "agent": "codex",
        "launch": {"requested": {"agent": "codex"}, "effective": {"agent": "codex"}},
    }
    payload = {"dispatch": {"id": "d", "task_id": "t", "run_id": "r"},
               "worker": {"dispatch_id": "d", "state": "succeeded",
                          "start_options": json.dumps(nested)}}
    assert validate_role_receipts(
        "r", [task], [worker], {"d": payload}, {"tests": target},
        dispatch_role_receipts=adoption,
    )["ok"]


def test_live_orca_worker_show_envelope_audits_start_options_launch():
    """Regression: full worker-show envelope from Orca 1.4.198 Mode C settle."""
    target = fake_execution_targets("cursor")[0]
    task = {"id": "task_f1fdf8c349dc", "run_id": "run_503f0c73fcce",
            "task_title": "mode_c_handoff"}
    worker = {"dispatchId": "ctx_504d404beaea", "taskId": "task_f1fdf8c349dc",
              "runId": "run_503f0c73fcce"}
    payload = {
        "dispatch": {
            "id": "ctx_504d404beaea",
            "run_id": "run_503f0c73fcce",
            "task_id": "task_f1fdf8c349dc",
            "status": "completed",
        },
        "worker": {
            "dispatch_id": "ctx_504d404beaea",
            "state": "succeeded",
            "startOptions": {
                "worktree": "current",
                "agent": "cursor",
                "launch": {
                    "requested": {"agent": "cursor", "model": None, "effort": None},
                    "effective": {"agent": "cursor", "model": None, "effort": None},
                },
            },
        },
    }
    assert validate_role_receipts(
        "run_503f0c73fcce",
        [task],
        [worker],
        {"ctx_504d404beaea": payload},
        {},
        bootstrap_target=target,
    )["ok"]


def test_inner_worker_requires_dispatch_role_adoption_receipt():
    target = fake_execution_targets()[0]
    task, worker, payload = receipt()
    with pytest.raises(ValueError, match="dispatch-role adoption receipt"):
        validate_role_receipts("r1", [task], [worker], {"d1": payload}, {"tests": target})
    wrong = adopted(task, worker, target)
    wrong["dispatch_id"] = "other"
    with pytest.raises(ValueError, match="dispatch-role adoption receipt"):
        validate_role_receipts(
            "r1", [task], [worker], {"d1": payload}, {"tests": target},
            dispatch_role_receipts=[wrong],
        )
    mismatch = adopted(task, worker, fake_execution_targets("cursor")[0])
    with pytest.raises(ValueError, match="adoption receipt target mismatch"):
        validate_role_receipts(
            "r1", [task], [worker], {"d1": payload}, {"tests": target},
            dispatch_role_receipts=[mismatch],
        )
    assert validate_role_receipts(
        "r1", [task], [worker], {"d1": payload}, {"tests": target},
        dispatch_role_receipts=[adopted(task, worker, target)],
    )["ok"]


def test_coordinator_bootstrap_does_not_require_dispatch_role_receipt():
    target = fake_execution_targets()[0]
    task, worker, payload = receipt(role="mode_c_handoff")
    assert validate_role_receipts(
        "r1", [task], [worker], {"d1": payload}, {},
        bootstrap_target=target,
    )["ok"]


def test_live_inner_worker_dispatch_role_envelope_audits_launch_effective():
    """Live Orca 1.4.200 (2026-09-11): coordinator-driven dispatch-role inner worker."""
    target = fake_execution_targets("cursor")[0]
    task = {
        "id": "task_a04c1c7f110b",
        "run_id": "run_46902f941734",
        "task_title": "tests",
        "status": "completed",
    }
    worker = {
        "dispatchId": "ctx_fc0f8a58b7b5",
        "taskId": "task_a04c1c7f110b",
        "runId": "run_46902f941734",
    }
    payload = {
        "dispatch": {
            "id": "ctx_fc0f8a58b7b5",
            "runId": "run_46902f941734",
            "taskId": "task_a04c1c7f110b",
            "status": "completed",
            "creatorDispatchId": "ctx_45ea46610b16",
        },
        "worker": {
            "dispatchId": "ctx_fc0f8a58b7b5",
            "state": "succeeded",
            "startOptions": {
                "agent": "cursor",
                "launch": {
                    "requested": {"agent": "cursor", "model": None, "effort": None},
                    "effective": {"agent": "cursor", "model": None, "effort": None},
                },
            },
        },
    }
    adoption = adopted(task, worker, target)
    assert validate_role_receipts(
        "run_46902f941734",
        [task],
        [worker],
        {"ctx_fc0f8a58b7b5": payload},
        {"tests": target},
        dispatch_role_receipts=[adoption],
    )["ok"]
    with pytest.raises(ValueError, match="dispatch-role adoption receipt"):
        validate_role_receipts(
            "run_46902f941734",
            [task],
            [worker],
            {"ctx_fc0f8a58b7b5": payload},
            {"tests": target},
        )


def test_provisionable_worker_contract_is_internally_consistent():
    from aichestra.execution.domain import (
        AgentRuntime,
        Compatibility,
        DiscoveryFacts,
        ExecutionCapabilities as Caps,
        LaunchCapability,
        LaunchStrategy,
        Locality,
        Model,
        ModelProvider,
    )
    from aichestra.execution.roles import (
        assert_role_dispatch_package_consistent,
        target_contract_entry,
    )
    from aichestra.execution.serialize import serialize_execution_target, serialize_launch_candidate
    from aichestra.execution.targets import resolve_targets

    runtime = AgentRuntime("opencode", True, binary_path="/usr/bin/opencode")
    provider = ModelProvider(
        "ollama", True, endpoint="http://127.0.0.1:11434", locality=Locality.LOCAL
    )
    model = Model("qwen", "ollama", True, capabilities=Caps(frozenset({"code"})))
    bridge = resolve_targets(
        DiscoveryFacts(runtimes=(runtime,), providers=(provider,), models=(model,)),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=(
            LaunchCapability(
                "opencode",
                "ollama",
                "qwen",
                endpoint="http://127.0.0.1:11434",
                strategy=LaunchStrategy.ORCA_TERMINAL_BRIDGE,
                proven=False,
            ),
        ),
    )[0]
    assert bridge.provisionable and not bridge.runnable
    entry = target_contract_entry(bridge)
    assert entry["state"] == "provisionable"
    assert entry["requires_launch_proof"] is True
    assert entry["candidate_id"] == bridge.id
    package = {
        "execution_targets": [],
        "execution_target_candidates": [serialize_launch_candidate(bridge)],
        "role_dispatch_contract": {"bindings": {"tests": entry}},
    }
    assert_role_dispatch_package_consistent(package)
    with pytest.raises(ValueError, match="missing from execution_target_candidates"):
        assert_role_dispatch_package_consistent({
            "execution_targets": [],
            "execution_target_candidates": [],
            "role_dispatch_contract": {"bindings": {"tests": entry}},
        })
    runnable = fake_execution_targets()[0]
    runnable_entry = target_contract_entry(runnable)
    assert runnable_entry["state"] == "runnable"
    assert runnable_entry["requires_launch_proof"] is False
    assert_role_dispatch_package_consistent({
        "execution_targets": [serialize_execution_target(runnable)],
        "execution_target_candidates": [],
        "role_dispatch_contract": {"bindings": {"implement": runnable_entry}},
    })


def test_dispatch_role_pins_exact_bound_target(tmp_path, monkeypatch):
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.serialize import ROLE_DISPATCH_OPERATION

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        from types import SimpleNamespace
        if "task-list" in argv:
            return SimpleNamespace(returncode=0, stdout='{"tasks":[{"id":"task-1","run_id":"run-1","role":"tests"}]}', stderr="")
        if "run-use" in argv:
            raise AssertionError("dispatch-role must not call run-use (ownership transfer)")
        if "worker-start" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"result":{"dispatchId":"d-pinned"}}',
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_orca_binary", lambda: "orca-test"
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.trusted_config_root", lambda _p: True
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_aichestra_config_root",
        lambda **_k: tmp_path,
    )
    target = fake_execution_targets("cursor")[0]
    cfg = {
        "roles": {
            "tests": {"runtime": "cursor"},
            "implement": {"runtime": "codex"},
            "research": {"runtime": "codex"},
            "docs": {"runtime": "codex"},
        }
    }
    from aichestra.execution.run_contract import save_contract
    from aichestra.execution.roles import target_contract_entry
    save_contract(tmp_path, "run-1", tmp_path, {
        "bindings": {"tests": target_contract_entry(target)}, "quota": {"mode": "manual"}})
    # Settings drift must not change the old Run's worker.
    cfg["roles"]["tests"] = {"runtime": "codex"}
    payload = dispatch_role(
        role="tests",
        run_id="run-1",
        task_id="task-1",
        project_root=tmp_path,
        repo_root=tmp_path,
        config=cfg,
        targets=fake_execution_targets() + fake_execution_targets("cursor"),
        binary="orca-test",
        from_handle="term-coordinator",
        run=fake_run,
    )
    assert payload["ok"] is True
    assert payload["operation"] == ROLE_DISPATCH_OPERATION
    assert payload["execution_target_id"] == target.id
    assert payload["dispatch_id"] == "d-pinned"
    assert payload["adoption_receipt_persisted"] is True
    from aichestra.execution.run_contract import load_dispatch_role_receipts
    stored = load_dispatch_role_receipts(tmp_path, "run-1")
    assert stored[0]["dispatch_id"] == "d-pinned"
    assert stored[0]["role"] == "tests"
    assert stored[0]["execution_target_id"] == target.id
    start = next(c for c in calls if "worker-start" in c)
    assert "--agent" in start
    assert start[start.index("--agent") + 1] == "cursor"
    assert start[start.index("--task") + 1] == "task-1"
    assert start[start.index("--run") + 1] == "run-1"
    assert start[start.index("--from") + 1] == "term-coordinator"
    assert "run-use" not in {c[2] for c in calls if len(c) > 2}

    wrong = dispatch_role(
        role="research",
        run_id="run-1",
        task_id="task-2",
        project_root=tmp_path,
        repo_root=tmp_path,
        config={
            "roles": {
                "research": {"runtime": "missing-agent"},
                "implement": {"runtime": "codex"},
                "tests": {"runtime": "codex"},
                "docs": {"runtime": "codex"},
            }
        },
        targets=fake_execution_targets(),
        binary="orca-test",
        run=fake_run,
    )
    assert wrong["ok"] is False
    assert "unavailable" in wrong["error"] or "unknown" in wrong["error"]


def test_run_contract_is_immutable_and_project_bound(tmp_path):
    from aichestra.execution.run_contract import save_contract, load_contract
    contract = {"bindings": {"tests": {"execution_target_id": "original"}}}
    save_contract(tmp_path, "../run", tmp_path, contract)
    save_contract(tmp_path, "../run", tmp_path, contract)
    with pytest.raises(ValueError, match="different policy"):
        save_contract(tmp_path, "../run", tmp_path, {"bindings": {}})
    assert load_contract(tmp_path, "../run", tmp_path) == contract
    with pytest.raises(ValueError, match="mismatch"):
        load_contract(tmp_path, "../run", tmp_path / "other")
    with pytest.raises(ValueError, match="missing"):
        load_contract(tmp_path, "absent", tmp_path)


@pytest.mark.parametrize("fault", [None, "manual", "missing", "cross-run", "wrong-primary", "future", "wrong-role", "disabled"])
def test_quota_fallback_authorized_before_worker_start(tmp_path, monkeypatch, fault):
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.run_contract import save_contract
    from aichestra.execution.roles import target_contract_entry
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    primary = fake_execution_targets("codex")[0]
    fallback = fake_execution_targets("cursor")[0]
    contract = {"bindings": {"implement": target_contract_entry(primary)},
                "quota": {"mode": "manual" if fault == "manual" else "auto",
                          "roles": {"implement": target_contract_entry(fallback)}}}
    save_contract(tmp_path, "r1", tmp_path, contract)
    task, worker, payload = receipt(role="implement")
    worker.update(failure="quota", completed_at="2026-01-01T00:00:00Z")
    if fault == "cross-run":
        worker["run_id"] = "other"
    if fault == "wrong-primary":
        payload["launch"]["effective"]["agent"] = "cursor"
    if fault == "future":
        worker["completed_at"] = "9999-01-01T00:00:00Z"
    if fault == "wrong-role":
        task["title"] = "tests"
    calls = []
    def run(argv, **kwargs):
        command = argv[2]
        calls.append(command)
        if command == "run-use":
            raise AssertionError("dispatch-role must not call run-use")
        data = {"task-list": {"tasks": [task]},
                "worker-list": {"workers": [] if fault == "missing" else [worker]},
                "worker-show": payload,
                "worker-start": {"dispatchId": "fallback"}}[command]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
    result = dispatch_role(role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
                           project_root=tmp_path, repo_root=tmp_path, config={},
                           targets=(primary, replace(fallback, enabled=fault != "disabled")),
                           binary="orca-test", run=run)
    assert result["ok"] is (fault is None), result
    assert ("worker-start" in calls) is (fault is None)
    assert "run-use" not in calls
    if fault is None:
        assert result["execution_target_id"] == fallback.id


def test_quota_fallback_accepts_nested_start_options_launch(tmp_path, monkeypatch):
    """Orca 1.4+ nests durable effective under startOptions.launch — authorize_task
    must use receipt_launch, not top-level launch only."""
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.run_contract import save_contract
    from aichestra.execution.roles import target_contract_entry
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    primary = fake_execution_targets("codex")[0]
    fallback = fake_execution_targets("cursor")[0]
    contract = {"bindings": {"implement": target_contract_entry(primary)},
                "quota": {"mode": "auto",
                          "roles": {"implement": target_contract_entry(fallback)}}}
    save_contract(tmp_path, "r1", tmp_path, contract)
    task = {"id": "t1", "run_id": "r1", "title": "implement"}
    worker = {"dispatch_id": "d1", "task_id": "t1", "run_id": "r1",
              "failure": "quota", "completed_at": "2026-01-01T00:00:00Z"}
    nested_ok = {
        "worker": {
            **worker,
            "startOptions": {
                "agent": "codex",
                "launch": {
                    "requested": {"agent": "codex"},
                    "effective": {"agent": "codex", "model": None},
                },
            },
        },
    }
    bare_preference = {
        "worker": {**worker, "startOptions": {"agent": "codex"}},
    }

    def run_factory(payload):
        calls = []

        def run(argv, **kwargs):
            command = argv[2]
            calls.append(command)
            if command == "run-use":
                raise AssertionError("dispatch-role must not call run-use")
            data = {"task-list": {"tasks": [task]},
                    "worker-list": {"workers": [worker]},
                    "worker-show": payload,
                    "worker-start": {"dispatchId": "fallback"}}[command]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

        return run, calls

    run_ok, calls_ok = run_factory(nested_ok)
    ok = dispatch_role(role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
                       project_root=tmp_path, repo_root=tmp_path, config={},
                       targets=(primary, fallback), binary="orca-test", run=run_ok)
    assert ok["ok"] is True, ok
    assert "worker-start" in calls_ok
    assert ok["execution_target_id"] == fallback.id

    run_bare, calls_bare = run_factory(bare_preference)
    bare = dispatch_role(role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
                         project_root=tmp_path, repo_root=tmp_path, config={},
                         targets=(primary, fallback), binary="orca-test", run=run_bare)
    assert bare["ok"] is False
    assert "worker-start" not in calls_bare


def test_quota_fallback_accepts_orca_last_failure_json(tmp_path, monkeypatch):
    """Live Orca stores completedAt + lastFailure JSON; typed failure must be explicit."""
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.run_contract import save_contract
    from aichestra.execution.roles import target_contract_entry
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    primary = fake_execution_targets("codex")[0]
    fallback = fake_execution_targets("cursor")[0]
    contract = {"bindings": {"implement": target_contract_entry(primary)},
                "quota": {"mode": "auto",
                          "roles": {"implement": target_contract_entry(fallback)}}}
    save_contract(tmp_path, "r1", tmp_path, contract)
    task = {"id": "t1", "run_id": "r1", "title": "implement"}
    worker = {"dispatchId": "d1", "taskId": "t1", "runId": "r1",
              "completedAt": "2026-01-01T00:00:00Z",
              "lastFailure": json.dumps({
                  "provenance": "worker_report",
                  "outcome": "failed",
                  "failure": "quota",
                  "completedAt": "2026-01-01T00:00:00Z",
              })}
    prose_only = {
        "worker": {
            "dispatchId": "d1", "taskId": "t1", "runId": "r1", "state": "failed",
            "startOptions": {"launch": {"effective": {"agent": "codex"}}},
        },
        "dispatch": {
            "id": "d1", "taskId": "t1", "runId": "r1", "status": "failed",
            "completedAt": "2026-01-01T00:00:00Z",
            "lastFailure": json.dumps({
                "provenance": "worker_report",
                "outcome": "failed",
                "subject": "primary implement quota",
                "body": "hit provider quota",
            }),
        },
    }
    typed = {
        "worker": {
            "dispatchId": "d1", "taskId": "t1", "runId": "r1", "state": "failed",
            "startOptions": {"launch": {"effective": {"agent": "codex"}}},
        },
        "dispatch": {**worker, "id": "d1", "status": "failed"},
    }

    def run_factory(payload):
        calls = []

        def run(argv, **kwargs):
            command = argv[2]
            calls.append(command)
            if command == "run-use":
                raise AssertionError("dispatch-role must not call run-use")
            if command == "run-show":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "result": {"run": {"id": "r1", "coordinator_handle": "term-c"}},
                    }),
                    stderr="",
                )
            if command == "check":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"ok": True, "result": {"messages": []}}),
                    stderr="",
                )
            data = {"task-list": {"tasks": [task]},
                    "worker-list": {"workers": [worker]},
                    "worker-show": payload,
                    "worker-start": {"dispatchId": "fallback"}}[command]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

        return run, calls

    run_typed, calls_typed = run_factory(typed)
    ok = dispatch_role(role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
                       project_root=tmp_path, repo_root=tmp_path, config={},
                       targets=(primary, fallback), binary="orca-test", run=run_typed)
    assert ok["ok"] is True, ok
    assert "worker-start" in calls_typed

    run_prose, calls_prose = run_factory(prose_only)
    bare = dispatch_role(role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
                         project_root=tmp_path, repo_root=tmp_path, config={},
                         targets=(primary, fallback), binary="orca-test", run=run_prose)
    assert bare["ok"] is False
    assert "worker-start" not in calls_prose


def test_quota_fallback_accepts_worker_done_payload_failure(tmp_path, monkeypatch):
    """Orca 1.4.200 stores failure=quota on worker_done payload but omits it from
    lastFailure — authorize_task must read check --all mail without run-use."""
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.run_contract import save_contract
    from aichestra.execution.roles import target_contract_entry
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    primary = fake_execution_targets("codex")[0]
    fallback = fake_execution_targets("cursor")[0]
    contract = {"bindings": {"implement": target_contract_entry(primary)},
                "quota": {"mode": "auto",
                          "roles": {"implement": target_contract_entry(fallback)}}}
    save_contract(tmp_path, "r1", tmp_path, contract)
    task = {"id": "t1", "run_id": "r1", "title": "implement"}
    worker = {"dispatchId": "d1", "taskId": "t1", "runId": "r1",
              "completedAt": "2026-01-01T00:00:00Z",
              "lastFailure": json.dumps({
                  "provenance": "worker_report",
                  "outcome": "failed",
                  "messageId": "msg_1",
                  "subject": "primary implement quota",
                  "body": "hit provider quota",
                  "completedAt": "2026-01-01T00:00:00Z",
              })}
    show = {
        "worker": {
            "dispatchId": "d1", "taskId": "t1", "runId": "r1", "state": "failed",
            "startOptions": {"launch": {"effective": {"agent": "codex"}}},
        },
        "dispatch": {**worker, "id": "d1", "status": "failed"},
    }
    mail = {
        "ok": True,
        "result": {
            "messages": [{
                "id": "msg_1",
                "type": "worker_done",
                "payload": json.dumps({
                    "taskId": "t1",
                    "dispatchId": "d1",
                    "outcome": "failed",
                    "failure": "quota",
                }),
            }],
        },
    }
    prose_mail = {
        "ok": True,
        "result": {
            "messages": [{
                "id": "msg_1",
                "type": "worker_done",
                "payload": json.dumps({
                    "taskId": "t1",
                    "dispatchId": "d1",
                    "outcome": "failed",
                }),
            }],
        },
    }

    def run_factory(check_payload):
        calls = []

        def run(argv, **kwargs):
            command = argv[2]
            calls.append(command)
            if command == "run-use":
                raise AssertionError("dispatch-role must not call run-use")
            if command == "check":
                assert "--all" in argv and "worker_done" in argv
                assert "--terminal" in argv
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps(check_payload), stderr=""
                )
            if command == "run-show":
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "result": {"run": {"id": "r1", "coordinator_handle": "term-c"}},
                    }),
                    stderr="",
                )
            data = {"task-list": {"tasks": [task]},
                    "worker-list": {"workers": [worker]},
                    "worker-show": show,
                    "worker-start": {"dispatchId": "fallback"}}[command]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

        return run, calls

    run_ok, calls_ok = run_factory(mail)
    ok = dispatch_role(
        role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
        project_root=tmp_path, repo_root=tmp_path, config={},
        targets=(primary, fallback), binary="orca-test", run=run_ok,
        from_handle="term-c",
    )
    assert ok["ok"] is True, ok
    assert "worker-start" in calls_ok
    assert "check" in calls_ok
    assert "run-use" not in calls_ok

    run_prose, calls_prose = run_factory(prose_mail)
    bare = dispatch_role(
        role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
        project_root=tmp_path, repo_root=tmp_path, config={},
        targets=(primary, fallback), binary="orca-test", run=run_prose,
        from_handle="term-c",
    )
    assert bare["ok"] is False
    assert "worker-start" not in calls_prose


@pytest.mark.parametrize("changed_role", ["cursor", {"runtime": "opencode", "provider": "beeline", "model": "new-model"}])
@pytest.mark.parametrize("fault", [None, "disabled", "wrong-terminal"])
def test_saved_provider_binding_uses_production_resolver(tmp_path, monkeypatch, changed_role, fault):
    import json
    from types import SimpleNamespace
    from aichestra.execution import serialize
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.domain import AgentRuntime, DiscoveryFacts, LaunchCapability, LaunchStrategy
    from aichestra.execution.roles import target_contract_entry
    from aichestra.execution.run_contract import save_contract

    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    provider = ModelProvider("beeline", available=True, endpoint="https://models.example/api?tenant=A")
    facts = DiscoveryFacts(runtimes=(AgentRuntime("opencode", available=True),),
                           providers=(provider,), models=(Model("Qwen", "beeline", available=True),))
    # Only external discovery/launch capabilities are substituted. Compatibility,
    # policy, target resolution and dispatch authorization are production code.
    monkeypatch.setattr(serialize, "discover_execution_facts", lambda *a, **kw: facts)
    monkeypatch.setattr("aichestra.execution.launch_strategies.discover_launches", lambda *a, **kw: (
        LaunchCapability("opencode", "beeline", "Qwen", endpoint=provider.endpoint,
                         strategy=LaunchStrategy.ORCA_EXISTING_TERMINAL, proven=True,
                         launch_ref="bound-terminal"),))
    cfg = {"roles": {"tests": {"runtime": "opencode", "provider": "beeline", "model": "Qwen"}}}
    original, _, _ = serialize.resolve_mode_c_execution(cfg)
    target = next(t for t in original if t.model and t.model.id == "Qwen")
    assert target.dispatchable
    save_contract(tmp_path, "r1", tmp_path, {"bindings": {"tests": target_contract_entry(target)}})
    cfg["roles"]["tests"] = changed_role
    if fault == "disabled":
        cfg["execution"] = {"bindings": [{"runtime": "opencode", "provider": "beeline", "enabled": False}]}
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "terminal":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"binding": {
                "runtime": "opencode", "provider": "beeline", "model": "Qwen",
                "endpoint": "https://models.example/api?tenant=B" if fault == "wrong-terminal" else provider.endpoint}}), stderr="")
        data = {"task-list": {"tasks": [{"id": "t1", "run_id": "r1", "role": "tests"}]},
                "worker-start": {"dispatchId": "d1"}}[argv[2]]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
    result = dispatch_role(role="tests", run_id="r1", task_id="t1", project_root=tmp_path,
                           repo_root=tmp_path, config=cfg, binary="orca-test", run=run)
    assert result["ok"] is (fault is None), result
    assert any("worker-start" in c for c in calls) is (fault is None)
    assert not any("run-use" in c for c in calls)
    if fault is None:
        assert result["execution_target_id"] == target.id
        start = next(c for c in calls if "worker-start" in c)
        assert start[start.index("--terminal") + 1] == "bound-terminal"
        assert "--agent" not in start


def test_endpoint_identity_preserves_tenant_without_exposing_it():
    from aichestra.execution.roles import target_contract_entry, contract_endpoint_matches
    from aichestra.execution.launch_strategies import extract_attested_binding
    target = replace(fake_execution_targets()[0], endpoint="https://host/api/?tenant=A")
    entry = target_contract_entry(target)
    assert "tenant" not in entry["endpoint"]
    assert contract_endpoint_matches(entry, "https://host/api?tenant=A")
    assert not contract_endpoint_matches(entry, "https://host/api?tenant=B")
    actual = extract_attested_binding({"effective": {"agent": "codex", "endpoint": target.endpoint}})
    assert contract_endpoint_matches(entry, actual["endpoint"])
    legacy = dict(entry)
    del legacy["endpoint_fingerprint"]
    assert not contract_endpoint_matches(legacy, target.endpoint)


def test_settle_audit_accepts_orca_worker_done_payload_quota():
    """Orca 1.4.200: lastFailure omits typed failure; worker_done payload.failure=quota
    must authorize final validate_role_receipts the same way as authorize_task."""
    import json
    from aichestra.execution.roles import _worker_record, resolve_worker_failure

    primary = fake_execution_targets("codex")[0]
    fallback = fake_execution_targets("cursor")[0]
    task = {"id": "t1", "run_id": "r1", "title": "implement", "status": "completed"}
    primary_worker = {
        "dispatchId": "d1",
        "taskId": "t1",
        "runId": "r1",
        "completedAt": "2026-01-01T00:00:00Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "lastFailure": json.dumps({
            "provenance": "worker_report",
            "outcome": "failed",
            "messageId": "msg_1",
            "subject": "primary implement quota",
            "body": "hit provider quota",
            "completedAt": "2026-01-01T00:00:00Z",
        }),
    }
    primary_show = {
        "worker": {
            "dispatchId": "d1",
            "taskId": "t1",
            "runId": "r1",
            "state": "failed",
            "startOptions": {"launch": {"effective": {"agent": "codex"}}},
        },
        "dispatch": {**primary_worker, "id": "d1", "status": "failed"},
    }
    _, fallback_worker, fallback_show = receipt(
        dispatch="d2", role="implement", runtime="cursor"
    )
    fallback_worker["created_at"] = "2026-01-01T00:00:01Z"
    fallback_show["worker"]["created_at"] = "2026-01-01T00:00:01Z"
    mail = [{
        "id": "msg_1",
        "type": "worker_done",
        "payload": json.dumps({
            "taskId": "t1",
            "dispatchId": "d1",
            "outcome": "failed",
            "failure": "quota",
        }),
    }]
    _, primary_row = _worker_record(primary_show)
    assert resolve_worker_failure(primary_row) is None
    assert resolve_worker_failure(primary_row, durable_messages=mail) == "quota"

    adoption = [
        adopted(task, {"dispatch_id": "d1"}, primary),
        adopted(task, fallback_worker, fallback, reason="quota-fallback"),
    ]
    with pytest.raises(ValueError, match="violates binding"):
        validate_role_receipts(
            "r1",
            [task],
            [primary_worker, fallback_worker],
            {"d1": primary_show, "d2": fallback_show},
            {"implement": primary},
            quota_target=fallback,
            quota_mode="auto",
            dispatch_role_receipts=adoption,
        )
    assert validate_role_receipts(
        "r1",
        [task],
        [primary_worker, fallback_worker],
        {"d1": primary_show, "d2": fallback_show},
        {"implement": primary},
        quota_target=fallback,
        quota_mode="auto",
        dispatch_role_receipts=adoption,
        durable_messages=mail,
    )["ok"]


def test_dispatch_role_fails_when_adoption_receipt_persist_fails(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.roles import target_contract_entry
    from aichestra.execution.run_contract import save_contract

    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    target = fake_execution_targets("cursor")[0]
    save_contract(
        tmp_path,
        "run-1",
        tmp_path,
        {"bindings": {"tests": target_contract_entry(target)}, "quota": {"mode": "manual"}},
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "task-list" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"tasks":[{"id":"task-1","run_id":"run-1","role":"tests"}]}',
                stderr="",
            )
        if "worker-start" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"result":{"dispatchId":"d-persist-fail"}}',
                stderr="",
            )
        if "worker-release" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"result": {"dispatchId": "d-persist-fail", "state": "released"}}),
                stderr="",
            )
        if "worker-show" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="skip")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.save_dispatch_role_receipt", boom
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_orca_binary", lambda: "orca-test"
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.trusted_config_root", lambda _p: True
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_aichestra_config_root",
        lambda **_k: tmp_path,
    )
    result = dispatch_role(
        role="tests",
        run_id="run-1",
        task_id="task-1",
        project_root=tmp_path,
        repo_root=tmp_path,
        config={"roles": {"tests": {"runtime": "cursor"}}},
        targets=fake_execution_targets("cursor"),
        binary="orca-test",
        run=fake_run,
    )
    assert result["ok"] is False
    assert result["worker_started"] is True
    assert result["adoption_receipt_persisted"] is False
    assert result["dispatch_id"] == "d-persist-fail"
    assert "adoption receipt persistence failed" in result["error"]
    assert result["worker_release_attempted"] is True
    assert result["worker_release_ok"] is True
    assert any("worker-release" in c for c in calls)


def test_dispatch_role_release_pending_is_not_release_ok(tmp_path, monkeypatch):
    """Exit 0 + state=release_pending must not report worker_release_ok."""
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.roles import target_contract_entry
    from aichestra.execution.run_contract import save_contract

    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    target = fake_execution_targets("cursor")[0]
    save_contract(
        tmp_path,
        "run-1",
        tmp_path,
        {"bindings": {"tests": target_contract_entry(target)}, "quota": {"mode": "manual"}},
    )
    calls = []
    pending = {
        "dispatchId": "d-pending",
        "state": "release_pending",
        "terminalState": "release_pending",
    }

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "task-list" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"tasks":[{"id":"task-1","run_id":"run-1","role":"tests"}]}',
                stderr="",
            )
        if "worker-start" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"result":{"dispatchId":"d-pending"}}',
                stderr="",
            )
        if "worker-release" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"result": pending}),
                stderr="",
            )
        if "worker-show" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"result": pending}),
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.save_dispatch_role_receipt", boom
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_orca_binary", lambda: "orca-test"
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.trusted_config_root", lambda _p: True
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_aichestra_config_root",
        lambda **_k: tmp_path,
    )
    result = dispatch_role(
        role="tests",
        run_id="run-1",
        task_id="task-1",
        project_root=tmp_path,
        repo_root=tmp_path,
        config={"roles": {"tests": {"runtime": "cursor"}}},
        targets=fake_execution_targets("cursor"),
        binary="orca-test",
        run=fake_run,
    )
    assert result["ok"] is False
    assert result["worker_started"] is True
    assert result["worker_release_attempted"] is True
    assert result["worker_release_ok"] is False
    assert any("worker-release" in c for c in calls)
    assert any("worker-show" in c for c in calls)


def test_dispatch_role_release_pending_recovers_via_worker_show(tmp_path, monkeypatch):
    """Bounded recovery may confirm released via worker-show terminalState."""
    import json
    from types import SimpleNamespace
    from aichestra.execution.dispatch_role import dispatch_role
    from aichestra.execution.roles import target_contract_entry
    from aichestra.execution.run_contract import save_contract

    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path))
    target = fake_execution_targets("cursor")[0]
    save_contract(
        tmp_path,
        "run-1",
        tmp_path,
        {"bindings": {"tests": target_contract_entry(target)}, "quota": {"mode": "manual"}},
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "task-list" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"tasks":[{"id":"task-1","run_id":"run-1","role":"tests"}]}',
                stderr="",
            )
        if "worker-start" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout='{"result":{"dispatchId":"d-recover"}}',
                stderr="",
            )
        if "worker-release" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "result": {
                            "dispatchId": "d-recover",
                            "state": "release_pending",
                            "recovery": "Recovery will retry after reconnect",
                        }
                    }
                ),
                stderr="",
            )
        if "worker-show" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "result": {
                            "dispatchId": "d-recover",
                            "terminalState": "released",
                        }
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.save_dispatch_role_receipt", boom
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_orca_binary", lambda: "orca-test"
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.trusted_config_root", lambda _p: True
    )
    monkeypatch.setattr(
        "aichestra.execution.dispatch_role.resolve_aichestra_config_root",
        lambda **_k: tmp_path,
    )
    result = dispatch_role(
        role="tests",
        run_id="run-1",
        task_id="task-1",
        project_root=tmp_path,
        repo_root=tmp_path,
        config={"roles": {"tests": {"runtime": "cursor"}}},
        targets=fake_execution_targets("cursor"),
        binary="orca-test",
        run=fake_run,
    )
    assert result["worker_release_ok"] is True
    assert any("worker-show" in c for c in calls)


def test_dispatch_role_receipts_are_per_dispatch_files(tmp_path):
    from aichestra.execution.run_contract import (
        _dispatch_role_receipt_path,
        load_dispatch_role_receipts,
        save_dispatch_role_receipt,
    )

    target = fake_execution_targets()[0]
    task, w1, _ = receipt(dispatch="d-a")
    _, w2, _ = receipt(dispatch="d-b")
    a = save_dispatch_role_receipt(tmp_path, adopted(task, w1, target))
    b = save_dispatch_role_receipt(tmp_path, adopted(task, w2, target))
    again = save_dispatch_role_receipt(tmp_path, adopted(task, w1, target))
    assert a["dispatch_id"] == "d-a"
    assert b["dispatch_id"] == "d-b"
    assert again["dispatch_id"] == "d-a"
    path_a = _dispatch_role_receipt_path(tmp_path, "r1", "d-a")
    path_b = _dispatch_role_receipt_path(tmp_path, "r1", "d-b")
    assert path_a.is_file() and path_b.is_file()
    assert path_a != path_b
    assert path_a.parent == path_b.parent
    assert path_a.parent.name.endswith(".dispatch-role")
    loaded = load_dispatch_role_receipts(tmp_path, "r1")
    assert {row["dispatch_id"] for row in loaded} == {"d-a", "d-b"}


def test_authorize_task_reads_orca_state_in_json_mode(tmp_path):
    """authorize_task must append --json; plain text Orca output is not parseable."""
    import json
    from types import SimpleNamespace
    from aichestra.execution.run_contract import authorize_task
    from aichestra.execution.roles import target_contract_entry

    primary = fake_execution_targets("codex")[0]
    fallback = fake_execution_targets("cursor")[0]
    contract = {
        "bindings": {"implement": target_contract_entry(primary)},
        "quota": {
            "mode": "auto",
            "roles": {"implement": target_contract_entry(fallback)},
        },
    }
    task = {"id": "t1", "run_id": "r1", "title": "implement"}
    worker = {
        "dispatchId": "d1",
        "taskId": "t1",
        "runId": "r1",
        "completedAt": "2026-01-01T00:00:00Z",
        "lastFailure": json.dumps({
            "messageId": "msg_1",
            "outcome": "failed",
            "completedAt": "2026-01-01T00:00:00Z",
        }),
    }
    show = {
        "worker": {
            "dispatchId": "d1",
            "taskId": "t1",
            "runId": "r1",
            "state": "failed",
            "startOptions": {"launch": {"effective": {"agent": "codex"}}},
        },
        "dispatch": {**worker, "id": "d1", "status": "failed"},
    }
    mail = {
        "ok": True,
        "result": {
            "messages": [{
                "id": "msg_1",
                "type": "worker_done",
                "payload": json.dumps({
                    "dispatchId": "d1",
                    "outcome": "failed",
                    "failure": "quota",
                }),
            }],
        },
    }
    seen = []

    def run(argv, **kwargs):
        assert argv[0] == "orca-test"
        assert argv[1] == "orchestration"
        assert argv[-1] == "--json", argv
        command = argv[2]
        seen.append(command)
        data = {
            "task-list": {"tasks": [task]},
            "worker-list": {"workers": [worker]},
            "worker-show": show,
            "run-show": {"result": {"run": {"coordinator_handle": "term-1"}}},
            "check": mail,
        }[command]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    authorize_task(
        "orca-test",
        run,
        "r1",
        "t1",
        "implement",
        contract,
        "quota-fallback",
    )
    for command in ("task-list", "worker-list", "worker-show", "run-show", "check"):
        assert command in seen, seen


def test_save_dispatch_role_receipt_cleans_up_partial_write(tmp_path, monkeypatch):
    """A write failure after exclusive create must not leave corrupt receipt files."""
    from pathlib import Path
    from aichestra.execution.run_contract import (
        _dispatch_role_receipt_path,
        _dispatch_role_receipts_dir,
        load_dispatch_role_receipts,
        save_dispatch_role_receipt,
    )

    target = fake_execution_targets()[0]
    task, worker, _ = receipt(dispatch="d-partial")
    row = adopted(task, worker, target)
    path = _dispatch_role_receipt_path(tmp_path, "r1", "d-partial")
    real_open = Path.open

    def flaky_open(self, mode="r", *args, **kwargs):
        if "x" in mode and self == path:
            handle = real_open(self, mode, *args, **kwargs)

            class FailingStream:
                def write(self, _data):
                    raise OSError("disk full during write")

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    handle.close()
                    return False

            return FailingStream()
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)
    with pytest.raises(OSError, match="disk full"):
        save_dispatch_role_receipt(tmp_path, row)
    directory = _dispatch_role_receipts_dir(tmp_path, "r1")
    assert not path.exists()
    leftover = list(directory.glob("*.json")) if directory.is_dir() else []
    assert leftover == []
    assert load_dispatch_role_receipts(tmp_path, "r1") == []


def test_durable_failure_resolver_rejects_conflicting_identifiers():
    """messageId match must not override a contradictory payload.dispatchId."""
    import json
    from aichestra.execution.roles import (
        failure_code_from_durable_messages,
        resolve_worker_failure,
    )

    conflict = [{
        "id": "msg_1",
        "type": "worker_done",
        "payload": json.dumps({
            "dispatchId": "d999",
            "outcome": "failed",
            "failure": "quota",
        }),
    }]
    assert failure_code_from_durable_messages(
        "d1", conflict, message_id="msg_1"
    ) is None

    wrong_type = [{
        "id": "msg_1",
        "type": "ask",
        "payload": json.dumps({
            "dispatchId": "d1",
            "outcome": "failed",
            "failure": "quota",
        }),
    }]
    assert failure_code_from_durable_messages(
        "d1", wrong_type, message_id="msg_1"
    ) is None

    ok = [{
        "id": "msg_1",
        "type": "worker_done",
        "payload": json.dumps({
            "dispatchId": "d1",
            "outcome": "failed",
            "failure": "quota",
        }),
    }]
    assert failure_code_from_durable_messages(
        "d1", ok, message_id="msg_1"
    ) == "quota"

    worker = {
        "dispatchId": "d1",
        "lastFailure": json.dumps({"messageId": "msg_1", "outcome": "failed"}),
    }
    assert resolve_worker_failure(worker, durable_messages=conflict) is None
    assert resolve_worker_failure(worker, durable_messages=ok) == "quota"
