"""T169–T171: facts are not workers or proof of an Orca launch."""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from aichestra.execution.compatibility import (
    legacy_local_compatibility,
    load_compatibility_bindings,
)
from aichestra.execution.discovery import discover_execution_facts, endpoint_locality
from aichestra.execution.domain import (
    AgentRuntime,
    Compatibility,
    DiscoveryFacts,
    ExecutionCapabilities as Caps,
    ExecutionPolicy,
    ExecutionTargetKey,
    LaunchBindingKey,
    LaunchCapability,
    LaunchStrategy,
    Locality,
    MachineCapabilities,
    Model,
    ModelProvider,
)
from aichestra.execution.model_providers import (
    DiscoveredModel,
    ProviderProbeRegistry,
    register_provider_probe,
    unregister_provider_probe,
)
from aichestra.execution.runtimes import TRACKED_RUNTIME_CAPABILITY_DEFAULTS
from aichestra.execution.targets import resolve_targets
from aichestra.local_runtime.ollama import OllamaRuntime
from aichestra.machine_profiler import (
    CpuInfo,
    LocalAiStub,
    MachineProfile,
    MemoryInfo,
)


def fixture_facts():
    return DiscoveryFacts(
        runtimes=(
            AgentRuntime(
                "fake-agent",
                True,
                capabilities=Caps(frozenset({"code_edit", "shell", "code", "vision"})),
            ),
            AgentRuntime("other-agent", True),
        ),
        providers=(
            ModelProvider(
                "fake-backend",
                True,
                endpoint="http://localhost:1234",
                locality=Locality.LOCAL,
            ),
        ),
        models=(
            Model(
                "coder",
                "fake-backend",
                True,
                capabilities=Caps(frozenset({"code"})),
                min_memory_bytes=8,
            ),
        ),
        machine=MachineCapabilities("linux", 16),
    )


BINDINGS = (
    Compatibility(
        "fake-agent",
        "fake-backend",
        "coder",
        required_model=Caps(frozenset({"code"})),
    ),
    Compatibility("other-agent"),
)


def profile():
    return MachineProfile(
        "linux",
        "Linux",
        "x86_64",
        "3.11",
        False,
        CpuInfo("fixture", 4, 4),
        MemoryInfo(16, 8),
        (),
        (),
        LocalAiStub(False, False),
        "",
        "",
        0,
    )


def test_arbitrary_runtime_provider_and_exact_launch_binding():
    facts = fixture_facts()
    targets = resolve_targets(facts, BINDINGS)
    assert len(targets) == 2
    assert targets[0].locality == Locality.LOCAL
    assert targets[1].locality == Locality.CLOUD
    assert targets[0].capable and targets[0].available
    assert all(
        t.launch_strategy == LaunchStrategy.UNSUPPORTED and not t.runnable
        for t in targets
    )
    launch = LaunchCapability(
        "fake-agent",
        "fake-backend",
        "coder",
        "http://localhost:1234",
        LaunchStrategy.ORCA_NATIVE,
    )
    assert resolve_targets(facts, BINDINGS, known_launches=(launch,))[0].runnable
    wrong_endpoint = replace(launch, endpoint="http://localhost:9999")
    assert not resolve_targets(
        facts, BINDINGS, known_launches=(wrong_endpoint,)
    )[0].runnable


def test_stable_target_identity_ignores_endpoint_change():
    facts = fixture_facts()
    old = resolve_targets(facts, BINDINGS)[0]
    moved = replace(
        facts,
        providers=(
            replace(facts.providers[0], endpoint="http://127.0.0.1:9999"),
        ),
    )
    new = resolve_targets(moved, BINDINGS)[0]
    assert old.id == new.id
    assert old.key == new.key == ExecutionTargetKey(
        "fake-agent", "fake-backend", "coder"
    )
    assert old.endpoint != new.endpoint
    old_proof = LaunchCapability(
        "fake-agent",
        "fake-backend",
        "coder",
        old.endpoint,
        LaunchStrategy.ORCA_NATIVE,
    )
    assert resolve_targets(facts, BINDINGS, known_launches=(old_proof,))[0].runnable
    assert not resolve_targets(
        moved, BINDINGS, known_launches=(old_proof,)
    )[0].runnable
    assert old.launch_binding_key != new.launch_binding_key
    assert LaunchBindingKey(
        "fake-agent", "fake-backend", "coder", new.endpoint
    ).target_key().target_id() == new.id


