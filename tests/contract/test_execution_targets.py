"""T169–T171: facts are not workers or proof of an Orca launch."""

from dataclasses import replace

import pytest

from aichestra.execution.discovery import discover_execution_facts, legacy_local_compatibility
from aichestra.execution.domain import (
    AgentRuntime, Compatibility, DiscoveryFacts, ExecutionCapabilities as Caps,
    ExecutionPolicy, LaunchCapability, LaunchStrategy, Locality,
    MachineCapabilities, Model, ModelProvider,
)
from aichestra.execution.targets import resolve_targets
from aichestra.local_runtime.base import LocalModel, ModelCapability
from aichestra.local_runtime.ollama import OllamaRuntime
from aichestra.machine_profiler import (
    CpuInfo, LocalAiStub, MachineProfile, MemoryInfo,
)


def fixture_facts():
    return DiscoveryFacts(
        runtimes=(AgentRuntime("fake-agent", True, capabilities=Caps(frozenset({"code_edit", "code", "vision"}))),
                  AgentRuntime("other-agent", True)),
        providers=(ModelProvider("fake-backend", True, endpoint="http://localhost:1234", locality=Locality.LOCAL),),
        models=(Model("coder", "fake-backend", True, capabilities=Caps(frozenset({"code"})), min_memory_bytes=8),),
        machine=MachineCapabilities("linux", 16),
    )


BINDINGS = (Compatibility("fake-agent", "fake-backend", "coder", required_model=Caps(frozenset({"code"}))),
            Compatibility("other-agent"))


def test_arbitrary_runtime_provider_and_exact_launch_binding():
    facts = fixture_facts()
    targets = resolve_targets(facts, BINDINGS)
    assert len(targets) == 2
    assert targets[0].locality == Locality.LOCAL
    assert targets[1].locality == Locality.CLOUD
    assert targets[0].capable and targets[0].available
    assert all(t.launch_strategy == LaunchStrategy.UNSUPPORTED and not t.runnable for t in targets)
    # Synthetic adapter input exercises the contract, not live launch proof.
    launch = LaunchCapability("fake-agent", "fake-backend", "coder", "http://localhost:1234", LaunchStrategy.ORCA_NATIVE)
    assert resolve_targets(facts, BINDINGS, known_launches=(launch,))[0].runnable
    wrong_endpoint = replace(launch, endpoint="http://localhost:9999")
    assert not resolve_targets(facts, BINDINGS, known_launches=(wrong_endpoint,))[0].runnable


@pytest.mark.parametrize("policy", [
    ExecutionPolicy(disabled_runtimes=frozenset({"fake-agent"})),
    ExecutionPolicy(disabled_providers=frozenset({"fake-backend"})),
    ExecutionPolicy(disabled_models=frozenset({("fake-backend", "coder")})),
])
def test_disable_only_dependent_targets(policy):
    a, b = resolve_targets(fixture_facts(), BINDINGS, policy)
    assert not a.enabled and a.available
    assert b.enabled and b.available


def test_model_and_vision_capability_mismatch():
    facts = fixture_facts()
    a = resolve_targets(facts, BINDINGS, ExecutionPolicy(required=Caps(frozenset({"vision"}))))[0]
    assert not a.capable and "vision" not in a.capabilities.names
    embedding = replace(facts.models[0], capabilities=Caps(frozenset({"embedding"})))
    a = resolve_targets(replace(facts, models=(embedding,)), BINDINGS)[0]
    assert not a.capable and a.available


def test_machine_project_policy_and_preference_are_separate():
    facts = fixture_facts()
    a = resolve_targets(facts, BINDINGS)[0]
    policy = ExecutionPolicy(allowed_localities=frozenset({Locality.CLOUD}), preferred_targets=frozenset({a.id}))
    a, b = resolve_targets(facts, BINDINGS, policy)
    assert a.preferred and not a.allowed and a.capable
    assert b.allowed and not b.preferred
    a = resolve_targets(replace(facts, machine=MachineCapabilities("linux", 1)), BINDINGS)[0]
    assert not a.capable and a.available
    restricted = replace(BINDINGS[0], supported_os=frozenset({"windows"}), required_machine=Caps(frozenset({"gpu"})))
    assert not resolve_targets(facts, (restricted,))[0].capable
    assert not any(t.allowed for t in resolve_targets(facts, BINDINGS, ExecutionPolicy(allowed_targets=frozenset())))


