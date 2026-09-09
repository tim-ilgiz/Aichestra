"""T172: ExecutionTargets serialize into Mode C coordinator package (no launch)."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from aichestra.execution.domain import (
    AgentRuntime,
    DiscoveryFacts,
    ExecutionCapabilities as Caps,
    ExecutionPolicy,
    ExecutionTarget,
    ExecutionTargetKey,
    LaunchCapability,
    LaunchStrategy,
    Locality,
    MachineCapabilities,
    Model,
    ModelProvider,
)
from aichestra.execution.serialize import (
    CANONICAL_EXECUTION_FIELDS,
    EXECUTION_TARGET_CONTRACT_VERSION,
    LEGACY_COMPATIBILITY_FIELDS,
    resolve_mode_c_execution,
    serialize_execution_policy,
    serialize_execution_target,
)
from aichestra.execution.targets import resolve_targets
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    ModeCRunController,
    WorkflowBindings,
)
from tests.fakes.providers import fake_codex, fake_orca


def _target(
    *,
    runtime: str = "weird-agent",
    provider: str | None = "weird-backend",
    model: str | None = "coder",
    endpoint: str | None = "http://127.0.0.1:9",
    locality: Locality = Locality.LOCAL,
    enabled: bool = True,
    available: bool = True,
    capable: bool = True,
    allowed: bool = True,
    preferred: bool = False,
    launch_strategy: LaunchStrategy = LaunchStrategy.UNSUPPORTED,
    caps: frozenset[str] = frozenset({"code_edit", "shell"}),
    reasons: tuple[str, ...] = (),
) -> ExecutionTarget:
    rt = AgentRuntime(
        runtime,
        available=available,
        enabled=enabled,
        capabilities=Caps(caps),
        locality=locality,
    )
    prov = (
        ModelProvider(
            provider,
            available=available,
            enabled=enabled,
            endpoint=endpoint,
            locality=locality,
        )
        if provider
        else None
    )
    mdl = (
        Model(model, provider, available=available, enabled=enabled)
        if model and provider
        else None
    )
    return ExecutionTarget(
        id=ExecutionTargetKey(runtime, provider, model).target_id(),
        runtime=rt,
        provider=prov,
        model=mdl,
        endpoint=endpoint,
        locality=locality,
        capabilities=Caps(caps),
        enabled=enabled,
        available=available,
        capable=capable,
        allowed=allowed,
        preferred=preferred,
        launch_strategy=launch_strategy,
        reasons=reasons,
    )


def _bindings(root: Path, *, orca, targets=(), policy=None) -> WorkflowBindings:
    return WorkflowBindings(
        orca=orca,
        providers=[fake_codex("success").probe()],
        project_root=str(root),
        task_prompt="implement safely",
        maintenance_kwargs={"change_summary": "x", "touches_behavior": False},
        verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        execution_targets=tuple(targets),
        execution_policy=policy or ExecutionPolicy(),
    )


def test_mode_c_package_contains_execution_targets(tmp_path: Path) -> None:
    orca = fake_orca("success")
    target = _target()
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(tmp_path, orca=orca, targets=[target]),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    package = state.metadata["mode_c_policy_package"]
    assert package["execution_target_contract_version"] == EXECUTION_TARGET_CONTRACT_VERSION
    assert package["canonical_execution_fields"] == list(CANONICAL_EXECUTION_FIELDS)
    assert "execution_targets" in package
    assert "execution_policy" in package
    assert len(package["execution_targets"]) == 1
    row = package["execution_targets"][0]
    assert row["runtime"] == "weird-agent"
    assert row["provider"] == "weird-backend"
    assert row["model"] == "coder"
    assert row["locality"] == "local"
    assert "code_edit" in row["capabilities"]
    assert row["launch_strategy"] == "unsupported"
    assert row["runnable"] is False


def test_arbitrary_fake_runtime_serializes_without_product_branches() -> None:
    target = _target(
        runtime="acme-coder",
        provider="acme-llm",
        model="turbo",
        endpoint="http://localhost:8080",
        caps=frozenset({"repository_read", "vision"}),
    )
    # Serialization must be product-id agnostic (no OpenCode/Codex branches).
    src = inspect.getsource(serialize_execution_target)
    assert "opencode" not in src.lower()
    assert "codex" not in src.lower()
    assert "ollama" not in src.lower()
    row = serialize_execution_target(target)
    assert row == {
        "id": target.id,
        "runtime": "acme-coder",
        "provider": "acme-llm",
        "model": "turbo",
        "endpoint": "http://localhost:8080",
        "locality": "local",
        "capabilities": ["repository_read", "vision"],
        "enabled": True,
        "available": True,
        "capable": True,
        "allowed": True,
        "preferred": False,
        "launch_strategy": "unsupported",
        "runnable": False,
        "reasons": [],
    }


def test_disabled_and_unavailable_states_preserved() -> None:
    disabled = serialize_execution_target(_target(enabled=False, available=True))
    unavailable = serialize_execution_target(_target(enabled=True, available=False))
    assert disabled["enabled"] is False
    assert disabled["available"] is True
    assert unavailable["enabled"] is True
    assert unavailable["available"] is False
    assert disabled["runnable"] is False
    assert unavailable["runnable"] is False


def test_unsupported_launch_remains_not_runnable() -> None:
    row = serialize_execution_target(_target(launch_strategy=LaunchStrategy.UNSUPPORTED))
    assert row["launch_strategy"] == "unsupported"
    assert row["runnable"] is False


def test_raw_provider_without_runtime_is_not_a_worker_target() -> None:
    facts = DiscoveryFacts(
        runtimes=(),
        providers=(
            ModelProvider(
                "orphan-backend",
                True,
                endpoint="http://127.0.0.1:1",
                locality=Locality.LOCAL,
            ),
        ),
        models=(Model("m1", "orphan-backend", True),),
        machine=MachineCapabilities("linux", 16),
    )
    # No Compatibility binding → provider alone cannot become a worker target.
    targets = resolve_targets(facts, ())
    assert targets == []


def test_local_target_need_not_be_opencode_ollama() -> None:
    target = _target(
        runtime="home-lab-agent",
        provider="vllm",
        model="llama",
        locality=Locality.LOCAL,
    )
    row = serialize_execution_target(target)
    assert row["locality"] == "local"
    assert row["runtime"] == "home-lab-agent"
    assert row["provider"] == "vllm"
    assert row["runtime"] != "opencode"
    assert row["provider"] != "ollama"


def test_coordinator_context_receives_capabilities_locality_policy(
    tmp_path: Path,
) -> None:
    orca = fake_orca("success")
    policy = ExecutionPolicy(
        required=Caps(frozenset({"shell"})),
        preferred_targets=frozenset(),
        allowed_localities=frozenset({Locality.LOCAL, Locality.CLOUD}),
    )
    target = _target(preferred=True, caps=frozenset({"shell", "code_edit"}))
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(tmp_path, orca=orca, targets=[target], policy=policy),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    ctx = handoff.context or {}
    assert ctx["execution_target_contract_version"] == EXECUTION_TARGET_CONTRACT_VERSION
    caps = ctx.get("capabilities") or {}
    assert caps.get("execution_targets")
    assert caps["execution_targets"][0]["locality"] == "local"
    assert "shell" in caps["execution_targets"][0]["capabilities"]
    assert "shell" in (caps.get("execution_policy") or {}).get(
        "required_capabilities", []
    )
    prompt = handoff.prompt or ""
    assert "ExecutionTargets" in prompt
    assert "Do not infer a worker from a raw ModelProvider" in prompt
    assert "Do not treat local as OpenCode/Ollama" in prompt
    assert "Do not use product-name phase routing" in prompt


def test_product_id_change_does_not_require_workflow_branch(tmp_path: Path) -> None:
    orca = fake_orca("success")
    for runtime, provider in (("alpha-rt", "alpha-p"), ("beta-rt", "beta-p")):
        target = _target(runtime=runtime, provider=provider, model="m")
        wf = ModeCRunController(
            mode=Mode.ORCHESTRATED,
            bindings=_bindings(tmp_path, orca=orca, targets=[target]),
        )
        state = wf.run_all()
        assert not state.failed, state.failed
        package = state.metadata["mode_c_policy_package"]
        assert package["execution_targets"][0]["runtime"] == runtime
        assert package["execution_targets"][0]["provider"] == provider
    # Controller path has no product-name branch on execution target ids.
    from aichestra.orchestration import workflow as wf_mod

    build_src = inspect.getsource(wf_mod.ModeCRunController._build_policy_package)
    assert "if runtime" not in build_src
    assert "opencode" not in build_src


def test_execution_targets_do_not_schedule_aichestra_inner_workers(
    tmp_path: Path,
) -> None:
    orca = fake_orca("success")
    # Many targets still must not create research/implement/writer roles.
    targets = [
        _target(runtime="rt-a", provider="p-a", model="m1"),
        _target(runtime="rt-b", provider="p-b", model="m2", locality=Locality.CLOUD),
    ]
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(tmp_path, orca=orca, targets=targets),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    roles = {(r.role or "") for r in orca.sent}
    assert MODE_C_HANDOFF_ROLE in roles
    for forbidden in (
        "research",
        "lead_implement",
        "mode_c_agents",
        "mode_c_writers",
        "lead_review",
        "test_writer",
        "doc_writer",
    ):
        assert forbidden not in roles
    package = state.metadata["mode_c_policy_package"]
    assert package["inner_worker_selection_owner"] == "coordinator_under_orca"
    assert package["orchestration_owner"] == "orca"


def test_legacy_fields_marked_secondary_compatibility(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(tmp_path, orca=orca, targets=[_target()]),
    )
    wf.bindings.preferred_lead = "codex"
    wf.bindings.fallback_lead = "cursor"
    wf.bindings.local_enabled = True
    wf.bindings.local_model_ref = "legacy-model"
    state = wf.run_all()
    assert not state.failed, state.failed
    package = state.metadata["mode_c_policy_package"]
    assert package["legacy_compatibility_fields"] == list(LEGACY_COMPATIBILITY_FIELDS)
    assert "preferred_lead" in package["legacy_compatibility_fields"]
    assert "execution_targets" in package["canonical_execution_fields"]
    assert package["preferred_lead"] == "codex"  # still present for back-compat
    assert package["local_model_ref"] == "legacy-model"
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    caps = (handoff.context or {}).get("capabilities") or {}
    assert "legacy_compatibility" in caps
    assert caps["legacy_compatibility"]["preferred_lead"] == "codex"
    assert caps["legacy_compatibility"]["local_model_ref"] == "legacy-model"


def test_t172_does_not_synthesize_launch_capability() -> None:
    """Regression: Mode C resolve path must not invent LaunchCapability proofs."""
    src = inspect.getsource(resolve_mode_c_execution)
    assert "LaunchCapability(" not in src
    assert "known_launches" in src
    # Default call with empty launches → unsupported / not runnable.
    config = {
        "execution": {
            "runtimes": {
                "ghost-agent": {
                    "enabled": True,
                    "binaries": (),
                    "locality": "local",
                    "capabilities": ["code_edit"],
                }
            },
            "model_providers": {
                "ghost-llm": {
                    "enabled": True,
                    "endpoint": "http://127.0.0.1:9999",
                    "locality": "local",
                    "models": {"g1": {"available": True, "capabilities": ["code"]}},
                }
            },
            "bindings": [
                {
                    "runtime": "ghost-agent",
                    "provider": "ghost-llm",
                    "model": "g1",
                }
            ],
        },
        "local": {"enabled": False},
    }
    targets, policy, _facts = resolve_mode_c_execution(config)
    assert isinstance(policy, ExecutionPolicy)
    assert targets
    for target in targets:
        assert target.launch_strategy is LaunchStrategy.UNSUPPORTED
        assert target.runnable is False
    # Explicit: callers did not pass synthetic proofs.
    assert LaunchCapability.__dataclass_fields__  # type exists for T174+


def test_serialize_execution_policy_round_trip_shape() -> None:
    policy = ExecutionPolicy(
        required=Caps(frozenset({"shell"})),
        disabled_runtimes=frozenset({"x"}),
        disabled_providers=frozenset({"y"}),
        disabled_models=frozenset({("y", "m")}),
        preferred_targets=frozenset({'["a",null,null]'}),
        allowed_localities=frozenset({Locality.CLOUD}),
    )
    data = serialize_execution_policy(policy)
    assert data["required_capabilities"] == ["shell"]
    assert data["disabled_runtimes"] == ["x"]
    assert data["disabled_providers"] == ["y"]
    assert data["disabled_models"] == [["y", "m"]]
    assert data["allowed_localities"] == ["cloud"]
