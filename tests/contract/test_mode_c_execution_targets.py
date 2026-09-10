"""T172: ExecutionTargets serialize into Mode C coordinator package (no launch)."""

from __future__ import annotations

import inspect
import json
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
    OWNERSHIP_METADATA,
    resolve_mode_c_execution,
    safe_endpoint_for_context,
    serialize_execution_policy,
    serialize_execution_target,
)
from aichestra.execution.targets import resolve_targets
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    ModeCPolicyPackage,
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
    launch_proven: bool | None = None,
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
    proven = (
        launch_proven
        if launch_proven is not None
        else launch_strategy == LaunchStrategy.ORCA_NATIVE
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
        launch_proven=proven,
        reasons=reasons,
    )


def _bindings(root: Path, *, orca, targets=(), policy=None) -> WorkflowBindings:
    return WorkflowBindings(
        orca=orca,
        providers=[],
        project_root=str(root),
        task_prompt="implement safely",
        maintenance_kwargs={"change_summary": "x", "touches_behavior": False},
        verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        execution_targets=tuple(targets),
        execution_policy=policy or ExecutionPolicy(),
    )


def test_mode_c_package_contains_execution_targets(tmp_path: Path) -> None:
    orca = fake_orca("success")
    target = _target(provider=None, model=None, endpoint=None, launch_strategy=LaunchStrategy.ORCA_NATIVE)
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
    assert row["provider"] is None
    assert row["model"] is None
    assert row["locality"] == "local"
    assert "code_edit" in row["capabilities"]
    assert row["launch_strategy"] == "orca-native"
    assert row["runnable"] is True


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
        "launch_proven": False,
        "runnable": False,
        "provisionable": False,
        "dispatchable": False,
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
    target = _target(launch_strategy=LaunchStrategy.ORCA_NATIVE, preferred=True, caps=frozenset({"shell", "code_edit"}))
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
    assert "Coordinator running under Orca owns the concrete workflow/DAG" in prompt
    assert "Orca owns canonical Run/Task/Dispatch lifecycle" in prompt
    assert "Do not infer workers from raw providers" in prompt
    assert "Do not impose product-name phase routing" in prompt
    assert "enabled, available, capable, allowed, and runnable" in prompt
    assert "execution_target_candidates" in prompt
    assert "aichestra.prove_launch" in prompt
    assert "launch_recipe" not in prompt
    assert "Use only enabled, available cloud providers" not in prompt
    assert "OpenCode launch is unavailable" not in prompt
    assert "Orca owns workflow graph" not in prompt
    assert "Orca owns task ordering" not in prompt
    # Adapter contract: bootstrap legacy seam ≠ inner-worker policy.
    from aichestra.providers import orca as orca_mod

    adapter_src = inspect.getsource(orca_mod.OrcaProvider.send)
    assert "Use only enabled, available cloud providers" not in adapter_src
    assert "OpenCode launch is unavailable" not in adapter_src
    assert "The coordinator owns inner worker selection" in adapter_src
    assert "temporary legacy launch path" not in adapter_src
    assert "Unsupported targets MUST NOT be dispatched" in adapter_src
    assert "Target locality may be local, remote, or cloud" in adapter_src
    assert "execution_target_candidates" in adapter_src
    assert "aichestra.prove_launch" in adapter_src
    assert "launch_recipe" not in adapter_src


def test_product_id_change_does_not_require_workflow_branch(tmp_path: Path) -> None:
    orca = fake_orca("success")
    for runtime, provider in (("alpha-rt", "alpha-p"), ("beta-rt", "beta-p")):
        target = _target(runtime=runtime, provider=provider, model="m", launch_strategy=LaunchStrategy.ORCA_NATIVE)
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
        _target(runtime="rt-a", provider="p-a", model="m1", launch_strategy=LaunchStrategy.ORCA_NATIVE),
        _target(runtime="rt-b", provider="p-b", model="m2", locality=Locality.CLOUD, launch_strategy=LaunchStrategy.ORCA_NATIVE),
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
    assert package["workflow_dag_owner"] == "coordinator_under_orca"
    assert package["canonical_lifecycle_owner"] == "orca"
    assert package["aichestra_role"] == "policy_context_gates"
    assert "orchestration_owner" not in package