@pytest.mark.parametrize(
    "policy",
    [
        ExecutionPolicy(disabled_runtimes=frozenset({"fake-agent"})),
        ExecutionPolicy(disabled_providers=frozenset({"fake-backend"})),
        ExecutionPolicy(disabled_models=frozenset({("fake-backend", "coder")})),
    ],
)
def test_disable_only_dependent_targets(policy):
    a, b = resolve_targets(fixture_facts(), BINDINGS, policy)
    assert not a.enabled and a.available
    assert b.enabled and b.available


def test_model_and_vision_capability_mismatch():
    facts = fixture_facts()
    a = resolve_targets(
        facts, BINDINGS, ExecutionPolicy(required=Caps(frozenset({"vision"})))
    )[0]
    assert not a.capable and "vision" not in a.capabilities.names
    embedding = replace(
        facts.models[0], capabilities=Caps(frozenset({"embedding"}))
    )
    a = resolve_targets(replace(facts, models=(embedding,)), BINDINGS)[0]
    assert not a.capable and a.available


def test_runtime_tool_capabilities_and_vision_intersection():
    facts = fixture_facts()
    text_only = resolve_targets(facts, BINDINGS)[0]
    assert "code_edit" in text_only.capabilities.names
    assert "shell" in text_only.capabilities.names
    assert "vision" not in text_only.capabilities.names
    vision_model = replace(
        facts.models[0],
        capabilities=Caps(frozenset({"code", "vision"})),
    )
    with_vision = resolve_targets(
        replace(facts, models=(vision_model,)), BINDINGS
    )[0]
    assert "vision" in with_vision.capabilities.names
    assert "shell" in with_vision.capabilities.names
    # Model-only modality cannot manufacture unsupported runtime capability.
    blind_runtime = replace(
        facts.runtimes[0],
        capabilities=Caps(frozenset({"code_edit", "shell", "code"})),
    )
    manufactured = resolve_targets(
        replace(facts, runtimes=(blind_runtime, facts.runtimes[1]), models=(vision_model,)),
        BINDINGS,
    )[0]
    assert "vision" not in manufactured.capabilities.names
    assert "code_edit" in manufactured.capabilities.names


def test_machine_project_policy_and_preference_are_separate():
    facts = fixture_facts()
    a = resolve_targets(facts, BINDINGS)[0]
    policy = ExecutionPolicy(
        allowed_localities=frozenset({Locality.CLOUD}),
        preferred_targets=frozenset({a.id}),
    )
    a, b = resolve_targets(facts, BINDINGS, policy)
    assert a.preferred and not a.allowed and a.capable
    assert b.allowed and not b.preferred
    a = resolve_targets(
        replace(facts, machine=MachineCapabilities("linux", 1)), BINDINGS
    )[0]
    assert not a.capable and a.available
    restricted = replace(
        BINDINGS[0],
        supported_os=frozenset({"windows"}),
        required_machine=Caps(frozenset({"gpu"})),
    )
    assert not resolve_targets(facts, (restricted,))[0].capable
    assert not any(
        t.allowed
        for t in resolve_targets(
            facts, BINDINGS, ExecutionPolicy(allowed_targets=frozenset())
        )
    )


def test_provider_without_compatible_runtime_never_becomes_worker():
    facts = fixture_facts()
    assert resolve_targets(replace(facts, runtimes=()), BINDINGS) == []
    assert resolve_targets(facts, (Compatibility("unknown", "fake-backend"),)) == []
    assert resolve_targets(facts, ()) == []
    assert resolve_targets(facts, (Compatibility("fake-agent", "missing"),)) == []
    assert (
        resolve_targets(
            facts, (Compatibility("fake-agent", "fake-backend", "missing"),)
        )
        == []
    )


def test_unavailable_codex_and_ollama_preserve_cursor_and_remote_opencode():
    facts = DiscoveryFacts(
        runtimes=(
            AgentRuntime("codex"),
            AgentRuntime("cursor", True),
            AgentRuntime("opencode", True),
        ),
        providers=(
            ModelProvider("ollama"),
            ModelProvider("openrouter", configured=True),
        ),
    )
    bindings = (
        Compatibility("codex"),
        Compatibility("cursor"),
        Compatibility("opencode", "ollama"),
        Compatibility("opencode", "openrouter"),
    )
    targets = resolve_targets(facts, bindings)
    assert not targets[0].available and targets[1].available
    assert targets[3].provider.configured and not targets[3].runnable
    assert resolve_targets(fixture_facts(), BINDINGS)[0].locality == Locality.LOCAL


