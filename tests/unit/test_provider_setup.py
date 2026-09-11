"""Interactive / machine-local provider registration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aichestra.config.layering import load_json, machine_local_path
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


def test_register_openai_compatible_writes_machine_local(tmp_path, monkeypatch):
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(tmp_path / "cfg"))
    # Force user-config-home layout (not Aichestra clone .local/).
    from aichestra.config import layering

    monkeypatch.setattr(
        layering,
        "machine_local_path",
        lambda repo_root=None: Path(tmp_path / "cfg" / "machine.local.json"),
    )
    register_openai_compatible_provider(
        "lmstudio",
        endpoint="http://127.0.0.1:1234/v1",
        api_key_env="LMSTUDIO_API_KEY",
        pair_opencode=True,
        repo_root=tmp_path / "cfg",
    )
    data = load_json(Path(tmp_path / "cfg" / "machine.local.json"))
    entry = data["execution"]["model_providers"]["lmstudio"]
    assert entry["api_style"] == "openai"
    assert entry["endpoint"] == "http://127.0.0.1:1234/v1"
    assert entry["api_key_env"] == "LMSTUDIO_API_KEY"
    assert {"runtime": "opencode", "provider": "lmstudio"} in data["execution"]["bindings"]
    assert data["execution"]["runtimes"]["opencode"]["enabled"] is True


def test_register_ollama_preserves_existing_bindings(tmp_path, monkeypatch):
    path = Path(tmp_path / "machine.local.json")
    path.write_text(
        json.dumps(
            {
                "execution": {
                    "bindings": [{"runtime": "opencode", "provider": "openrouter"}]
                }
            }
        ),
        encoding="utf-8",
    )
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
    register_ollama_provider(endpoint="http://127.0.0.1:11434", repo_root=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    bindings = data["execution"]["bindings"]
    assert {"runtime": "opencode", "provider": "openrouter"} in bindings
    assert {"runtime": "opencode", "provider": "ollama"} in bindings


def test_openai_compatible_probe_lists_models(monkeypatch):
    payload = json.dumps({"data": [{"id": "qwen"}, {"id": "coder"}]}).encode()

    class Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: Resp(),
    )
    probe = OpenAICompatibleProviderProbe(
        provider_id="lmstudio",
        endpoint="http://127.0.0.1:1234/v1",
    )
    assert probe.is_reachable()
    assert [m.id for m in probe.list_models()] == ["qwen", "coder"]


def test_discovery_uses_openai_api_style_without_named_factory(monkeypatch):
    class Resp:
        def read(self):
            return json.dumps({"data": [{"id": "m1"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
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
    assert facts.models[0].id == "m1"


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


def test_settings_providers_menu_registers_openai(tmp_path, monkeypatch):
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
    # 8 providers → 1 add model → 2 openai → pair yes → id/endpoint/env → back → save
    replies = iter(
        [
            "8",
            "1",
            "2",
            "1",
            "lmstudio",
            "http://127.0.0.1:1234/v1",
            "LMSTUDIO_API_KEY",
            "0",
            "9",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    interactive_settings(tmp_path)
    data = json.loads(machine.read_text(encoding="utf-8"))
    assert data["execution"]["model_providers"]["lmstudio"]["api_style"] == "openai"