def test_legacy_fields_marked_secondary_compatibility(tmp_path: Path) -> None:
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(tmp_path, orca=orca, targets=[_target(launch_strategy=LaunchStrategy.ORCA_NATIVE)]),
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
    assert "execution_target_candidates" in package["canonical_execution_fields"]
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


def test_no_local_forbids_all_local_execution_targets() -> None:
    """CLI --no-local must forbid locality=local via ExecutionPolicy, agnostic of ids."""
    from aichestra.config.layering import apply_provider_enable_overrides

    config = {
        "local": {"enabled": True},
        "execution": {
            "runtimes": {
                "acme-agent": {
                    "enabled": True,
                    "binaries": (),
                    "locality": "local",
                    "capabilities": ["code_edit"],
                },
                "sky-agent": {
                    "enabled": True,
                    "binaries": (),
                    "locality": "cloud",
                    "capabilities": ["code_edit"],
                },
            },
            "model_providers": {
                "home-vllm": {
                    "enabled": True,
                    "endpoint": "http://127.0.0.1:8000",
                    "locality": "local",
                    "models": {
                        "lab": {"available": True, "capabilities": ["code"]},
                    },
                },
                "cloud-llm": {
                    "enabled": True,
                    "endpoint": "https://api.example.com",
                    "locality": "cloud",
                    "models": {
                        "pro": {"available": True, "capabilities": ["code"]},
                    },
                },
            },
            "bindings": [
                {
                    "runtime": "acme-agent",
                    "provider": "home-vllm",
                    "model": "lab",
                },
                {
                    "runtime": "sky-agent",
                    "provider": "cloud-llm",
                    "model": "pro",
                },
            ],
            # Explicit localities including local — CLI override still wins.
            "policy": {
                "allowed_localities": ["local", "remote", "cloud"],
            },
        },
    }

    normal_targets, normal_policy, _ = resolve_mode_c_execution(config)
    by_runtime = {t.runtime.id: t for t in normal_targets}
    assert "acme-agent" in by_runtime
    assert by_runtime["acme-agent"].locality is Locality.LOCAL
    assert by_runtime["acme-agent"].allowed is True
    assert Locality.LOCAL in normal_policy.allowed_localities

    no_local_cfg = apply_provider_enable_overrides(config, no_local=True)
    assert no_local_cfg["local"]["enabled"] is False  # legacy seam preserved
    targets, policy, _ = resolve_mode_c_execution(no_local_cfg)
    assert Locality.LOCAL not in policy.allowed_localities
    assert Locality.CLOUD in policy.allowed_localities
    by_runtime = {t.runtime.id: t for t in targets}
    assert by_runtime["acme-agent"].allowed is False
    assert by_runtime["sky-agent"].allowed is True
    # Source must not branch on product/provider names.
    from aichestra.config import layering as layering_mod

    src = inspect.getsource(layering_mod.apply_provider_enable_overrides)
    assert "ollama" not in src.lower()
    assert "opencode" not in src.lower()


