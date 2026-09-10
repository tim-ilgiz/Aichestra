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


def test_installed_orca_worker_shape_fails_without_effective_launch_evidence():
    target = fake_execution_targets()[0]
    task = {"id": "t", "run_id": "r", "task_title": "tests"}
    worker = {"dispatchId": "d", "taskId": "t", "runId": "r"}
    # Shape from installed Orca's workerShow handler. startOptions describes
    # requested options, which cannot substitute for an effective receipt.
    payload = {"dispatch": {"id": "d", "task_id": "t", "run_id": "r"},
               "worker": {"dispatch_id": "d", "state": "succeeded",
                          "startOptions": {"agent": "codex"}}}
    with pytest.raises(ValueError, match="no effective launch binding"):
        validate_role_receipts("r", [task], [worker], {"d": payload}, {"tests": target})
    payload["launch"] = {"effective": {"agent": "codex"}}
    assert validate_role_receipts("r", [task], [worker], {"d": payload}, {"tests": target})["ok"]