def test_provider_without_compatible_runtime_never_becomes_worker():
    facts = fixture_facts()
    assert resolve_targets(replace(facts, runtimes=()), BINDINGS) == []
    assert resolve_targets(facts, (Compatibility("unknown", "fake-backend"),)) == []
    assert resolve_targets(facts, ()) == []
    assert resolve_targets(facts, (Compatibility("fake-agent", "missing"),)) == []
    assert resolve_targets(facts, (Compatibility("fake-agent", "fake-backend", "missing"),)) == []


def test_unavailable_codex_and_ollama_preserve_cursor_and_remote_opencode():
    facts = DiscoveryFacts(
        runtimes=(AgentRuntime("codex"), AgentRuntime("cursor", True), AgentRuntime("opencode", True)),
        providers=(ModelProvider("ollama"), ModelProvider("openrouter", configured=True)),
    )
    bindings = (Compatibility("codex"), Compatibility("cursor"), Compatibility("opencode", "ollama"), Compatibility("opencode", "openrouter"))
    targets = resolve_targets(facts, bindings)
    assert not targets[0].available and targets[1].available
    assert targets[3].provider.configured and not targets[3].runnable
    # Neither named cloud agent is required for a local candidate.
    assert resolve_targets(fixture_facts(), BINDINGS)[0].locality == Locality.LOCAL


def profile():
    return MachineProfile("linux", "Linux", "x86_64", "3.11", False,
                          CpuInfo("fixture", 4, 4), MemoryInfo(16, 8), (), (),
                          LocalAiStub(False, False), "", "", 0)


def test_independent_discovery_reuses_inference_adapter(monkeypatch):
    class Backend:
        name = "fake-backend"
        host = "http://localhost:1234"

        def is_available(self):
            return True

        def list_models(self):
            return [LocalModel("coder", "coder", self.name, frozenset({ModelCapability.CODE}))]

    monkeypatch.setattr("aichestra.execution.discovery.which_binary", lambda names: None)
    facts = discover_execution_facts({}, machine=profile(), runtime_binaries={"fake-agent": ("fake",)}, inference_adapters=[Backend()])
    assert not facts.runtimes[0].available
    assert facts.providers[0].available and facts.models[0].available
    assert not resolve_targets(facts, BINDINGS)[0].available
    monkeypatch.setattr("aichestra.execution.discovery.which_binary", lambda names: "fake-binary")
    facts = discover_execution_facts({}, machine=profile(), runtime_binaries={"fake-agent": ("fake",)}, inference_adapters=[])
    assert facts.runtimes[0].available and not facts.providers


def test_ollama_binary_is_not_endpoint_reachability(monkeypatch):
    adapter = OllamaRuntime(binary="ollama")
    monkeypatch.setattr(adapter, "_probe_api", lambda: False)
    monkeypatch.setattr(adapter, "list_models", lambda: pytest.fail("unreachable endpoint must not list models"))
    facts = discover_execution_facts({}, machine=profile(), runtime_binaries={}, inference_adapters=[adapter])
    assert adapter.is_available()  # Historical API means binary OR endpoint.
    assert not facts.providers[0].available
    assert not facts.runtimes and not facts.models


def test_configuration_and_legacy_are_compatibility_inputs(monkeypatch):
    config = {"local": {"enabled": True, "model": "coder"}, "providers": {"local_worker": {"enabled": False}},
              "execution": {"model_providers": {"openrouter": {"configured": True, "models": {"remote": {"available": True, "capabilities": ["vision"]}}}}}}
    binding, = legacy_local_compatibility(config)
    assert binding.runtime == "opencode" and binding.provider == "ollama" and not binding.enabled
    facts = discover_execution_facts(config, machine=profile(), runtime_binaries={}, inference_adapters=[])
    assert facts.providers[0].configured and not facts.providers[0].available
    assert facts.models[0].capabilities.supports(Caps(frozenset({"vision"})))
    assert facts.runtimes == ()
    config["providers"] = {"local-worker": {"enabled": True}}
    assert legacy_local_compatibility(config)[0].enabled
    config["local"]["enabled"] = False
    assert not legacy_local_compatibility(config)[0].enabled


def test_duplicate_facts_fail_closed():
    facts = fixture_facts()
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_targets(replace(facts, runtimes=facts.runtimes * 2), BINDINGS)