def test_no_codex_and_no_cursor_disable_canonical_runtimes_via_policy() -> None:
    """CLI --no-codex/--no-cursor must beat explicit execution.runtimes.*.enabled."""
    from aichestra.config.layering import apply_provider_enable_overrides

    config = {
        "providers": {
            "codex": {"enabled": True},
            "cursor": {"enabled": True},
        },
        "execution": {
            "runtimes": {
                "codex": {
                    "enabled": True,
                    "binaries": (),
                    "locality": "cloud",
                    "capabilities": ["code_edit"],
                },
                "cursor": {
                    "enabled": True,
                    "binaries": (),
                    "locality": "cloud",
                    "capabilities": ["code_edit"],
                },
                "custom-agent": {
                    "enabled": True,
                    "binaries": (),
                    "locality": "cloud",
                    "capabilities": ["code_edit"],
                },
            },
            "model_providers": {
                "cloud-llm": {
                    "enabled": True,
                    "endpoint": "https://api.example.com",
                    "locality": "cloud",
                    "models": {
                        "pro": {"available": True, "capabilities": ["code"]},
                    },
                },
            },
            "bindings": [
                {"runtime": "codex", "provider": "cloud-llm", "model": "pro"},
                {"runtime": "cursor", "provider": "cloud-llm", "model": "pro"},
                {
                    "runtime": "custom-agent",
                    "provider": "cloud-llm",
                    "model": "pro",
                },
            ],
            "policy": {
                "disabled_runtimes": ["legacy-rt"],
                "allowed_localities": ["cloud", "remote", "local"],
            },
        },
    }

    baseline, baseline_policy, _ = resolve_mode_c_execution(config)
    by_rt = {t.runtime.id: t for t in baseline}
    assert by_rt["codex"].enabled is True
    assert by_rt["cursor"].enabled is True
    assert by_rt["custom-agent"].enabled is True
    assert "legacy-rt" in baseline_policy.disabled_runtimes

    no_codex_cfg = apply_provider_enable_overrides(config, no_codex=True)
    assert no_codex_cfg["providers"]["codex"]["enabled"] is False  # legacy seam
    assert "codex" in no_codex_cfg["execution"]["policy"]["disabled_runtimes"]
    assert "legacy-rt" in no_codex_cfg["execution"]["policy"]["disabled_runtimes"]
    targets, policy, _ = resolve_mode_c_execution(no_codex_cfg)
    by_rt = {t.runtime.id: t for t in targets}
    assert by_rt["codex"].enabled is False
    assert by_rt["codex"].runnable is False
    assert by_rt["cursor"].enabled is True
    assert by_rt["custom-agent"].enabled is True
    assert "codex" in policy.disabled_runtimes
    assert "legacy-rt" in policy.disabled_runtimes

    no_cursor_cfg = apply_provider_enable_overrides(config, no_cursor=True)
    assert no_cursor_cfg["providers"]["cursor"]["enabled"] is False
    assert "cursor" in no_cursor_cfg["execution"]["policy"]["disabled_runtimes"]
    assert "legacy-rt" in no_cursor_cfg["execution"]["policy"]["disabled_runtimes"]
    targets, policy, _ = resolve_mode_c_execution(no_cursor_cfg)
    by_rt = {t.runtime.id: t for t in targets}
    assert by_rt["cursor"].enabled is False
    assert by_rt["cursor"].runnable is False
    assert by_rt["codex"].enabled is True
    assert by_rt["custom-agent"].enabled is True

    both = apply_provider_enable_overrides(config, no_codex=True, no_cursor=True)
    assert set(both["execution"]["policy"]["disabled_runtimes"]) >= {
        "codex",
        "cursor",
        "legacy-rt",
    }
    targets, _, _ = resolve_mode_c_execution(both)
    by_rt = {t.runtime.id: t for t in targets}
    assert by_rt["codex"].enabled is False
    assert by_rt["cursor"].enabled is False
    assert by_rt["custom-agent"].enabled is True