def test_generic_binding_source_resolves_without_product_branches(monkeypatch):
    config = {
        "execution": {
            "bindings": [
                {"runtime": "custom-agent", "provider": "custom-provider"},
            ],
            "runtimes": {
                "custom-agent": {
                    "enabled": True,
                    "capabilities": ["code_edit", "shell", "code"],
                },
            },
            "model_providers": {
                "custom-provider": {
                    "endpoint": "http://127.0.0.1:9",
                    "models": {
                        "alpha": {
                            "available": True,
                            "capabilities": ["code"],
                        },
                        "beta": {
                            "available": True,
                            "capabilities": ["code", "vision"],
                        },
                    },
                },
            },
        }
    }
    bindings = load_compatibility_bindings(config)
    assert Compatibility(runtime="custom-agent", provider="custom-provider") in bindings
    source = inspect.getsource(resolve_targets)
    for banned in (
        "OpenCode",
        "Ollama",
        "Codex",
        "Cursor",
        "opencode",
        "ollama",
        "codex",
        "cursor",
    ):
        assert banned not in source

    class Probe:
        def __init__(self, *, endpoint=None, config=None):
            self._endpoint = endpoint
            self._config = config or {}

        @property
        def id(self):
            return "custom-provider"

        @property
        def endpoint(self):
            return self._endpoint

        def is_reachable(self):
            return True

        def list_models(self):
            return (
                DiscoveredModel("alpha", frozenset({"code"})),
                DiscoveredModel("beta", frozenset({"code", "vision"})),
            )

    registry = ProviderProbeRegistry()
    registry.register("custom-provider", Probe)
    monkeypatch.setattr(
        "aichestra.execution.runtimes.which_binary",
        lambda names: "/usr/bin/custom-agent",
    )
    facts = discover_execution_facts(
        config,
        machine=profile(),
        runtime_binaries={"custom-agent": ("custom-agent",)},
        provider_registry=registry,
    )
    targets = resolve_targets(facts, bindings)
    assert {t.model.id for t in targets} == {"alpha", "beta"}
    assert all(t.id == t.key.target_id() for t in targets)
    assert "local-worker" not in {t.runtime.id for t in targets}


def test_provider_probe_registry_is_production_extension_point(monkeypatch):
    class Probe:
        def __init__(self, *, endpoint=None, config=None):
            self._endpoint = endpoint or "http://127.0.0.1:5555"
            self._config = config or {}

        @property
        def id(self):
            return "acme-llm"

        @property
        def endpoint(self):
            return self._endpoint

        def is_reachable(self):
            return True

        def list_models(self):
            return (DiscoveredModel("m1", frozenset({"text"})),)

    registry = ProviderProbeRegistry()
    registry.register("acme-llm", Probe)
    monkeypatch.setattr(
        "aichestra.execution.runtimes.which_binary", lambda names: None
    )
    facts = discover_execution_facts(
        {
            "execution": {
                "model_providers": {
                    "acme-llm": {"endpoint": "http://127.0.0.1:5555"},
                    "ghost": {"configured": True},
                }
            }
        },
        machine=profile(),
        runtime_binaries={},
        provider_registry=registry,
    )
    by_id = {p.id: p for p in facts.providers}
    assert by_id["acme-llm"].available and facts.models[0].id == "m1"
    assert by_id["ghost"].configured and not by_id["ghost"].available
    # Global register/unregister API for future providers.
    register_provider_probe("temp-probe", Probe)
    try:
        assert "temp-probe" in __import__(
            "aichestra.execution.model_providers", fromlist=["PROVIDER_PROBE_REGISTRY"]
        ).PROVIDER_PROBE_REGISTRY.registered()
    finally:
        unregister_provider_probe("temp-probe")


