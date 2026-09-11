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


def test_canonical_receipts_enforce_role_model_and_run():
    target = replace(fake_execution_targets()[0], model=Model("pinned", "codex", available=True))
    task, worker, payload = receipt(model="pinned")
    def audit():
        return validate_role_receipts("r1", [task], [worker], {"d1": payload}, {"tests": target})
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


def test_fallback_receipt_requires_auto_exact_target_and_prior_quota():
    primary = fake_execution_targets()[0]
    fallback = fake_execution_targets("cursor")[0]
    task, first, p1 = receipt(role="implement")
    _, second, p2 = receipt(dispatch="d2", role="implement", runtime="cursor")
    first.update(failure="quota", completed_at="2026-01-01T00:00:00Z")
    second["created_at"] = "2026-01-01T00:00:01Z"
    def audit(mode="auto"):
        return validate_role_receipts("r1", [task], [first, second], {"d1": p1, "d2": p2},
                                      {"implement": primary}, quota_target=fallback, quota_mode=mode)
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
    with pytest.raises(ValueError, match="no effective launch binding"):
        validate_role_receipts("r", [task], [worker], {"d": payload}, {"tests": target})
    # Live Orca 1.4+ nests durable launch.effective under startOptions.launch.
    payload["worker"]["startOptions"] = {
        "agent": "codex",
        "launch": {
            "requested": {"agent": "codex", "model": None},
            "effective": {"agent": "codex", "model": None},
        },
    }
    assert validate_role_receipts("r", [task], [worker], {"d": payload}, {"tests": target})["ok"]
    # Top-level launch.effective remains accepted.
    payload = {"dispatch": {"id": "d", "task_id": "t", "run_id": "r"},
               "worker": {"dispatch_id": "d", "state": "succeeded"},
               "launch": {"effective": {"agent": "codex"}}}
    assert validate_role_receipts("r", [task], [worker], {"d": payload}, {"tests": target})["ok"]
    # JSON twin start_options string (Orca worker-show) also counts.
    import json
    nested = {
        "agent": "codex",
        "launch": {"requested": {"agent": "codex"}, "effective": {"agent": "codex"}},
    }
    payload = {"dispatch": {"id": "d", "task_id": "t", "run_id": "r"},
               "worker": {"dispatch_id": "d", "state": "succeeded",
                          "start_options": json.dumps(nested)}}
    assert validate_role_receipts("r", [task], [worker], {"d": payload}, {"tests": target})["ok"]


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
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
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
        run=fake_run,
    )
    assert payload["ok"] is True
    assert payload["operation"] == ROLE_DISPATCH_OPERATION
    assert payload["execution_target_id"] == target.id
    assert payload["dispatch_id"] == "d-pinned"
    start = next(c for c in calls if "worker-start" in c)
    assert "--agent" in start
    assert start[start.index("--agent") + 1] == "cursor"
    assert start[start.index("--task") + 1] == "task-1"

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


@pytest.mark.parametrize("fault", [None, "manual", "missing", "cross-run", "wrong-primary", "future", "wrong-role", "disabled", "run-use"])
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
        data = {"task-list": {"tasks": [task]},
                "worker-list": {"workers": [] if fault == "missing" else [worker]},
                "worker-show": payload, "run-use": {},
                "worker-start": {"dispatchId": "fallback"}}[command]
        return SimpleNamespace(returncode=1 if fault == "run-use" and command == "run-use" else 0,
                               stdout=json.dumps(data), stderr="")
    result = dispatch_role(role="implement", reason="quota-fallback", run_id="r1", task_id="t1",
                           project_root=tmp_path, repo_root=tmp_path, config={},
                           targets=(primary, replace(fallback, enabled=fault != "disabled")),
                           binary="orca-test", run=run)
    assert result["ok"] is (fault is None), result
    assert ("worker-start" in calls) is (fault is None)
    if fault is None:
        assert result["execution_target_id"] == fallback.id


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
                "run-use": {}, "worker-start": {"dispatchId": "d1"}}[argv[2]]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
    result = dispatch_role(role="tests", run_id="r1", task_id="t1", project_root=tmp_path,
                           repo_root=tmp_path, config=cfg, binary="orca-test", run=run)
    assert result["ok"] is (fault is None), result
    assert any("worker-start" in c for c in calls) is (fault is None)
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