def test_safe_endpoint_drops_userinfo_query_and_fragment() -> None:
    secret = "SUPER_SECRET"
    assert (
        safe_endpoint_for_context("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    )
    assert (
        safe_endpoint_for_context(
            f"https://alice:{secret}@example.com/v1?x-api-key={secret}#token={secret}"
        )
        == "https://example.com/v1"
    )
    # Known credential query keys and arbitrary query names — all dropped.
    for dirty in (
        f"https://example.com/v1?api_key={secret}",
        f"https://example.com/v1?x-api-key={secret}",
        f"https://example.com/v1?token={secret}",
        f"https://example.com/v1?arbitrary={secret}",
        f"https://example.com/v1#token={secret}",
        f"https://example.com/v1#access_token={secret}",
        f"https://user:{secret}@example.com/v1",
    ):
        safe = safe_endpoint_for_context(dirty)
        assert safe == "https://example.com/v1"
        assert secret not in (safe or "")
        assert "?" not in (safe or "")
        assert "#" not in (safe or "")
        assert "@" not in (safe or "")


def test_serialized_surfaces_never_leak_endpoint_secrets(tmp_path: Path) -> None:
    secret = "SUPER_SECRET"
    dirty = (
        f"https://alice:{secret}@example.com/v1"
        f"?x-api-key={secret}&arbitrary={secret}&api_key={secret}&token={secret}"
        f"#token={secret}"
    )
    target = _target(
        runtime="acme-agent",
        provider="home-vllm",
        model="lab",
        endpoint=dirty,
        launch_strategy=LaunchStrategy.ORCA_NATIVE,
        locality=Locality.LOCAL,
    )
    # Internal exact endpoint must remain intact for future launch proof.
    assert secret in (target.endpoint or "")

    row = serialize_execution_target(target)
    blob_row = json.dumps(row)
    assert secret not in blob_row
    assert "alice" not in blob_row
    assert row["endpoint"] == "https://example.com/v1"
    assert "?" not in row["endpoint"]
    assert "#" not in row["endpoint"]

    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(tmp_path, orca=orca, targets=[target]),
    )
    wf.bindings.local_endpoint = dirty
    state = wf.run_all()
    assert not state.failed, state.failed

    package = state.metadata["mode_c_policy_package"]
    package_blob = json.dumps(package)
    assert secret not in package_blob
    assert "alice" not in package_blob
    assert package["local_endpoint"] == "https://example.com/v1"
    assert package["execution_targets"][0]["endpoint"] == "https://example.com/v1"

    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    ctx_blob = json.dumps(handoff.context or {})
    assert secret not in ctx_blob

    state_blob = json.dumps(state.to_dict())
    assert secret not in state_blob

    direct = ModeCPolicyPackage(
        run_id="r1",
        task_prompt="t",
        research_query="",
        project_root=str(tmp_path),
        project_context={},
        local_endpoint=safe_endpoint_for_context(dirty),
        execution_targets=(serialize_execution_target(target),),
    ).to_dict()
    direct_blob = json.dumps(direct)
    assert secret not in direct_blob
    for key, value in OWNERSHIP_METADATA.items():
        assert direct[key] == value
    assert "orchestration_owner" not in direct


def test_no_runnable_target_fails_before_run_creation(tmp_path):
    orca = fake_orca("success")
    controller = ModeCRunController(bindings=_bindings(tmp_path, orca=orca, targets=[_target()]))
    state = controller.run_all()
    assert state.failed
    assert not orca.sent


def test_production_resolver_discovers_native_launch_contract(monkeypatch):
    from types import SimpleNamespace
    from aichestra.execution import serialize as module
    from aichestra.execution.domain import Compatibility
    from aichestra.execution.launch_strategies import NativeLaunch
    facts = DiscoveryFacts(runtimes=(AgentRuntime("acme-agent", available=True),))
    monkeypatch.setattr(module, "discover_execution_facts", lambda *a, **k: facts)
    monkeypatch.setattr(module, "load_compatibility_bindings", lambda c: (Compatibility("acme-agent"),))
    monkeypatch.setattr("aichestra.providers.orca.resolve_orca_binary", lambda: "orca-test")
    monkeypatch.setattr("aichestra.execution.launch_strategies.LAUNCH_ADAPTERS", [NativeLaunch("acme-agent")])
    calls = []
    def probe(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"schemaVersion": 1, "commands": [
            {"path": ["orchestration", "worker-start"], "flags": ["agent", "task", "worktree"]}]}))
    monkeypatch.setattr("aichestra.execution.launch_strategies.subprocess.run", probe)
    targets, _, _ = module.resolve_mode_c_execution({})
    assert targets[0].runnable
    assert calls == [["orca-test", "agent-context", "--json"]]
    # Native launch cannot pretend that --agent also selects an endpoint/model.
    assert not NativeLaunch("acme-agent").accepts(_target(runtime="acme-agent"))