def test_ollama_binary_is_not_endpoint_reachability(monkeypatch):
    adapter = OllamaRuntime(binary="ollama")
    monkeypatch.setattr(adapter, "_probe_api", lambda: False)
    monkeypatch.setattr(
        adapter,
        "list_models",
        lambda: pytest.fail("unreachable endpoint must not list models"),
    )

    class Wrapper:
        id = "ollama"
        endpoint = adapter.host

        def is_reachable(self):
            return adapter.is_reachable()

        def list_models(self):
            return ()

    facts = discover_execution_facts(
        {},
        machine=profile(),
        runtime_binaries={},
        provider_probes=[Wrapper()],
    )
    assert adapter.is_available()  # Historical API means binary OR endpoint.
    assert not facts.providers[0].available
    assert not facts.models


def test_configuration_and_legacy_are_compatibility_inputs():
    config = {
        "local": {"enabled": True, "model": "coder"},
        "providers": {"local_worker": {"enabled": False}},
        "execution": {
            "model_providers": {
                "openrouter": {
                    "configured": True,
                    "models": {
                        "remote": {
                            "available": True,
                            "capabilities": ["vision"],
                        }
                    },
                }
            }
        },
    }
    binding, = legacy_local_compatibility(config)
    assert binding.runtime == "opencode"
    assert binding.provider == "ollama"
    assert not binding.enabled
    facts = discover_execution_facts(
        config, machine=profile(), runtime_binaries={}, provider_probes=[]
    )
    assert facts.providers[0].configured and not facts.providers[0].available
    assert facts.models[0].capabilities.supports(Caps(frozenset({"vision"})))
    loaded = load_compatibility_bindings(config)
    assert loaded[0].runtime == "opencode" and loaded[0].provider == "ollama"
    assert "local-worker" not in {b.runtime for b in loaded}
    config["providers"] = {"local-worker": {"enabled": True}}
    assert legacy_local_compatibility(config)[0].enabled
    config["local"]["enabled"] = False
    assert not legacy_local_compatibility(config)[0].enabled


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://localhost:11434", Locality.LOCAL),
        ("http://127.0.0.1:11434", Locality.LOCAL),
        ("http://[::1]:11434", Locality.LOCAL),
        ("http://example.com:443", Locality.REMOTE),
        ("https://api.openrouter.ai/v1", Locality.REMOTE),
    ],
)
def test_endpoint_locality_inference(endpoint, expected):
    assert endpoint_locality(endpoint) == expected


def test_provider_locality_precedence(monkeypatch):
    monkeypatch.setattr(
        "aichestra.execution.runtimes.which_binary", lambda names: None
    )
    loopback = discover_execution_facts(
        {
            "execution": {
                "model_providers": {
                    "custom": {"endpoint": "http://127.0.0.1:8080"},
                }
            }
        },
        machine=profile(),
        runtime_binaries={},
        provider_probes=[],
    )
    assert loopback.providers[0].locality == Locality.LOCAL
    remote = discover_execution_facts(
        {
            "execution": {
                "model_providers": {
                    "custom": {"endpoint": "http://10.0.0.5:8080"},
                }
            }
        },
        machine=profile(),
        runtime_binaries={},
        provider_probes=[],
    )
    assert remote.providers[0].locality == Locality.REMOTE
    overridden = discover_execution_facts(
        {
            "execution": {
                "model_providers": {
                    "custom": {
                        "endpoint": "http://127.0.0.1:8080",
                        "locality": "cloud",
                    },
                }
            }
        },
        machine=profile(),
        runtime_binaries={},
        provider_probes=[],
    )
    assert overridden.providers[0].locality == Locality.CLOUD
    no_endpoint = discover_execution_facts(
        {
            "execution": {
                "model_providers": {"cloudish": {"configured": True}},
            }
        },
        machine=profile(),
        runtime_binaries={},
        provider_probes=[],
    )
    assert no_endpoint.providers[0].locality == Locality.CLOUD


def test_tracked_runtime_capabilities_are_non_empty_for_builtins(monkeypatch):
    monkeypatch.setattr(
        "aichestra.execution.runtimes.which_binary", lambda names: "/bin/fake"
    )
    facts = discover_execution_facts(
        {}, machine=profile(), provider_probes=[]
    )
    by_id = {r.id: r for r in facts.runtimes}
    for runtime_id, expected in TRACKED_RUNTIME_CAPABILITY_DEFAULTS.items():
        assert expected <= by_id[runtime_id].capabilities.names


def test_duplicate_facts_fail_closed():
    facts = fixture_facts()
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_targets(replace(facts, runtimes=facts.runtimes * 2), BINDINGS)
