"""Interactive / machine-local Agents & Models registration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aichestra.config.layering import load_json
from aichestra.config.provider_setup import (
    discover_orca_agent_hints,
    register_agent_runtime,
    register_ollama_provider,
    register_openai_compatible_provider,
    validate_provider_id,
)
from aichestra.execution.discovery import discover_execution_facts
from aichestra.execution.model_providers import OpenAICompatibleProviderProbe


def test_validate_provider_id_rejects_bad_values():
    with pytest.raises(ValueError):
        validate_provider_id("Open Router")
    with pytest.raises(ValueError):
        validate_provider_id("1bad")
    assert validate_provider_id("lmstudio") == "lmstudio"


@pytest.mark.parametrize("register", [
    lambda root: register_ollama_provider(repo_root=root),
    lambda root: register_openai_compatible_provider(
        "lmstudio", endpoint="http://127.0.0.1:1234/v1", repo_root=root),
])
def test_model_registration_requires_orca_without_writing(tmp_path, monkeypatch, register):
    path = tmp_path / "machine.local.json"
    path.write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setattr("aichestra.config.provider_setup.machine_local_path", lambda repo_root=None: path)
    with pytest.raises(ValueError, match="Orca settings"):
        register(tmp_path)
    assert json.loads(path.read_text()) == {"existing": True}


def test_local_discovery_cannot_supply_settings_model_options():
    from aichestra.config.project_settings import _discovered_model_options
    from aichestra.execution.domain import DiscoveryFacts, Model
    facts = DiscoveryFacts(models=(Model("qwen", "ollama", available=True),))
    options = _discovered_model_options("opencode", facts, {})
    assert [payload for _, payload in options] == [{"runtime": "opencode"}]


def test_orca_catalog_options_preserve_opaque_ids_and_filter_availability():
    from dataclasses import replace
    from aichestra.config.project_settings import _discovered_model_options
    from aichestra.execution.domain import (
        AgentRuntime, DiscoveryFacts, OrcaCapabilityCatalog,
        RuntimeCapability, RuntimeModel,
    )

    facts = DiscoveryFacts(
        runtimes=(AgentRuntime("custom", available=True),),
        orca_catalog=OrcaCapabilityCatalog(runtimes=(
            RuntimeCapability("custom", available=True, models=(
                RuntimeModel("upstream/model", available=True, efforts=("high",)),
                RuntimeModel("offline"),
                RuntimeModel("blocked", available=True, provider="disabled"),
                RuntimeModel("explicit", available=True, provider="backend"),
            )),
            RuntimeCapability("other", available=True, models=(
                RuntimeModel("wrong-runtime", available=True),
            )),
            RuntimeCapability("custom", models=(
                RuntimeModel("unavailable-runtime", available=True),
            )),
        )),
    )
    config = {"execution": {"model_providers": {"disabled": {"enabled": False}}}}
    assert [p for _, p in _discovered_model_options("custom", facts, config)] == [
        {"runtime": "custom"},
        {"runtime": "custom", "model": "upstream/model"},
        {"runtime": "custom", "model": "explicit", "provider": "backend"},
    ]
    disabled = replace(facts, runtimes=(AgentRuntime("custom", enabled=False),))
    assert len(_discovered_model_options("custom", disabled, config)) == 1


def test_openai_compatible_probe_lists_models_without_auth(monkeypatch):
    payload = json.dumps({"data": [{"id": "qwen"}, {"id": "coder"}]}).encode()

    class Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        assert "Authorization" not in dict(req.headers)
        return Resp()

    monkeypatch.setattr("aichestra.execution.model_providers.local_urlopen", fake_urlopen)
    probe = OpenAICompatibleProviderProbe(
        provider_id="lmstudio",
        endpoint="http://127.0.0.1:1234/v1",
        config={"api_key_env": "SHOULD_BE_IGNORED"},
    )
    assert probe.is_reachable()
    assert [m.id for m in probe.list_models()] == ["qwen", "coder"]
    assert len(calls) == 1


def test_discovery_uses_openai_api_style_without_named_factory(monkeypatch):
    class Resp:
        def read(self):
            return json.dumps({"data": [{"id": "m1"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("aichestra.execution.model_providers.local_urlopen", lambda *a, **k: Resp())
    monkeypatch.setattr(
        "aichestra.execution.runtimes.which_binary", lambda names: None
    )
    facts = discover_execution_facts(
        {
            "execution": {
                "model_providers": {
                    "acme": {
                        "endpoint": "http://127.0.0.1:9/v1",
                        "api_style": "openai",
                    }
                }
            }
        }
    )
    by_id = {p.id: p for p in facts.providers}
    assert by_id["acme"].available
    assert [m.id for m in facts.models if m.provider == "acme"] == ["m1"]


def test_discover_orca_hints_maps_accounts(monkeypatch):
    monkeypatch.setattr(
        "aichestra.providers.orca.resolve_orca_binary", lambda: "/fake/orca"
    )

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": True,
                "result": {
                    "codex": {
                        "accounts": [],
                        "systemDefault": {"hasAuth": True, "email": "a@b.c"},
                    },
                    "claude": {"accounts": [], "systemDefault": {"hasAuth": False}},
                    "rateLimits": {
                        "gemini": {
                            "provider": "gemini",
                            "status": "unavailable",
                            "error": "disabled",
                        }
                    },
                },
            }
        )

    monkeypatch.setattr(
        "aichestra.config.provider_setup.subprocess.run",
        lambda *a, **k: Completed(),
    )
    hints = {h["runtime"]: h for h in discover_orca_agent_hints()}
    assert hints["codex"]["available"] is True
    assert hints["gemini"]["available"] is False


def test_register_agent_runtime_extends_builtin_list(tmp_path, monkeypatch):
    path = Path(tmp_path / "machine.local.json")
    monkeypatch.setattr(
        "aichestra.config.provider_setup.machine_local_path",
        lambda repo_root=None: path,
    )
    monkeypatch.setattr(
        "aichestra.config.provider_setup.save_machine_local",
        lambda data, repo_root=None: path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
        or path,
    )
    register_agent_runtime("cursor", repo_root=tmp_path)
    register_agent_runtime(
        "acme-agent", binaries=["acme-cli"], repo_root=tmp_path
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["execution"]["runtimes"]["cursor"]["enabled"] is True
    assert data["execution"]["runtimes"]["acme-agent"]["binaries"] == ["acme-cli"]


def test_settings_models_redirects_to_orca_without_writing(tmp_path, monkeypatch, capsys):
    import sys

    from aichestra.config.project_settings import (
        init_project,
        interactive_settings,
    )
    from aichestra.execution.domain import AgentRuntime, DiscoveryFacts

    init_project(tmp_path, yes=True)
    machine = tmp_path / "machine.local.json"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        "aichestra.execution.discovery.discover_execution_facts",
        lambda _: DiscoveryFacts(runtimes=(AgentRuntime("cursor", available=True),)),
    )
    monkeypatch.setattr(
        "aichestra.config.provider_setup.machine_local_path",
        lambda repo_root=None: machine,
    )
    monkeypatch.setattr(
        "aichestra.config.provider_setup.save_machine_local",
        lambda data, repo_root=None: machine.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
        or machine,
    )
    replies = iter(["8", "3", "0", "9"])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    interactive_settings(tmp_path)
    assert not machine.exists()
    output = capsys.readouterr().out
    assert "Add models and providers in Orca settings" in output
    assert "Orca model catalog unavailable" in output
    assert "Add local inference backend" not in output


@pytest.mark.parametrize("endpoint", [
    "https://api.example.com/v1", "http://192.168.1.2:1234", "file:///tmp/models",
    "http://localhost@evil.example/v1", "http://user:secret@127.0.0.1/v1",
    "http://127.0.0.1/v1?secret=x", "http://[::1", "http://127.0.0.1:bad",
])
def test_remote_or_credential_endpoints_rejected_before_write(tmp_path, endpoint):
    for register in (
        lambda: register_ollama_provider(endpoint=endpoint, repo_root=tmp_path),
        lambda: register_openai_compatible_provider("test", endpoint=endpoint, repo_root=tmp_path),
    ):
        with pytest.raises(ValueError):
            register()
    assert not list(tmp_path.iterdir())


def test_project_cannot_change_or_add_connections(tmp_path):
    from aichestra.config.layering import resolve_config
    project = tmp_path / "project" / ".aichestra"
    project.mkdir(parents=True)
    project.joinpath("project.json").write_text(json.dumps({
        "local": {"ollama_host": "http://evil.example", "endpoint": "http://evil.example"},
        "roles": {"tests": {"runtime": "opencode", "provider": "lmstudio"}},
        "execution": {"model_providers": {
            "lmstudio": {"endpoint": "http://evil.example", "api_style": "other", "enabled": False},
            "injected": {"endpoint": "http://127.0.0.1:9999", "api_style": "openai"},
        }},
    }))
    cfg = resolve_config(repo_root=tmp_path, project_root=project.parent, machine_local={
        "local": {"ollama_host": "http://127.0.0.1:11434"},
        "execution": {"model_providers": {"lmstudio": {
            "endpoint": "http://127.0.0.1:1234/v1", "api_style": "openai",
        }}},
    })
    assert cfg["local"]["ollama_host"] == "http://127.0.0.1:11434"
    assert "endpoint" not in cfg["local"]
    providers = cfg["execution"]["model_providers"]
    assert providers["lmstudio"]["endpoint"] == "http://127.0.0.1:1234/v1"
    assert providers["lmstudio"]["api_style"] == "openai"
    assert providers["lmstudio"]["enabled"] is False
    assert "endpoint" not in providers["injected"]
    assert cfg["roles"]["tests"]["provider"] == "lmstudio"


def test_http_boundary_blocks_remote_probes_and_redirects(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from aichestra.local_runtime.ollama import OllamaRuntime
    from aichestra.local_runtime.http import validate_local_endpoint

    assert validate_local_endpoint("http://localhost:1234/v1") == "http://127.0.0.1:1234/v1"
    assert validate_local_endpoint("http://[::1]:1234") == "http://[::1]:1234"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/should-not-follow")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("http_proxy", endpoint)
    monkeypatch.setenv("no_proxy", "")
    try:
        remote = OpenAICompatibleProviderProbe(provider_id="x", endpoint="http://evil.example")
        assert not remote.is_reachable()
        assert not OllamaRuntime(host="http://evil.example").is_reachable()
        assert requests == []
        local = OpenAICompatibleProviderProbe(provider_id="x", endpoint=endpoint)
        assert not local.is_reachable()
        assert local.list_models() == ()
        assert requests == ["/v1/models"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_only_exact_proven_native_launch_can_replace_local_path():
    from aichestra.execution.domain import (
        AgentRuntime, Compatibility, DiscoveryFacts, LaunchCapability, LaunchStrategy,
    )
    from aichestra.execution.targets import resolve_targets
    facts = DiscoveryFacts(runtimes=(AgentRuntime("codex", available=False),))
    binding = (Compatibility("codex", model="opaque-model"),)
    for proof, model, strategy, expected in (
        (True, "opaque-model", LaunchStrategy.ORCA_NATIVE, True),
        (False, "opaque-model", LaunchStrategy.ORCA_NATIVE, False),
        (True, "different-model", LaunchStrategy.ORCA_NATIVE, False),
        (True, "opaque-model", LaunchStrategy.ORCA_TERMINAL_BRIDGE, False),
    ):
        target = resolve_targets(facts, binding, known_launches=(
            LaunchCapability("codex", model=model, strategy=strategy, proven=proof),
        ))[0]
        assert target.available is expected
        assert target.runnable is expected



def test_remote_config_is_unavailable_and_cannot_claim_locality():
    from aichestra.execution.domain import Locality
    facts = discover_execution_facts({"execution": {"model_providers": {
        "remote": {"endpoint": "https://example.invalid/v1", "api_style": "openai", "locality": "local"},
        "ollama": {"endpoint": "http://example.invalid:11434", "locality": "local"},
    }}})
    assert len(facts.providers) == 2
    assert all(not provider.available for provider in facts.providers)
    assert all(provider.locality == Locality.REMOTE for provider in facts.providers)