def test_unproven_candidates_not_exposed_to_coordinator(tmp_path):
    from tests.fakes.providers import fake_execution_targets

    orca = fake_orca("success")
    # Unsupported + runnable native: only runnable enters execution_targets.
    targets = [*fake_execution_targets(), _target()]
    state = ModeCRunController(bindings=_bindings(tmp_path, orca=orca, targets=targets)).run_all()
    assert not state.failed
    package = state.metadata["mode_c_policy_package"]
    assert len(package["execution_targets"]) == 1
    assert package["execution_targets"][0]["runnable"] is True
    assert package["execution_target_candidates"] == []
    assert "launch_recipe" not in package["execution_targets"][0]


def test_provisionable_targets_are_candidates_not_dispatchable(tmp_path, monkeypatch):
    """T163: provisionable bridges must not enter canonical execution_targets."""
    from aichestra.execution.domain import Compatibility, LaunchCapability
    from aichestra.execution.launch_strategies import PreparedLaunch, mark_launch_proven
    from aichestra.execution.serialize import LAUNCH_PROOF_OPERATION
    from tests.fakes.providers import fake_execution_targets

    runtime = AgentRuntime("opencode", True, binary_path="/usr/bin/opencode")
    provider = ModelProvider(
        "ollama", True, endpoint="http://127.0.0.1:11434", locality=Locality.LOCAL
    )
    model = Model("qwen", "ollama", True, capabilities=Caps(frozenset({"code"})))
    facts = DiscoveryFacts(runtimes=(runtime,), providers=(provider,), models=(model,))
    bridge = resolve_targets(
        facts,
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
    assert bridge.provisionable
    assert not bridge.runnable

    native = fake_execution_targets()[0]
    assert native.runnable

    def prove_ok(target, ctx):
        proven = mark_launch_proven(target) if target.provisionable else target
        handle = "term_boot" if target.provisionable else None
        args = ["--terminal", handle] if handle else ["--agent", target.runtime.id]
        return proven, PreparedLaunch(
            arguments=args, terminal_handle=handle, owns_terminal=bool(handle)
        )

    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.prove_bootstrap_launch",
        prove_ok,
    )
    monkeypatch.setattr(
        "aichestra.providers.orca.resolve_orca_binary",
        lambda: "orca-test",
    )

    orca = fake_orca("success")
    state = ModeCRunController(
        bindings=_bindings(tmp_path, orca=orca, targets=[native, bridge])
    ).run_all()
    assert not state.failed, state.failed
    package = state.metadata["mode_c_policy_package"]
    assert all(row["runnable"] for row in package["execution_targets"])
    assert all(not row.get("provisionable") for row in package["execution_targets"])
    assert "launch_recipe" not in str(package["execution_targets"])
    candidates = package["execution_target_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["id"] == bridge.id
    assert candidates[0]["dispatchable"] is False
    assert candidates[0]["proof_operation"] == LAUNCH_PROOF_OPERATION
    assert "steps" not in candidates[0]
    assert "launch_recipe" not in candidates[0]
    assert candidates[0]["expected_binding"]["model"] == "qwen"
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    assert (handoff.context or {}).get("execution_target_candidates")
    assert LAUNCH_PROOF_OPERATION in (handoff.prompt or "")
